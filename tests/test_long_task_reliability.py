"""Long-task recovery: provider retries, stream defects, compaction fallback, outcomes."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from pydantic import BaseModel, ConfigDict, ValidationError
from typer.testing import CliRunner

from morrow.adapters.models.openai_compatible import estimate_request_chars
from morrow.application.outcome_budget import build_bounded_task_outcome
from morrow.application.tasks import TaskOutcomeAssembler
from morrow.core.domain import (
    TASK_OUTCOME_MAX_BYTES,
    ArtifactReference,
    TaskOutcomeEvidenceKind,
    TaskOutcomeEvidenceRef,
    TaskOutcomeTrigger,
    TaskRunStatus,
    canonical_json_bytes,
)
from morrow.core.execution import EffectClass, ToolExecutionDisposition, ToolExecutionState
from morrow.core.execution_pause import LocalTurnPauseControl
from morrow.core.models import (
    AgentEvent,
    AssistantMessage,
    FinishReason,
    FunctionToolCall,
    ModelErrorCode,
    ModelEvent,
    ModelFailure,
    ModelFailureOrigin,
    ModelFinishReason,
    ModelProviderError,
    ModelRef,
    UserMessage,
    WorkspaceIdentity,
    WorkspaceResolution,
)
from morrow.core.runtime_policy import (
    PI_DEFAULT_MAX_RETRIES,
    PROVIDER_RETRY_WAIT_BUDGET_SECONDS,
    PROVIDER_RUN_RETRY_WAIT_BUDGET_SECONDS,
)
from morrow.interfaces import cli as cli_module
from morrow.interfaces.cli import app
from morrow.runtime.agent import AgentLoop, _model_attempt_seconds
from morrow.runtime.deadline import RunDeadline
from morrow.runtime.provider_retry import next_provider_retry_delay
from morrow.runtime.session import Session
from morrow.runtime.tools import ToolExecutor, ToolRegistry, make_tool
from morrow.services.workspace import DataRoot
from morrow.testing import FixedClock, FixedIdSource, make_context_builder, seed_user_turn

MODEL = ModelRef(provider_id="p", model_id="m")


def test_host_deadline_allows_active_request_past_default_600_seconds():
    deadline = RunDeadline.from_seconds(1_800, clock=lambda: 0.0)
    assert _model_attempt_seconds(deadline) == 1_770
    assert _model_attempt_seconds(None) == 600


async def _no_sleep(_delay: float) -> None:
    return None


def _failure(
    code: ModelErrorCode, *, retryable: bool, retry_after: float | None = None
) -> ModelEvent:
    return ModelEvent(
        kind="error",
        failure=ModelFailure(
            code=code,
            origin=ModelFailureOrigin.PROVIDER,
            retryable=retryable,
            message="sanitized",
            retry_after_seconds=retry_after,
        ),
    )


def _answer(text: str) -> ModelEvent:
    return ModelEvent(
        kind="completed",
        finish_reason=ModelFinishReason.STOP,
        message=AssistantMessage(content=text),
    )


class _Script:
    def __init__(self, events: list[list[ModelEvent] | BaseException]) -> None:
        self._events = events
        self.calls = 0

    async def stream(self, _model, messages, _tools=()):
        del messages
        self.calls += 1
        step = self._events[min(self.calls - 1, len(self._events) - 1)]
        if isinstance(step, BaseException):
            raise step
        for event in step:
            yield event


class _EchoArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: str


def _executor(policy, counter: list[int]):
    async def echo(arguments: _EchoArgs) -> object:
        counter.append(1)
        return {"echo": arguments.value}

    registry = ToolRegistry()
    registry.register(
        make_tool(name="echo", description="echo", arguments_model=_EchoArgs, handler=echo)
    )
    return ToolExecutor(registry.snapshot(), policy)


def _call(call_id: str) -> FunctionToolCall:
    return FunctionToolCall(
        id=call_id, name="echo", arguments=json.dumps({"value": "once"}, separators=(",", ":"))
    )


@pytest.mark.asyncio
async def test_disconnects_recover_without_duplicating_history() -> None:
    provider = _Script(
        [
            [_failure(ModelErrorCode.NETWORK, retryable=True, retry_after=10.0)],
            [_failure(ModelErrorCode.NETWORK, retryable=True)],
            [_failure(ModelErrorCode.NETWORK, retryable=True)],
            [_failure(ModelErrorCode.TIMEOUT, retryable=True)],
            [_answer("recovered")],
        ]
    )
    delays: list[float] = []

    async def sleep(delay: float) -> None:
        delays.append(delay)

    loop = AgentLoop(
        provider,
        MODEL,
        make_context_builder(compaction_enabled=False),
        retry_sleep=sleep,
        retry_unit=lambda: 1.0,
    )
    session = Session(session_id="s")

    events = [event async for event in loop.run_task(session, "continue")]

    assert provider.calls == 5
    assert delays == [10.0, 4.0, 8.0, 16.0]
    assert sum(delays) < PROVIDER_RETRY_WAIT_BUDGET_SECONDS
    assert [message.content for message in session.log.messages_view()] == ["continue", "recovered"]
    assert events[-1].payload["finish_reason"] == FinishReason.STOP.value
    assert PI_DEFAULT_MAX_RETRIES == 5


@pytest.mark.asyncio
async def test_permanent_provider_error_stops_on_the_first_attempt() -> None:
    provider = _Script([[_failure(ModelErrorCode.AUTH, retryable=False)]])
    loop = AgentLoop(provider, MODEL, make_context_builder(), retry_sleep=_no_sleep)
    session = Session(session_id="s")

    events = [event async for event in loop.run_task(session, "stop")]

    assert provider.calls == 1
    assert events[-1].payload["stop_code"] == "provider_auth"
    assert [message.role for message in session.log.messages_view()] == ["user"]


@pytest.mark.asyncio
async def test_retryable_invalid_stream_recovers_before_tool_intent() -> None:
    provider = _Script(
        [
            [_failure(ModelErrorCode.INVALID_RESPONSE, retryable=True)],
            [_answer("after the broken stream")],
        ]
    )
    loop = AgentLoop(provider, MODEL, make_context_builder(), retry_sleep=_no_sleep)
    session = Session(session_id="s")

    events = [event async for event in loop.run_task(session, "go")]

    assert provider.calls == 2
    assert events[-1].payload["finish_reason"] == FinishReason.STOP.value
    assert [message.content for message in session.log.messages_view()] == [
        "go",
        "after the broken stream",
    ]


@pytest.mark.asyncio
async def test_retry_discards_visible_text_from_a_failed_attempt() -> None:
    provider = _Script(
        [
            [
                ModelEvent(kind="text_delta", text="stale draft\n"),
                _failure(ModelErrorCode.INVALID_RESPONSE, retryable=True),
            ],
            [_answer("fresh answer")],
        ]
    )
    session = Session(session_id="s")
    events = [
        event
        async for event in AgentLoop(
            provider, MODEL, make_context_builder(), retry_sleep=_no_sleep
        ).run_task(session, "go")
    ]

    visible = [
        (event.type, event.payload.get("status"), event.payload.get("text"))
        for event in events
        if event.type == "text.delta"
        or event.type == "status.changed"
        and event.payload.get("status") in {"response_reset", "retrying"}
    ]
    assert visible == [
        ("text.delta", None, "stale draft\n"),
        ("status.changed", "response_reset", None),
        ("status.changed", "retrying", None),
        ("text.delta", None, "fresh answer"),
    ]
    assert [message.content for message in session.log.messages_view()] == ["go", "fresh answer"]


@pytest.mark.asyncio
async def test_nonretryable_invalid_response_and_post_tool_defect_recovers_once() -> None:
    rejected = _Script([[_failure(ModelErrorCode.INVALID_RESPONSE, retryable=False)]])
    rejected_events = [
        event
        async for event in AgentLoop(
            rejected, MODEL, make_context_builder(), retry_sleep=_no_sleep
        ).run_task(Session(session_id="rejected"), "go")
    ]
    assert rejected.calls == 1
    assert rejected_events[-1].payload["stop_code"] == "invalid_response"

    counter: list[int] = []
    builder = make_context_builder()
    provider = _Script(
        [
            [
                ModelEvent(
                    kind="completed",
                    finish_reason=ModelFinishReason.TOOL_CALLS,
                    message=AssistantMessage(tool_calls=(_call("c1"),)),
                )
            ],
            [_failure(ModelErrorCode.INVALID_RESPONSE, retryable=True)],
            [_answer("recovered after tool")],
        ]
    )
    session = Session(session_id="tools")
    events = [
        event
        async for event in AgentLoop(
            provider,
            MODEL,
            builder,
            tool_executor=_executor(builder.run_policy, counter),
            retry_sleep=_no_sleep,
        ).run_task(session, "use the tool")
    ]

    assert provider.calls == 3
    assert counter == [1]
    assert events[-1].payload["finish_reason"] == FinishReason.STOP.value
    assert [message.role for message in session.log.messages_view()].count("tool") == 1
    assert session.log.messages_view()[-1].content == "recovered after tool"


@pytest.mark.asyncio
async def test_successful_requests_reset_the_fault_window_but_keep_total_wait() -> None:
    counter: list[int] = []
    builder = make_context_builder()
    tool_reply = ModelEvent(
        kind="completed",
        finish_reason=ModelFinishReason.TOOL_CALLS,
        message=AssistantMessage(tool_calls=(_call("c1"),)),
    )
    provider = _Script(
        [
            [_failure(ModelErrorCode.RATE_LIMIT, retryable=True, retry_after=60)],
            [tool_reply],
            [_failure(ModelErrorCode.RATE_LIMIT, retryable=True, retry_after=60)],
            [_answer("done")],
        ]
    )
    delays: list[float] = []

    async def sleep(delay: float) -> None:
        delays.append(delay)

    events = [
        event
        async for event in AgentLoop(
            provider,
            MODEL,
            builder,
            tool_executor=_executor(builder.run_policy, counter),
            retry_sleep=sleep,
        ).run_task(Session(session_id="two-faults"), "go")
    ]

    assert delays == [60, 60]
    assert counter == [1]
    assert events[-1].payload["finish_reason"] == FinishReason.STOP.value
    assert (
        next_provider_retry_delay(
            policy=builder.run_policy,
            retry_index=1,
            retry_after_seconds=60,
            waited_seconds=0,
            total_waited_seconds=PROVIDER_RUN_RETRY_WAIT_BUDGET_SECONDS - 30,
            unit=1,
        )
        is None
    )


@pytest.mark.asyncio
async def test_retry_budget_stops_before_the_next_wait_and_can_be_cancelled() -> None:
    policy = make_context_builder().run_policy
    assert (
        next_provider_retry_delay(
            policy=policy,
            retry_index=3,
            retry_after_seconds=50,
            waited_seconds=100,
            unit=1,
        )
        is None
    )
    provider = _Script(
        [[_failure(ModelErrorCode.RATE_LIMIT, retryable=True, retry_after=50.0)]] * 6
    )
    delays: list[float] = []

    async def sleep(delay: float) -> None:
        delays.append(delay)

    loop = AgentLoop(
        provider,
        MODEL,
        make_context_builder(),
        retry_sleep=sleep,
        retry_unit=lambda: 1.0,
        pause_control=LocalTurnPauseControl(),
    )
    events = [event async for event in loop.run_task(Session(session_id="budget"), "go")]

    assert delays == [50.0, 50.0]
    assert sum(delays) <= PROVIDER_RETRY_WAIT_BUDGET_SECONDS
    assert provider.calls == 3
    assert events[-1].payload["finish_reason"] == FinishReason.INTERRUPTED.value
    assert events[-1].payload["stop_code"] == "provider_rate_limit"

    cancelled = _Script([[_failure(ModelErrorCode.NETWORK, retryable=True)]])
    started = asyncio.Event()

    async def blocking_sleep(_delay: float) -> None:
        started.set()
        await asyncio.Future()

    session = Session(session_id="cancel")
    task = asyncio.create_task(
        _collect(
            AgentLoop(
                cancelled, MODEL, make_context_builder(), retry_sleep=blocking_sleep
            ).run_task(session, "go")
        )
    )
    await started.wait()
    task.cancel()
    cancelled_events = await task
    assert cancelled.calls == 1
    assert cancelled_events[-1].payload["finish_reason"] == FinishReason.CANCELLED.value


@pytest.mark.asyncio
async def test_fault_window_counts_elapsed_time_as_well_as_sleep() -> None:
    provider = _Script([[_failure(ModelErrorCode.RATE_LIMIT, retryable=True, retry_after=50)]] * 3)
    clock = FixedClock()
    delays: list[float] = []

    async def sleep(delay: float) -> None:
        delays.append(delay)
        clock.value += timedelta(seconds=100)

    events = [
        event
        async for event in AgentLoop(
            provider,
            MODEL,
            make_context_builder(),
            clock=clock,
            retry_sleep=sleep,
            pause_control=LocalTurnPauseControl(),
        ).run_task(Session(session_id="elapsed-window"), "go")
    ]

    assert provider.calls == 2
    assert delays == [50]
    assert events[-1].payload["stop_code"] == "provider_rate_limit"


@pytest.mark.asyncio
async def test_jitter_lowers_the_exponential_component_without_ignoring_retry_after() -> None:
    policy = make_context_builder(retry_base_delay_seconds=2.0).run_policy
    assert (
        next_provider_retry_delay(
            policy=policy,
            retry_index=1,
            retry_after_seconds=None,
            waited_seconds=0,
            unit=0,
        )
        == 1.0
    )
    assert (
        next_provider_retry_delay(
            policy=policy,
            retry_index=1,
            retry_after_seconds=10,
            waited_seconds=0,
            unit=0,
        )
        == 10.0
    )


@pytest.mark.asyncio
async def test_compaction_failure_drops_old_turns_without_changing_the_log() -> None:
    class SummaryFails:
        def __init__(self) -> None:
            self.stream_calls: list[list] = []
            self.complete_calls = 0

        async def stream(self, _model, messages, _tools=()):
            self.stream_calls.append(list(messages))
            yield _answer("continued")

        async def complete(self, _model, _messages):
            self.complete_calls += 1
            raise ModelProviderError(
                ModelFailure(
                    code=ModelErrorCode.AUTH,
                    origin=ModelFailureOrigin.PROVIDER,
                    retryable=False,
                    message="summary unavailable",
                )
            )

    session = Session(session_id="s")
    seed_user_turn(session, "ALPHA_OLD " * 40, assistant="ALPHA_ANSWER " * 40)
    seed_user_turn(session, "BETA_OLD " * 40, assistant="BETA_ANSWER " * 40)
    probe = make_context_builder()
    mandatory = (*probe._system_messages(session), UserMessage(content="CURRENT_REQUEST"))
    before = session.log.snapshot()
    # Leave room for the bounded omission notice that the model must see.
    limit = estimate_request_chars(mandatory, ()) + 600
    provider = SummaryFails()
    events = [
        event
        async for event in AgentLoop(
            provider,
            MODEL,
            make_context_builder(limit, keep_recent_tokens=1),
            retry_sleep=_no_sleep,
        ).run_task(session, "CURRENT_REQUEST")
    ]

    assert provider.complete_calls == 1
    assert provider.stream_calls
    sent = " ".join(getattr(message, "content", "") or "" for message in provider.stream_calls[0])
    assert "ALPHA_OLD" not in sent
    assert "BETA_OLD" not in sent
    assert "CURRENT_REQUEST" in sent
    assert "上下文降级" in sent
    assert events[-1].payload["finish_reason"] == FinishReason.STOP.value
    assert any(
        event.payload.get("status") == "compaction_failure"
        and event.payload["cause_phase"] == "summary_request"
        and event.payload["cause_code"] == "auth"
        and event.payload["request_output_tokens"] == 4_096
        for event in events
    )
    assert any(
        event.payload.get("status") == "context_degraded"
        and event.payload["dropped_turn_count"] >= 1
        for event in events
    )
    assert [
        record.sequence for record in session.log.snapshot().records[: len(before.records)]
    ] == [record.sequence for record in before.records]
    retained = [
        record.message.content
        for record in session.log.snapshot().records
        if hasattr(record, "message") and record.message.content
    ]
    assert any("ALPHA_OLD" in text for text in retained)


@pytest.mark.asyncio
async def test_failed_compaction_install_is_not_treated_as_summary_fallback(monkeypatch) -> None:
    class SummarySucceeds:
        async def complete(self, _model, _messages):
            return json.dumps(
                {
                    "goal": "continue",
                    "constraints_preferences": [],
                    "progress_done": [],
                    "progress_in_progress": [],
                    "progress_blocked": [],
                    "key_decisions": [],
                    "next_steps": ["continue"],
                    "critical_context": [],
                    "files_read": [],
                    "files_modified": [],
                }
            )

    session = Session(session_id="s")
    seed_user_turn(session, "old request", assistant="old answer")
    seed_user_turn(session, "another request", assistant="another answer")
    builder = make_context_builder(keep_recent_tokens=1)

    def fail_install(*_args, **_kwargs):
        raise RuntimeError("compaction persistence failed")

    monkeypatch.setattr(builder, "apply_compaction", fail_install)
    loop = AgentLoop(SummarySucceeds(), MODEL, builder, retry_sleep=_no_sleep)

    with pytest.raises(RuntimeError, match="compaction persistence failed"):
        await loop._compact_context(session, loop.runner.provider, MODEL, builder)
    assert session.compaction_entries == ()
    assert session.compaction_in_progress is False


@pytest.mark.asyncio
async def test_unfittable_input_stays_a_recoverable_interrupt_for_a_durable_host() -> None:
    provider = _Script([[_answer("unused")]])
    durable = AgentLoop(
        provider,
        MODEL,
        make_context_builder(80),
        pause_control=LocalTurnPauseControl(),
        retry_sleep=_no_sleep,
    )
    durable_events = [
        event async for event in durable.run_task(Session(session_id="durable"), "x" * 500)
    ]
    assert provider.calls == 0
    assert durable_events[-1].payload["finish_reason"] == FinishReason.INTERRUPTED.value
    assert durable_events[-1].payload["stop_code"] == "context_budget"

    plain = _Script([[_answer("unused")]])
    plain_events = [
        event
        async for event in AgentLoop(
            plain, MODEL, make_context_builder(80), retry_sleep=_no_sleep
        ).run_task(Session(session_id="plain"), "x" * 500)
    ]
    assert plain.calls == 0
    assert plain_events[-1].payload["finish_reason"] == FinishReason.ERROR.value
    assert plain_events[-1].payload["stop_code"] == "context_budget"


def test_outcome_keeps_priority_refs_and_records_omissions_for_tasks_and_workflows() -> None:
    executions = tuple(
        SimpleNamespace(
            facts=SimpleNamespace(files=(SimpleNamespace(relative_path=f"src/f{index}.py"),)),
            tool_name="write",
            disposition=ToolExecutionDisposition.SUCCEEDED,
            state=ToolExecutionState.CLOSED,
            intent=SimpleNamespace(effect_class=EffectClass.PURE),
            tool_execution_id=f"tex_{index}",
            artifact_refs=(ArtifactReference(artifact_id=f"art_e{index}", role="tool_output"),),
        )
        for index in range(70)
    )
    journal = SimpleNamespace(
        list_task_turns=lambda _ws, _task: (SimpleNamespace(turn_id="turn_goal"),),
        list_task_executions=lambda _ws, _task: executions,
        list_task_transitions=lambda _ws, _task: (),
        list_task_outcomes=lambda _ws, _task: (),
    )
    task = SimpleNamespace(
        task_run_id="task_1",
        session_id="ses_1",
        status=TaskRunStatus.READY_FOR_ACCEPTANCE,
    )
    outcome = TaskOutcomeAssembler(
        journal, workspace_id="ws_1", id_source=FixedIdSource(), clock=FixedClock().now
    ).build(
        task,
        trigger=TaskOutcomeTrigger.SNAPSHOT,
        artifact_refs=(ArtifactReference(artifact_id="art_keep", role="final_output"),),
    )

    assert len(journal.list_task_executions("ws_1", "task_1")) == 70
    assert len(outcome.artifact_refs) == 64
    assert outcome.artifact_refs[0].artifact_id == "art_keep"
    kept = {ref.artifact_id for ref in outcome.artifact_refs}
    assert "art_e69" in kept
    assert "art_e0" not in kept
    assert "omitted_artifact_refs=7" in outcome.completion_basis
    assert len(canonical_json_bytes(outcome.model_dump(mode="json"))) <= TASK_OUTCOME_MAX_BYTES

    workflow = build_bounded_task_outcome(
        {
            "outcome_id": "out_flow",
            "workspace_id": "ws_1",
            "session_id": "ses_1",
            "task_run_id": "task_1",
            "version": 1,
            "trigger": TaskOutcomeTrigger.SNAPSHOT,
            "task_status": TaskRunStatus.READY_FOR_ACCEPTANCE,
            "summary": "workflow " * 400,
            "goal_reference": TaskOutcomeEvidenceRef(
                kind=TaskOutcomeEvidenceKind.ARTIFACT,
                reference_id="art_input",
                role="workflow_input",
            ),
            "changed_paths": tuple(f"src/p{index}.py" for index in range(200)) + ("../secret",),
            "validation_facts": tuple(f"check-{index}-" + ("x" * 400) for index in range(80)),
            "side_effects": (),
            "unresolved_items": (),
            "completion_basis": (
                "trigger=snapshot",
                "task_status=ready_for_acceptance",
                "tool_execution_count=70",
            ),
            "feedback": tuple("note " * 80 for _index in range(70)),
            "evidence_refs": (
                TaskOutcomeEvidenceRef(
                    kind=TaskOutcomeEvidenceKind.WORKFLOW_RUN,
                    reference_id="wrun_root",
                    role="workflow_result_snapshot",
                ),
                *(
                    TaskOutcomeEvidenceRef(
                        kind=TaskOutcomeEvidenceKind.TOOL_EXECUTION,
                        reference_id=f"tex_{index}",
                        role="tool_execution",
                    )
                    for index in range(80)
                ),
            ),
            "artifact_refs": tuple(
                ArtifactReference(artifact_id=f"art_w{index}", role="workflow_result")
                for index in range(70)
            ),
            "created_at": datetime(2026, 1, 1, tzinfo=UTC),
        },
        workflow=True,
    )

    assert len(workflow.artifact_refs) <= 64
    assert workflow.artifact_refs[0].role == "workflow_result"
    assert "workflow_result_snapshot" in {ref.role for ref in workflow.evidence_refs}
    assert any(line.startswith("omitted_artifact_refs=") for line in workflow.completion_basis)
    assert any(line.startswith("omitted_changed_paths=") for line in workflow.completion_basis)
    assert "../secret" not in workflow.changed_paths
    assert len(canonical_json_bytes(workflow.model_dump(mode="json"))) <= TASK_OUTCOME_MAX_BYTES
    with pytest.raises((ValidationError, ValueError)):
        build_bounded_task_outcome({"summary": "missing identity"})


def test_headless_failure_reports_this_run_without_traceback_or_the_previous_run(
    monkeypatch, tmp_path
):
    workspace = tmp_path / "project"
    workspace.mkdir()
    state_root = tmp_path / "state"
    seen: list[str] = []

    class ExplodingIfPrevious:
        def get_agent_run_observation(self, agent_run_id):
            seen.append(agent_run_id)
            if mode["value"] == "observation-unavailable":
                raise RuntimeError("observation unavailable")
            if agent_run_id == "arun_old":
                return SimpleNamespace(
                    model_dump=lambda mode="json": {
                        "agent_run_id": "arun_old",
                        "session_id": "ses_old",
                        "task_run_id": "task_old",
                        "turn_id": "turn_old",
                        "requests": [{"state": "completed"}],
                        "terminal_metrics": {"finish_reason": "stop"},
                    }
                )
            if mode["value"] in {"after-terminal", "after-terminal-cancel"}:
                return SimpleNamespace(
                    model_dump=lambda mode="json": {
                        "agent_run_id": "arun_new",
                        "session_id": "ses_new",
                        "task_run_id": "task_new",
                        "turn_id": "turn_new",
                        "requests": [{"state": "completed"}],
                        "terminal_metrics": {"finish_reason": "stop", "model_attempts": 1},
                    }
                )
            return SimpleNamespace(
                model_dump=lambda mode="json": {
                    "agent_run_id": "arun_new",
                    "session_id": "ses_new",
                    "task_run_id": "task_new",
                    "turn_id": "turn_new",
                    "requests": [{"state": "failed"}, {"state": "failed"}],
                    "terminal_metrics": {
                        "finish_reason": "interrupted",
                        "stop_code": "provider_network",
                        "model_attempts": 2,
                    },
                }
            )

    class Orchestrator:
        def __init__(self, mode: str) -> None:
            self.mode = mode

        async def stream(self, prompt):
            del prompt
            if self.mode == "before-start":
                raise RuntimeError("sk-secret OpenAI client exploded")
            yield AgentEvent(
                type="turn.started",
                event_id="evt_1",
                session_id="ses_new",
                turn_id="turn_new",
                sequence=1,
            )
            if self.mode == "cancel":
                raise asyncio.CancelledError
            if self.mode in {"after-terminal", "after-terminal-cancel"}:
                yield AgentEvent(
                    type="turn.completed",
                    event_id="evt_2",
                    session_id="ses_new",
                    turn_id="turn_new",
                    sequence=2,
                    payload={"finish_reason": "stop", "text": "done"},
                )
            if self.mode == "after-terminal-cancel":
                raise asyncio.CancelledError
            raise RuntimeError("credential=sk-secret provider sdk")

    mode = {"value": "failed"}

    def build_session(**_kwargs):
        return SimpleNamespace(
            orchestrator=Orchestrator(mode["value"]),
            session=SimpleNamespace(
                session_id="ses_old",
                committer=SimpleNamespace(
                    current_task_run_id="task_old", current_agent_run_id="arun_old"
                ),
            ),
            persistence=SimpleNamespace(
                current_task_run_id="task_old",
                current_agent_run_id=(
                    "arun_new"
                    if mode["value"]
                    in {
                        "confirmed",
                        "after-terminal",
                        "after-terminal-cancel",
                        "observation-unavailable",
                    }
                    else "arun_old"
                ),
            ),
            api=ExplodingIfPrevious(),
        )

    monkeypatch.setattr(
        cli_module,
        "build_application",
        lambda **_: SimpleNamespace(
            data_root=DataRoot(state_root),
            workspace_service=SimpleNamespace(
                resolve=lambda path: WorkspaceResolution(
                    status="existing",
                    identity=WorkspaceIdentity(
                        workspace_id="ws_1", path=str(path), display_name="project"
                    ),
                )
            ),
        ),
    )
    monkeypatch.setattr(cli_module, "build_session_application", build_session)

    failed = CliRunner().invoke(
        app,
        ["run", "--workspace", str(workspace), "--prompt", "go", "--state-root", str(state_root)],
    )
    assert failed.exit_code == 2
    assert "Traceback" not in failed.output
    assert "sk-secret" not in failed.output
    assert "OpenAI" not in failed.output
    record = json.loads(failed.output.splitlines()[-1])
    assert record["kind"] == "run.completed"
    assert record["agent_run_id"] is None
    assert record["turn_id"] == "turn_new"
    assert record["request_count"] is None
    assert record["stop_reason"] == "error"
    assert "arun_old" not in failed.output

    mode["value"] = "before-start"
    seen.clear()
    early = CliRunner().invoke(
        app,
        ["run", "--workspace", str(workspace), "--prompt", "go", "--state-root", str(state_root)],
    )
    early_record = json.loads(early.output.splitlines()[-1])
    assert early_record["agent_run_id"] is None
    assert early_record["turn_id"] is None
    assert early_record["request_count"] is None
    assert seen == []
    assert "sk-secret" not in early.output

    mode["value"] = "confirmed"
    confirmed = CliRunner().invoke(
        app,
        ["run", "--workspace", str(workspace), "--prompt", "go", "--state-root", str(state_root)],
    )
    confirmed_record = json.loads(confirmed.output.splitlines()[-1])
    assert confirmed_record["agent_run_id"] == "arun_new"
    assert confirmed_record["request_count"] == 2
    assert confirmed_record["stop_reason"] == "provider_network"
    assert "sk-secret" not in confirmed.output

    mode["value"] = "observation-unavailable"
    unavailable = CliRunner().invoke(
        app,
        ["run", "--workspace", str(workspace), "--prompt", "go", "--state-root", str(state_root)],
    )
    unavailable_record = json.loads(unavailable.output.splitlines()[-1])
    assert unavailable.exit_code == 2
    assert unavailable_record["turn_id"] == "turn_new"
    assert unavailable_record["agent_run_id"] is None
    assert unavailable_record["task_run_id"] is None
    assert unavailable_record["request_count"] is None

    mode["value"] = "after-terminal"
    after_terminal = CliRunner().invoke(
        app,
        ["run", "--workspace", str(workspace), "--prompt", "go", "--state-root", str(state_root)],
    )
    after_terminal_record = json.loads(after_terminal.output.splitlines()[-1])
    assert after_terminal.exit_code == 2
    assert after_terminal_record["agent_run_id"] == "arun_new"
    assert after_terminal_record["request_count"] == 1
    assert after_terminal_record["metrics"]["finish_reason"] == "stop"
    assert after_terminal_record["stop_reason"] == "post_turn_error"
    assert "sk-secret" not in after_terminal.output

    mode["value"] = "after-terminal-cancel"
    after_terminal_cancel = CliRunner().invoke(
        app,
        ["run", "--workspace", str(workspace), "--prompt", "go", "--state-root", str(state_root)],
    )
    cancelled_after_terminal_record = json.loads(after_terminal_cancel.output.splitlines()[-1])
    assert after_terminal_cancel.exit_code == 2
    assert cancelled_after_terminal_record["stop_reason"] == "post_turn_cancelled"

    mode["value"] = "cancel"
    cancelled = CliRunner().invoke(
        app,
        ["run", "--workspace", str(workspace), "--prompt", "go", "--state-root", str(state_root)],
    )
    cancelled_record = json.loads(cancelled.output.splitlines()[-1])
    assert cancelled_record["stop_reason"] == "cancelled"
    assert cancelled_record["agent_run_id"] is None
    assert "Traceback" not in cancelled.output


async def _collect(iterator):
    return [event async for event in iterator]
