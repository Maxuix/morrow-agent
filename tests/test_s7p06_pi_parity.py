"""Offline black-box evidence for the S7P-06 Pi-parity runtime contract."""

from __future__ import annotations

import json

import pytest
from pydantic import BaseModel, ConfigDict, ValidationError

from morrow.adapters.state.artifacts import FilesystemArtifactStore
from morrow.adapters.state.journal import SqliteOperationalJournal
from morrow.adapters.state.operational import OperationalStore
from morrow.application.artifacts import ArtifactService
from morrow.application.compaction_persistence import (
    checkpoint_for_compaction,
    entry_from_compaction_checkpoint,
)
from morrow.application.context import ContextBuilder
from morrow.application.local_tools import (
    READ_PROVIDER_SCHEMA,
    ReadArguments,
    make_read_artifact_tool,
)
from morrow.core.capabilities import ToolRunContext
from morrow.core.compaction import (
    CompactionEntry,
    CompactionSummary,
    TokenAccounting,
    TokenAccountingBasis,
)
from morrow.core.domain import (
    ArtifactReference,
    DurableConversationRecord,
    DurableSession,
    sha256_digest,
)
from morrow.core.models import (
    AgentStopCode,
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
    ToolMessage,
    UserMessage,
)
from morrow.core.runtime_policy import (
    PI_DEFAULT_MAX_RETRIES,
)
from morrow.runtime.agent import AgentLoop
from morrow.runtime.policy import LongHorizonPolicySettings, load_runtime_policy
from morrow.runtime.session import Session
from morrow.runtime.tool_cycle import ToolCycleExecutor
from morrow.runtime.tools import (
    ToolErrorCode,
    ToolExecutionOutcome,
    ToolExecutor,
    ToolRegistry,
    make_tool,
)
from morrow.runtime.truncation import truncate_head, truncate_line, truncate_tail
from morrow.testing import FixedIdSource, seed_user_turn

MODEL = ModelRef(provider_id="test", model_id="test")


def _v2_policy(*, context_window_tokens: int | None = 10_000_000, **settings):
    selected = LongHorizonPolicySettings(**settings)
    return load_runtime_policy().agent_run.resolve(
        MODEL,
        tool_protocol="openai_function",
        multiple_tool_calls=True,
        context_window_tokens=context_window_tokens,
        settings=selected,
    )


def _v2_context(*, context_window_tokens: int = 10_000_000, **settings):
    from morrow.adapters.models.openai_compatible import estimate_request_chars

    return ContextBuilder(
        run_policy=_v2_policy(context_window_tokens=context_window_tokens, **settings),
        estimate_request_chars=estimate_request_chars,
    )


class EchoArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: str


def _echo_executor(policy):
    async def handler(arguments: EchoArguments) -> object:
        return {"echo": arguments.value}

    registry = ToolRegistry()
    registry.register(
        make_tool(name="echo", description="echo", arguments_model=EchoArguments, handler=handler)
    )
    return ToolExecutor(registry.snapshot(), policy)


def _tool_call(call_id: str, value: str = "same") -> FunctionToolCall:
    return FunctionToolCall(
        id=call_id,
        name="echo",
        arguments=json.dumps({"value": value}, separators=(",", ":")),
    )


class ManyToolProvider:
    def __init__(self, tool_cycles: int) -> None:
        self.tool_cycles = tool_cycles
        self.stream_calls = 0

    async def stream(self, model, messages, tools=()):
        del model, messages, tools
        self.stream_calls += 1
        if self.stream_calls <= self.tool_cycles:
            yield ModelEvent(
                kind="completed",
                finish_reason=ModelFinishReason.TOOL_CALLS,
                message=AssistantMessage(tool_calls=(_tool_call(f"call-{self.stream_calls}"),)),
            )
        else:
            yield ModelEvent(
                kind="completed",
                finish_reason=ModelFinishReason.STOP,
                message=AssistantMessage(content="done"),
            )


@pytest.mark.asyncio
async def test_v2_loop_can_repeat_and_exceed_retired_caps_before_normal_stop():
    policy = _v2_policy(compaction_enabled=False)
    provider = ManyToolProvider(tool_cycles=513)
    session = Session(session_id="s")
    loop = AgentLoop(
        provider,
        MODEL,
        ContextBuilder(
            run_policy=policy,
            estimate_request_chars=lambda messages, tools: sum(
                len(message.content or "") for message in messages
            ),
        ),
        id_source=FixedIdSource(),
        tool_executor=_echo_executor(policy),
    )

    events = [event async for event in loop.run_task(session, "keep going")]

    assert events[-1].payload["finish_reason"] == FinishReason.STOP.value
    assert provider.stream_calls == 514
    assert (
        len(
            [message for message in session.log.messages_view() if isinstance(message, ToolMessage)]
        )
        == 513
    )
    assert not any(
        event.type == "error"
        and event.payload.get("stop_code")
        in {"model_call_limit", "tool_call_limit", "run_timeout", "loop_detected"}
        for event in events
    )


@pytest.mark.asyncio
async def test_v2_host_stop_hook_is_optional_and_keeps_cancelled_history_legal():
    provider = ManyToolProvider(tool_cycles=4)
    policy = _v2_policy(compaction_enabled=False)
    seen = []

    def stop_after_cycle(session, message):
        seen.append((session.session_id, bool(message.tool_calls)))
        return True

    session = Session(session_id="s")
    loop = AgentLoop(
        provider,
        MODEL,
        _v2_context(compaction_enabled=False),
        tool_executor=_echo_executor(policy),
        should_stop_after_turn=stop_after_cycle,
    )

    events = [event async for event in loop.run_task(session, "stop after this cycle")]

    assert provider.stream_calls == 1
    assert seen == [("s", True)]
    assert events[-1].payload["finish_reason"] == FinishReason.CANCELLED.value
    assert any(
        event.type == "status.changed" and event.payload == {"status": "stopped", "source": "host"}
        for event in events
    )
    assert session.log.snapshot().records[-1].finish_reason is FinishReason.CANCELLED


class RetryProvider:
    def __init__(self) -> None:
        self.stream_calls = 0
        self.retry_after = (10.0, None, 60.0)

    async def stream(self, model, messages, tools=()):
        del model, messages, tools
        self.stream_calls += 1
        if self.stream_calls <= 3:
            raise ModelProviderError(
                ModelFailure(
                    code=ModelErrorCode.RATE_LIMIT,
                    origin=ModelFailureOrigin.PROVIDER,
                    retryable=True,
                    message="provider-internal-detail",
                    retry_after_seconds=self.retry_after[self.stream_calls - 1],
                )
            )
        yield ModelEvent(
            kind="completed",
            finish_reason=ModelFinishReason.STOP,
            message=AssistantMessage(content="recovered"),
        )


@pytest.mark.asyncio
async def test_v2_retry_uses_provider_delay_cap_and_does_not_duplicate_history():
    provider = RetryProvider()
    delays: list[float] = []
    policy = _v2_policy(compaction_enabled=False)
    session = Session(session_id="s")

    async def retry_sleep(delay: float) -> None:
        delays.append(delay)

    loop = AgentLoop(
        provider,
        MODEL,
        _v2_context(compaction_enabled=False),
        retry_sleep=retry_sleep,
    )

    events = [event async for event in loop.run_task(session, "retry this")]

    assert provider.stream_calls == 4
    assert delays == [10.0, 4.0, 60.0]
    assert [message.content for message in session.log.messages_view()] == [
        "retry this",
        "recovered",
    ]
    assert [
        event.payload["retry_delay_seconds"]
        for event in events
        if event.type == "status.changed" and event.payload.get("status") == "retrying"
    ] == delays
    assert events[-1].payload["finish_reason"] == FinishReason.STOP.value
    assert policy.max_retries == PI_DEFAULT_MAX_RETRIES


class OverflowProvider:
    def __init__(self) -> None:
        self.stream_calls = 0
        self.complete_calls = 0

    async def stream(self, model, messages, tools=()):
        del model, messages, tools
        self.stream_calls += 1
        if self.stream_calls == 1:
            raise ModelProviderError(
                ModelFailure(
                    code=ModelErrorCode.CONTEXT_OVERFLOW,
                    origin=ModelFailureOrigin.PROVIDER,
                    message="context detail",
                )
            )
        yield ModelEvent(
            kind="completed",
            finish_reason=ModelFinishReason.STOP,
            message=AssistantMessage(content="after compaction"),
        )

    async def complete(self, model, messages):
        del model, messages
        self.complete_calls += 1
        return json.dumps(
            {
                "goal": "continue the task",
                "constraints_preferences": ["preserve history"],
                "progress_done": ["finished the old turn"],
                "progress_in_progress": [],
                "progress_blocked": [],
                "key_decisions": ["use the recent tail"],
                "next_steps": ["continue"],
                "critical_context": ["overflow recovery was used"],
                "files_read": [],
                "files_modified": [],
            }
        )


class CompactionRetryProvider(OverflowProvider):
    def __init__(
        self,
        code: ModelErrorCode = ModelErrorCode.RATE_LIMIT,
        *,
        retryable: bool = False,
    ) -> None:
        super().__init__()
        self.code = code
        self.retryable = retryable

    async def complete(self, model, messages):
        del model, messages
        self.complete_calls += 1
        if self.complete_calls <= 2:
            raise ModelProviderError(
                ModelFailure(
                    code=self.code,
                    origin=ModelFailureOrigin.PROVIDER,
                    retryable=self.retryable,
                    message="summary retry",
                )
            )
        return json.dumps(
            {
                "goal": "continue the task",
                "constraints_preferences": ["preserve history"],
                "progress_done": ["finished the old turn"],
                "progress_in_progress": [],
                "progress_blocked": [],
                "key_decisions": ["use the recent tail"],
                "next_steps": ["continue"],
                "critical_context": ["summary retry succeeded"],
                "files_read": [],
                "files_modified": [],
            }
        )


class ProactiveCompactionProvider(OverflowProvider):
    async def stream(self, model, messages, tools=()):
        del model, messages, tools
        self.stream_calls += 1
        yield ModelEvent(
            kind="completed",
            finish_reason=ModelFinishReason.STOP,
            message=AssistantMessage(content="after proactive compaction"),
        )


@pytest.mark.asyncio
async def test_manual_idle_compaction_uses_the_same_durable_summary_path():
    provider = ProactiveCompactionProvider()
    session = Session(session_id="s")
    seed_user_turn(session, "old request", assistant="old answer")
    seed_user_turn(session, "another old request", assistant="another answer")
    loop = AgentLoop(provider, MODEL, _v2_context(keep_recent_tokens=1))

    compacted = await loop.compact_idle(session, instructions="保留接口决策")

    assert compacted is True
    assert provider.complete_calls == 1
    assert len(session.compaction_entries) == 1
    assert session.compaction_entries[0].instructions == "保留接口决策"
    assert session.compaction_summary is not None


@pytest.mark.asyncio
async def test_context_overflow_has_one_compaction_recovery_path():
    provider = OverflowProvider()
    policy = _v2_policy(context_window_tokens=1_024, reserve_tokens=64, keep_recent_tokens=40)
    session = Session(session_id="s")
    seed_user_turn(session, "old request", assistant="old answer")
    seed_user_turn(session, "another old request", assistant="another answer")
    loop = AgentLoop(
        provider,
        MODEL,
        ContextBuilder(
            run_policy=policy,
            estimate_request_chars=lambda messages, tools: 1,
        ),
    )

    events = [event async for event in loop.run_task(session, "current request")]

    assert provider.stream_calls == 2
    assert provider.complete_calls == 1
    assert len(session.compaction_entries) == 1
    assert session.compaction_summary is not None
    assert events[-1].payload["finish_reason"] == FinishReason.STOP.value
    assert [event.payload["status"] for event in events if event.type == "status.changed"] == [
        "compacting",
        "compacted",
    ]


@pytest.mark.asyncio
async def test_v2_compaction_rechecks_threshold_before_model_admission():
    provider = OverflowProvider()
    policy = _v2_policy(context_window_tokens=1_024, reserve_tokens=64, keep_recent_tokens=40)
    session = Session(session_id="s")
    seed_user_turn(session, "old request", assistant="old answer")
    seed_user_turn(session, "another old request", assistant="another answer")
    loop = AgentLoop(
        provider,
        MODEL,
        ContextBuilder(
            run_policy=policy,
            estimate_request_chars=lambda messages, tools: 1,
            estimate_request_tokens=lambda messages, tools: 2_000,
        ),
    )

    events = [event async for event in loop.run_task(session, "current request")]

    assert provider.stream_calls == 0
    assert provider.complete_calls == 1
    assert events[-1].payload["finish_reason"] == FinishReason.ERROR.value
    assert events[-1].payload["stop_code"] == AgentStopCode.CONTEXT_BUDGET.value


@pytest.mark.parametrize(
    ("code", "retryable"),
    [
        (ModelErrorCode.RATE_LIMIT, True),
        (ModelErrorCode.INTERNAL, True),
    ],
)
@pytest.mark.asyncio
async def test_compaction_summary_uses_bounded_transient_retries(code, retryable):
    provider = CompactionRetryProvider(code, retryable=retryable)
    delays: list[float] = []
    policy = _v2_policy(context_window_tokens=1_024, reserve_tokens=64, keep_recent_tokens=40)
    session = Session(session_id="s")
    seed_user_turn(session, "old request", assistant="old answer")
    seed_user_turn(session, "another old request", assistant="another answer")

    async def retry_sleep(delay: float) -> None:
        delays.append(delay)

    loop = AgentLoop(
        provider,
        MODEL,
        ContextBuilder(run_policy=policy, estimate_request_chars=lambda messages, tools: 1),
        retry_sleep=retry_sleep,
    )

    events = [event async for event in loop.run_task(session, "current request")]

    assert provider.complete_calls == 3
    assert delays == [2.0, 4.0]
    assert len(session.compaction_entries) == 1
    assert events[-1].payload["finish_reason"] == FinishReason.STOP.value


def test_compaction_summary_sections_are_structured_and_safe():
    summary = CompactionSummary(
        goal="ship the change",
        constraints_preferences=["keep the log immutable"],
        progress_done=["implemented the policy"],
        progress_in_progress=["run the offline gate"],
        progress_blocked=["live test is not authorized"],
        key_decisions=["use token accounting"],
        next_steps=["verify restart"],
        critical_context=["v2 has no cumulative stop"],
        files_read=["src/morrow/runtime/agent.py"],
        files_modified=["src/morrow/application/context.py"],
    )
    rendered = summary.render()

    assert "## Goal" in rendered
    assert "## Constraints & Preferences" in rendered
    assert "## Progress (Done)" in rendered
    assert "## Files Modified" in rendered
    with pytest.raises((ValidationError, ValueError)):
        CompactionSummary.model_validate({"goal": "sk-" + "x" * 24}, strict=True)


@pytest.mark.parametrize("entry_id", ("cmp_-urlsafe", "cmp__urlsafe"))
def test_compaction_entry_accepts_every_random_id_suffix_start(entry_id):
    summary = CompactionSummary(goal="continue")
    accounting = TokenAccounting(
        basis=TokenAccountingBasis.PI_ESTIMATOR,
        context_tokens=500,
        context_window_tokens=1_000,
        reserve_tokens=100,
        keep_recent_tokens=200,
    )

    assert _entry(summary, accounting, entry_id=entry_id).entry_id == entry_id


def test_compaction_summary_recovers_common_provider_json_wrappers():
    summary = CompactionSummary.from_provider_text(
        """Here is the summary:
```json
{
  "goal": "ship ,} safely",
  "progress_done": ["implemented"],
  "progress_blocked": null,
  "provider_note": "discard me",
}
```
"""
    )

    assert summary.goal == "ship ,} safely"
    assert summary.progress_done == ("implemented",)
    assert summary.progress_blocked == ()
    assert "provider_note" not in summary.model_dump()
    with pytest.raises(ValidationError):
        CompactionSummary.model_validate(
            {"goal": "ship the change", "provider_note": "reject durable extra"}, strict=True
        )
    with pytest.raises(ValidationError):
        CompactionSummary.model_validate({"goal": "ship", "progress_blocked": None}, strict=True)


def test_long_horizon_without_exact_window_uses_conservative_character_budget():
    policy = _v2_policy(context_window_tokens=None)
    builder = ContextBuilder(
        run_policy=policy,
        estimate_request_chars=lambda messages, tools: 300_000,
        estimate_request_tokens=lambda messages, tools: 50_000,
    )
    session = Session(session_id="s")
    seed_user_turn(session, "current request", assistant="answer")

    context = builder.build(session)

    assert context.accounting_basis is TokenAccountingBasis.PI_ESTIMATOR
    assert context.estimated_context_tokens == 50_000
    assert context.token_threshold is None
    assert context.compaction_required is True


@pytest.mark.asyncio
async def test_unknown_window_character_budget_can_compact_and_continue():
    provider = ProactiveCompactionProvider()
    policy = _v2_policy(context_window_tokens=None, keep_recent_tokens=1)
    session = Session(session_id="s")
    seed_user_turn(session, "old request", assistant="old answer")
    seed_user_turn(session, "another old request", assistant="another answer")
    loop = AgentLoop(
        provider,
        MODEL,
        ContextBuilder(
            run_policy=policy,
            estimate_request_chars=lambda messages, tools: len(messages) * 40_000,
            estimate_request_tokens=lambda messages, tools: len(messages),
        ),
    )

    events = [event async for event in loop.run_task(session, "current request")]

    assert provider.complete_calls == 1
    assert provider.stream_calls == 1
    assert len(session.compaction_entries) == 1
    assert session.compaction_entries[0].accounting.context_window_tokens is None
    assert events[-1].payload["finish_reason"] == FinishReason.STOP.value


def test_split_turn_compaction_keeps_user_anchor_and_tool_pairs_together():
    policy = _v2_policy(keep_recent_tokens=1_000_000)
    builder = ContextBuilder(
        run_policy=policy,
        estimate_request_chars=lambda messages, tools: 1,
        estimate_request_tokens=lambda messages, tools: 1,
    )
    session = Session(session_id="s")
    session.begin_user_turn(UserMessage(content="long task"))
    first = _tool_call("c1", "one")
    second = _tool_call("c2", "two")
    session.append_assistant(AssistantMessage(tool_calls=(first,)))
    session.append_tool_result("c1", '{"ok":true}')
    session.append_assistant(AssistantMessage(tool_calls=(second,)))
    session.append_tool_result("c2", '{"ok":true}')
    candidate = builder.prepare_compaction(session)

    assert candidate is None

    builder = ContextBuilder(
        run_policy=_v2_policy(keep_recent_tokens=1),
        estimate_request_chars=lambda messages, tools: 1,
        estimate_request_tokens=lambda messages, tools: 1,
    )
    candidate = builder.prepare_compaction(session)
    assert candidate is not None
    instructed = builder.prepare_compaction(session, instructions="保留接口变更")
    assert instructed is not None
    assert "保留接口变更" in instructed.summary_messages[0].content
    assert candidate.source_messages[0].role == "user"
    assert candidate.source_end_sequence < candidate.first_retained_sequence
    assert candidate.source_messages[-1].role == "tool"
    assert candidate.source_end_sequence == 3
    assert candidate.first_retained_sequence == 4


def test_compaction_checkpoint_round_trip_is_bounded_and_immutable():
    summary = CompactionSummary(goal="old work", progress_done=["done"])
    accounting = TokenAccounting(
        basis=TokenAccountingBasis.PI_ESTIMATOR,
        context_tokens=500,
        context_window_tokens=1_000,
        reserve_tokens=100,
        keep_recent_tokens=200,
    )
    entry = _entry(summary, accounting)
    records = (
        DurableConversationRecord(
            record_id="rec_1",
            session_id="ses_1",
            conversation_position=1,
            kind="message",
            payload={"role": "user", "content": "old"},
        ),
        DurableConversationRecord(
            record_id="rec_2",
            session_id="ses_1",
            conversation_position=2,
            kind="message",
            payload={"role": "assistant", "content": "answer"},
        ),
        DurableConversationRecord(
            record_id="rec_3",
            session_id="ses_1",
            conversation_position=3,
            kind="terminal",
            payload={"finish_reason": "stop", "interrupted_call_ids": []},
        ),
    )
    checkpoint = checkpoint_for_compaction("ws_1", entry, records)
    restored = entry_from_compaction_checkpoint(checkpoint)

    assert restored == entry
    assert checkpoint.codec == "pi_compaction"
    assert checkpoint.output_bytes <= 24 * 1024
    with pytest.raises(ValidationError):
        entry.instructions = "changed"


def _entry(summary, accounting, *, entry_id="cmp_1"):
    summary_digest = sha256_digest(
        json.dumps(
            summary.model_dump(mode="json"),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    )
    return CompactionEntry(
        entry_id=entry_id,
        session_id="ses_1",
        model=MODEL,
        summary=summary,
        first_retained_sequence=4,
        source_start_sequence=1,
        source_end_sequence=3,
        tokens_before=500,
        estimated_tokens_after=100,
        accounting=accounting,
        prompt_digest="a" * 64,
        source_digest="b" * 64,
        summary_digest=summary_digest,
    )


def test_pi_truncation_matches_head_tail_utf8_and_grep_boundaries():
    head = truncate_head("a\nb\nc", max_lines=2, max_bytes=100)
    tail = truncate_tail("a\nb\nc", max_lines=2, max_bytes=100)
    unicode_tail = truncate_tail("前" * 5, max_lines=2, max_bytes=7)
    line, was_truncated = truncate_line("x" * 6, max_chars=5)

    assert head.content == "a\nb"
    assert head.truncated_by == "lines"
    assert tail.content == "b\nc"
    assert tail.truncated_by == "lines"
    assert unicode_tail.content.encode("utf-8").decode("utf-8") == unicode_tail.content
    assert unicode_tail.last_line_partial is True
    assert line == "xxxxx... [truncated]"
    assert was_truncated is True


def test_current_read_contract_keeps_pi_shape_and_defers_bounds_to_execution():
    assert READ_PROVIDER_SCHEMA["required"] == ["path"]
    assert "maximum" not in READ_PROVIDER_SCHEMA["properties"]["limit"]
    parsed = ReadArguments.model_validate(
        {"path": "src/main.py", "limit": 2_001, "unused": True}, strict=True
    )
    assert parsed.limit == 2_001
    assert "unused" not in parsed.model_dump()


def test_artifact_projection_preserves_failed_tool_outcomes():
    reference = ArtifactReference(artifact_id="art_1")
    outcome = ToolExecutionOutcome(
        call_id="call_1",
        name="run_command",
        ok=False,
        envelope=json.dumps(
            {"ok": False, "error": {"code": "process_failed", "message": "failed"}},
            separators=(",", ":"),
        ),
        error_code=ToolErrorCode.PROCESS_FAILED,
    )

    projected = ToolCycleExecutor._attach_artifact_references(
        outcome, (reference,), result_limit=2_048
    )
    payload = json.loads(projected.envelope)

    assert payload["ok"] is False
    assert payload["error"]["code"] == "process_failed"
    assert payload["artifact_refs"][0]["artifact_id"] == "art_1"


@pytest.mark.asyncio
async def test_v2_truncation_artifact_is_model_visible_and_bounded_readable(tmp_path):
    store = OperationalStore(tmp_path / "state", maintenance_timeout=0)
    handle = store.initialize()
    try:
        journal = SqliteOperationalJournal(handle)
        journal.create_session(DurableSession(session_id="ses_1", workspace_id="ws_1"))
        artifacts = ArtifactService(
            journal=journal,
            filesystem=FilesystemArtifactStore(store.layout),
            workspace_id="ws_1",
            id_source=FixedIdSource(),
        )
        metadata = artifacts.publish_bytes(
            b"0123456789abcdef",
            kind="command_output",
            session_id="ses_1",
            already_redacted=True,
        )
        policy = _v2_policy()
        registry = ToolRegistry()
        registry.register(make_read_artifact_tool(artifacts))
        executor = ToolExecutor(registry.snapshot(), policy)
        context = ToolRunContext(run_id="arun_1", session_id="ses_1")

        first = await executor.execute_with_context(
            FunctionToolCall(
                id="call_1",
                name="read_artifact",
                arguments=json.dumps(
                    {"artifact_id": metadata.artifact_id, "max_bytes": 6},
                    separators=(",", ":"),
                ),
            ),
            run_context=context,
            ordinal=1,
            total=1,
        )
        first_payload = json.loads(first.envelope)["result"]
        assert first_payload["content"] == "012345"
        assert first_payload["truncated"] is True
        assert first_payload["next_start_byte"] == 6

        second = await executor.execute_with_context(
            FunctionToolCall(
                id="call_2",
                name="read_artifact",
                arguments=json.dumps(
                    {
                        "artifact_id": metadata.artifact_id,
                        "start_byte": first_payload["next_start_byte"],
                        "max_bytes": 64,
                    },
                    separators=(",", ":"),
                ),
            ),
            run_context=context,
            ordinal=1,
            total=1,
        )
        second_payload = json.loads(second.envelope)["result"]
        assert second_payload["content"] == "6789abcdef"
        assert second_payload["truncated"] is False

        unicode_metadata = artifacts.publish_bytes(
            "前😀后".encode(),
            kind="command_output",
            session_id="ses_1",
            already_redacted=True,
        )
        unicode_first = await executor.execute_with_context(
            FunctionToolCall(
                id="call_4",
                name="read_artifact",
                arguments=json.dumps(
                    {"artifact_id": unicode_metadata.artifact_id, "max_bytes": 4},
                    separators=(",", ":"),
                ),
            ),
            run_context=context,
            ordinal=1,
            total=1,
        )
        unicode_payload = json.loads(unicode_first.envelope)["result"]
        assert unicode_payload["content"] == "前"
        assert unicode_payload["next_start_byte"] == 3

        unicode_second = await executor.execute_with_context(
            FunctionToolCall(
                id="call_5",
                name="read_artifact",
                arguments=json.dumps(
                    {
                        "artifact_id": unicode_metadata.artifact_id,
                        "start_byte": unicode_payload["next_start_byte"],
                        "max_bytes": 64,
                    },
                    separators=(",", ":"),
                ),
            ),
            run_context=context,
            ordinal=1,
            total=1,
        )
        assert json.loads(unicode_second.envelope)["result"]["content"] == "😀后"

        denied = await executor.execute_with_context(
            FunctionToolCall(
                id="call_3",
                name="read_artifact",
                arguments=json.dumps({"artifact_id": metadata.artifact_id}),
            ),
            run_context=ToolRunContext(run_id="arun_2", session_id="ses_2"),
            ordinal=1,
            total=1,
        )
        assert denied.error_code is ToolErrorCode.NOT_FOUND
    finally:
        handle.close()
