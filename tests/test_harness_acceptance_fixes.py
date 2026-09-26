"""Fault-injection regressions for the final harness acceptance findings."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from morrow.adapters.local.process import HostProcessAdapter, _CursorBuffer
from morrow.core.capabilities import (
    PolicyVerdict,
    ToolHandlerOutcome,
    ToolRunContext,
    ValidationFact,
)
from morrow.core.local_tools import (
    CommandRequest,
    TrackedCommandStatus,
    TrackedLifecycle,
)
from morrow.core.models import (
    AssistantMessage,
    FunctionToolCall,
    ModelErrorCode,
    ModelEvent,
    ModelFailure,
    ModelFailureOrigin,
    ModelRef,
)
from morrow.runtime.agent import AgentLoop
from morrow.runtime.completion_check import check_completion
from morrow.runtime.session import Session
from morrow.runtime.tools import ToolExecutor, ToolRegistry, make_tool
from morrow.services import bash_execution
from morrow.services.files import WorkspaceFileService, WorkspacePathResolver
from morrow.services.process import PreparedBash, ProcessExecutionService, SecretRedactor
from morrow.services.tracked_process import TrackedExecution, TrackedProcessRegistry
from morrow.testing import ScriptedModelProvider, make_context_builder

MODEL = ModelRef(provider_id="p", model_id="m")
EXECUTION_ID = "exec_" + "a" * 24


def _buffered_execution(data: bytes, *, running: bool = True, capacity: int = 1024):
    stdout, stderr = _CursorBuffer(capacity), _CursorBuffer(capacity)
    stdout.add(data)
    return SimpleNamespace(
        execution_id=EXECUTION_ID,
        spawned=SimpleNamespace(stdout=stdout, stderr=stderr),
        status=TrackedCommandStatus.RUNNING if running else TrackedCommandStatus.EXITED,
        exit_code=None if running else 0,
        signal=None,
        lifecycle=TrackedLifecycle.TASK,
        cwd_relative=".",
        command_class="shell",
    )


@pytest.mark.parametrize("running", [True, False])
@pytest.mark.parametrize("limit", [1, 4, 20, 64])
def test_tracked_pages_redact_before_any_cut(running, limit):
    secret = "合成凭据_SECRET_1234"
    raw = b"hello " + secret.encode() + b" world"
    execution = _buffered_execution(raw, running=running)
    redactor = SecretRedactor((secret,))
    registry = TrackedProcessRegistry()
    offset = 0
    pages = []
    while offset < len(raw):
        view = registry._view(
            execution, offset=offset, stderr_offset=0, limit=limit, redactor=redactor
        )
        assert view.output_offset > offset
        pages.append(view.stdout)
        offset = view.output_offset
    output = "".join(pages)
    assert "合成凭据" not in output
    assert "SECRET_1234" not in output
    assert "hello " in output and " world" in output


def test_tracked_ring_truncation_hides_secret_suffix_and_arbitrary_offset():
    secret = "SYNTHETIC_SECRET_1234"
    execution = _buffered_execution(secret.encode() + b"-safe", capacity=15)
    view = TrackedProcessRegistry()._view(
        execution, offset=0, stderr_offset=0, limit=1, redactor=SecretRedactor((secret,))
    )
    assert view.output_truncated
    assert "1234" not in view.stdout
    assert view.output_offset > execution.spawned.stdout.base


def test_tracked_ring_smaller_than_credential_never_discloses_middle():
    secret = "SYNTHETIC_" + "X" * 40 + "_SECRET"
    execution = _buffered_execution(secret.encode(), capacity=12)
    view = TrackedProcessRegistry()._view(
        execution, offset=0, stderr_offset=0, limit=2, redactor=SecretRedactor((secret,))
    )
    assert view.stdout == "<redacted>"
    assert view.output_offset > execution.spawned.stdout.base


def test_tracked_stderr_uses_the_same_safe_paging():
    secret = "SYNTHETIC_SECRET_1234"
    execution = _buffered_execution(b"ordinary")
    execution.spawned.stderr.add(b"before " + secret.encode() + b" after")
    view = TrackedProcessRegistry()._view(
        execution, offset=0, stderr_offset=0, limit=12, redactor=SecretRedactor((secret,))
    )
    assert "SYNTHETIC" not in view.stderr
    assert view.stderr_offset > 0


def test_tracked_growing_stream_never_reveals_secret_across_polls():
    secret = "SYNTHETIC_SECRET_1234"
    execution = _buffered_execution(b"prefix SYNTHETIC_")
    registry = TrackedProcessRegistry()
    redactor = SecretRedactor((secret,))
    first = registry._view(execution, offset=0, stderr_offset=0, limit=64, redactor=redactor)
    assert "SYNTHETIC" not in first.stdout
    execution.spawned.stdout.add(b"SECRET_1234 suffix")
    second = registry._view(
        execution,
        offset=first.output_offset,
        stderr_offset=0,
        limit=64,
        redactor=redactor,
    )
    assert "SYNTHETIC" not in first.stdout + second.stdout
    assert "SECRET_1234" not in first.stdout + second.stdout
    assert "prefix " in first.stdout and " suffix" in second.stdout


def test_tracked_utf8_cursor_preserves_nonsecret_text_with_one_byte_limit():
    original = "开始 café 结束"
    execution = _buffered_execution(original.encode())
    registry = TrackedProcessRegistry()
    offset = 0
    pages = []
    while offset < len(original.encode()):
        view = registry._view(
            execution,
            offset=offset,
            stderr_offset=0,
            limit=1,
            redactor=SecretRedactor(("SYNTHETIC_SECRET",)),
        )
        assert view.output_offset > offset
        pages.append(view.stdout)
        offset = view.output_offset
    assert "".join(pages) == original


@pytest.mark.asyncio
async def test_overflow_compaction_is_rejected_after_work_deadline():
    clock = SimpleNamespace(value=0.0)
    entered = []

    class ProbeLoop(AgentLoop):
        async def _compact_context(self, *args, **kwargs):
            entered.append(clock.value)
            return False

    provider = ScriptedModelProvider(
        [
            ModelEvent(
                kind="error",
                failure=ModelFailure(
                    code=ModelErrorCode.CONTEXT_OVERFLOW,
                    origin=ModelFailureOrigin.PROVIDER,
                    message="synthetic overflow",
                ),
            )
        ]
    )
    events = []
    async for event in ProbeLoop(
        provider, MODEL, make_context_builder(), monotonic_clock=lambda: clock.value
    ).run_task(Session(session_id="s"), "work", run_timeout_seconds=10):
        events.append(event)
        if event.type == "status.changed" and event.payload.get("status") == "compacting":
            clock.value = 10.0
    assert not entered
    assert events[-1].payload["stop_code"] == "run_timeout"


@pytest.mark.asyncio
async def test_overflow_compaction_wait_is_bounded():
    entered = asyncio.Event()

    class WaitingLoop(AgentLoop):
        async def _compact_context(self, *args, **kwargs):
            entered.set()
            await asyncio.Event().wait()

    provider = ScriptedModelProvider(
        [
            ModelEvent(
                kind="error",
                failure=ModelFailure(
                    code=ModelErrorCode.CONTEXT_OVERFLOW,
                    origin=ModelFailureOrigin.PROVIDER,
                    message="synthetic overflow",
                ),
            )
        ]
    )
    events = [
        event
        async for event in WaitingLoop(provider, MODEL, make_context_builder()).run_task(
            Session(session_id="s"), "work", run_timeout_seconds=0.1
        )
    ]
    assert entered.is_set()
    assert events[-1].payload["stop_code"] == "run_timeout"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    [
        ConnectionError("synthetic retry"),
        ModelEvent(
            kind="error",
            failure=ModelFailure(
                code=ModelErrorCode.INVALID_RESPONSE,
                origin=ModelFailureOrigin.PROVIDER,
                retryable=True,
                message="synthetic invalid stream",
            ),
        ),
    ],
)
async def test_review_retry_keeps_candidate_and_prompt_without_reexecuting_tool(failure):
    class Arguments(BaseModel):
        pass

    executions = []

    async def handler(_arguments):
        executions.append("done")
        return "done"

    async def no_sleep(_seconds):
        pass

    registry = ToolRegistry()
    registry.register(
        make_tool(name="work", description="work", arguments_model=Arguments, handler=handler)
    )
    provider = ScriptedModelProvider(
        [
            AssistantMessage(tool_calls=(FunctionToolCall(id="c1", name="work", arguments="{}"),)),
            AssistantMessage(content="CANDIDATE_WITH_WRONG_UNITS"),
            failure,
            AssistantMessage(content="final"),
        ]
    )
    builder = make_context_builder()
    session = Session(session_id="s")
    events = [
        event
        async for event in AgentLoop(
            provider,
            MODEL,
            builder,
            retry_sleep=no_sleep,
            tool_executor=ToolExecutor(registry.snapshot(), builder.run_policy),
        ).run_task(session, "Write out/report.json")
    ]
    assert events[-1].payload["finish_reason"] == "stop"
    assert executions == ["done"]
    assert session.log.messages_view()[-1].content == "final"
    for call in provider.stream_calls[2:]:
        assert "CANDIDATE_WITH_WRONG_UNITS" in str(call)
        assert "交付前复核" in str(call)


@pytest.mark.asyncio
async def test_second_review_retry_keeps_its_own_candidate():
    class Arguments(BaseModel):
        pass

    async def handler(_arguments):
        return ToolHandlerOutcome(
            payload="done",
            facts=(
                ValidationFact(
                    call_id="synthetic",
                    tool_name="work",
                    ordinal=1,
                    approval_verdict=PolicyVerdict.ALLOW,
                    relative_paths=(".",),
                    validator_kind="pytest",
                    scope=".",
                    status="failed",
                    exit_code=1,
                    evidence_summary="exit_nonzero",
                ),
            ),
        )

    async def no_sleep(_seconds):
        pass

    registry = ToolRegistry()
    registry.register(
        make_tool(name="work", description="work", arguments_model=Arguments, handler=handler)
    )
    provider = ScriptedModelProvider(
        [
            AssistantMessage(tool_calls=(FunctionToolCall(id="c1", name="work", arguments="{}"),)),
            AssistantMessage(content="first candidate"),
            AssistantMessage(tool_calls=(FunctionToolCall(id="c2", name="work", arguments="{}"),)),
            AssistantMessage(content="second candidate"),
            ConnectionError("second review retry"),
            AssistantMessage(content="accepted answer"),
        ]
    )
    builder = make_context_builder()
    session = Session(session_id="s")
    events = [
        event
        async for event in AgentLoop(
            provider,
            MODEL,
            builder,
            retry_sleep=no_sleep,
            tool_executor=ToolExecutor(registry.snapshot(), builder.run_policy),
        ).run_task(session, "Write out/report.json")
    ]
    assert events[-1].payload["finish_reason"] == "stop"
    assert session.log.messages_view()[-1].content == "accepted answer"
    assert len(provider.stream_calls) == 6
    for call in provider.stream_calls[4:]:
        assert "second candidate" in str(call)
        assert "最后一次自动复核" in str(call)


def _run() -> ToolRunContext:
    return ToolRunContext(run_id="run", session_id="s")


def _passed(call_id: str, ordinal: int) -> ValidationFact:
    return ValidationFact(
        call_id=call_id,
        tool_name="bash",
        ordinal=ordinal,
        approval_verdict=PolicyVerdict.ALLOW,
        relative_paths=(".",),
        validator_kind="pytest",
        scope=".",
        status="passed",
        exit_code=0,
        evidence_summary="exit_zero",
    )


@pytest.mark.asyncio
async def test_tracked_command_facts_invalidate_then_settle_once(tmp_path: Path, monkeypatch):
    service = ProcessExecutionService(WorkspaceFileService(WorkspacePathResolver(tmp_path)))
    execution = TrackedExecution(
        execution_id=EXECUTION_ID,
        session_id="s",
        task_id="t",
        lifecycle=TrackedLifecycle.TASK,
        command_class="shell",
        cwd_relative=".",
        adapter=HostProcessAdapter(),
        spawned=SimpleNamespace(
            stdout=_CursorBuffer(1024),
            stderr=_CursorBuffer(1024),
            started=time.monotonic(),
        ),
    )
    service.tracked._items[EXECUTION_ID] = execution
    monkeypatch.setattr(service.tracked, "_refresh", lambda _execution: None)

    async def fake_start(*_args, **_kwargs):
        return EXECUTION_ID

    monkeypatch.setattr(bash_execution, "_start", fake_start)
    run = _run()
    run.record((_passed("old", 1),))
    start = await bash_execution.run_bash(
        service,
        PreparedBash(
            action="start",
            plan=service.preflight(CommandRequest(shell="printf changed > result.txt")),
        ),
        session_id="s",
        task_id="t",
        result_limit=8192,
        run=run,
        call_id="start",
        tool_name="bash",
        ordinal=2,
        approval_verdict=PolicyVerdict.ALLOW,
    )
    run.record(start.facts)
    assert start.facts[0].status == "running"
    assert check_completion(run).validation_outcome != "passed"
    run.record((_passed("during", 3),))
    assert check_completion(run).validation_outcome != "passed"

    execution.status, execution.exit_code = TrackedCommandStatus.EXITED, 0
    poll = PreparedBash(action="poll", execution_id=EXECUTION_ID)
    final = await bash_execution.run_bash(
        service,
        poll,
        session_id="s",
        task_id="t",
        result_limit=8192,
        run=run,
        call_id="poll",
        tool_name="bash",
        ordinal=4,
        approval_verdict=PolicyVerdict.ALLOW,
    )
    run.record(final.facts)
    assert len(final.facts) == 1 and final.facts[0].status == "exited"
    assert check_completion(run).validation_outcome != "passed"
    repeated = await bash_execution.run_bash(
        service,
        poll,
        session_id="s",
        task_id="t",
        result_limit=8192,
        run=run,
        call_id="poll-again",
        tool_name="bash",
        ordinal=5,
        approval_verdict=PolicyVerdict.ALLOW,
    )
    assert repeated.facts == ()
    run.record((_passed("new", 6),))
    assert check_completion(run).validation_outcome == "passed"


@pytest.mark.asyncio
async def test_tracked_validator_terminal_failure_is_evidence(tmp_path: Path, monkeypatch):
    service = ProcessExecutionService(WorkspaceFileService(WorkspacePathResolver(tmp_path)))
    execution = TrackedExecution(
        execution_id=EXECUTION_ID,
        session_id="s",
        task_id="t",
        lifecycle=TrackedLifecycle.TASK,
        command_class="shell",
        cwd_relative=".",
        adapter=HostProcessAdapter(),
        spawned=SimpleNamespace(
            stdout=_CursorBuffer(1024),
            stderr=_CursorBuffer(1024),
            started=time.monotonic(),
        ),
        validation_kind="pytest",
        validation_scope=".",
    )
    service.tracked._items[EXECUTION_ID] = execution
    monkeypatch.setattr(service.tracked, "_refresh", lambda _execution: None)

    async def fake_start(*_args, **_kwargs):
        return EXECUTION_ID

    monkeypatch.setattr(bash_execution, "_start", fake_start)
    run = _run()
    start = await bash_execution.run_bash(
        service,
        PreparedBash(action="start", plan=service.preflight(CommandRequest(shell="pytest"))),
        session_id="s",
        task_id="t",
        result_limit=8192,
        run=run,
        call_id="start",
        tool_name="bash",
        ordinal=1,
        approval_verdict=PolicyVerdict.ALLOW,
    )
    run.record(start.facts)
    assert not run.validation_facts
    execution.status, execution.exit_code = TrackedCommandStatus.EXITED, 1
    final = await bash_execution.run_bash(
        service,
        PreparedBash(action="poll", execution_id=EXECUTION_ID),
        session_id="s",
        task_id="t",
        result_limit=8192,
        run=run,
        call_id="poll",
        tool_name="bash",
        ordinal=2,
        approval_verdict=PolicyVerdict.ALLOW,
    )
    run.record(final.facts)
    assert final.facts[1].status == "failed"
    assert check_completion(run).validation_outcome == "failed"
