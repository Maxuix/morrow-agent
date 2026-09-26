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
    ChangeToolFact,
    CommandToolFact,
    PolicyVerdict,
    ToolFact,
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
        started_run_id="run",
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


def _tracked_service(
    tmp_path: Path,
    monkeypatch,
    *,
    lifecycle=TrackedLifecycle.TASK,
    validation_kind: str | None = None,
    validation_scope: str | None = None,
    started_run_id: str | None = None,
):
    service = ProcessExecutionService(WorkspaceFileService(WorkspacePathResolver(tmp_path)))
    execution = TrackedExecution(
        execution_id=EXECUTION_ID,
        session_id="s",
        task_id="t",
        lifecycle=lifecycle,
        command_class="shell",
        cwd_relative=".",
        adapter=HostProcessAdapter(),
        spawned=SimpleNamespace(
            stdout=_CursorBuffer(1024),
            stderr=_CursorBuffer(1024),
            started=time.monotonic(),
        ),
        validation_kind=validation_kind,
        validation_scope=validation_scope,
        started_run_id=started_run_id,
    )
    service.tracked._items[EXECUTION_ID] = execution
    monkeypatch.setattr(service.tracked, "_refresh", lambda _execution: None)
    return service, execution


async def _poll(service, run, *, task_id="t", call_id="poll", ordinal=2):
    return await bash_execution.run_bash(
        service,
        PreparedBash(action="poll", execution_id=EXECUTION_ID),
        session_id="s",
        task_id=task_id,
        result_limit=8192,
        run=run,
        call_id=call_id,
        tool_name="bash",
        ordinal=ordinal,
        approval_verdict=PolicyVerdict.ALLOW,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "lifecycle,next_task",
    [
        (TrackedLifecycle.TASK, "t"),
        (TrackedLifecycle.ACCEPTANCE, "next-task"),
    ],
)
async def test_running_poll_in_later_run_invalidates_validation(
    tmp_path: Path, monkeypatch, lifecycle, next_task
):
    # The registry survives; each AgentLoop.run_task gets a fresh ToolRunContext.
    service, _execution = _tracked_service(tmp_path, monkeypatch, lifecycle=lifecycle)
    run = ToolRunContext(run_id="next-run", session_id="s", owner_task_id=next_task)
    run.record((_passed("validate", 1),))
    result = await _poll(service, run, task_id=next_task)
    assert result.payload.status is TrackedCommandStatus.RUNNING
    assert len(result.facts) == 1 and result.facts[0].status == "running"
    assert not result.facts[0].historical
    run.record(result.facts)
    assert check_completion(run).validation_outcome != "passed"
    # A repeated poll of the unchanged state stays idempotent within this run.
    repeated = await _poll(service, run, task_id=next_task, call_id="poll-again", ordinal=3)
    assert repeated.facts == ()


@pytest.mark.asyncio
async def test_settled_terminal_state_stays_visible_to_later_runs(tmp_path: Path, monkeypatch):
    service, execution = _tracked_service(
        tmp_path,
        monkeypatch,
        lifecycle=TrackedLifecycle.ACCEPTANCE,
        validation_kind="pytest",
        validation_scope=".",
        started_run_id="first-run",
    )
    execution.status, execution.exit_code = TrackedCommandStatus.EXITED, 1
    first = ToolRunContext(run_id="first-run", session_id="s", owner_task_id="t")
    first.record((_start_fact(1),))
    claimed = await _poll(service, first)
    assert len(claimed.facts) == 2 and claimed.facts[1].status == "failed"
    assert not any(fact.historical for fact in claimed.facts)
    first.record(claimed.facts)

    later = ToolRunContext(run_id="later-run", session_id="s", owner_task_id="next-task")
    visible = await _poll(service, later, task_id="next-task")
    assert len(visible.facts) == 2 and visible.facts[1].status == "failed"
    # Cross-run evidence is historical even when this run's fact chain is
    # empty: prior runs changed the workspace without leaving facts here.
    assert all(fact.historical for fact in visible.facts)
    later.record(visible.facts)
    later_check = check_completion(later)
    assert later_check.validation_outcome == "not_run"
    assert any("历史结果" in line and "failed" in line for line in later_check.evidence)
    # A repeated read in the same run does not settle or record again.
    repeated = await _poll(service, later, task_id="next-task", call_id="poll-again", ordinal=3)
    assert repeated.facts == ()

    # Reading the settled state neither consumes it nor settles it again.
    third = ToolRunContext(run_id="third-run", session_id="s", owner_task_id="another-task")
    reread = await _poll(service, third, task_id="another-task")
    assert len(reread.facts) == 2 and reread.facts[1].status == "failed"

    # After the terminal state is visible, revalidation recovers passed.
    later.record((_passed("recheck", 4),))
    assert check_completion(later).validation_outcome == "passed"


def _failed(call_id: str, ordinal: int) -> ValidationFact:
    return ValidationFact(
        call_id=call_id,
        tool_name="bash",
        ordinal=ordinal,
        approval_verdict=PolicyVerdict.ALLOW,
        relative_paths=(".",),
        validator_kind="pytest",
        scope=".",
        status="failed",
        exit_code=1,
        evidence_summary="exit_nonzero",
    )


async def _settled_validator_run(tmp_path: Path, monkeypatch, *, exit_code: int):
    """One old run starts an acceptance validator, and it finishes unpolled."""

    service, execution = _tracked_service(
        tmp_path,
        monkeypatch,
        lifecycle=TrackedLifecycle.ACCEPTANCE,
        validation_kind="pytest",
        validation_scope=".",
        started_run_id="old-run",
    )
    execution.status, execution.exit_code = TrackedCommandStatus.EXITED, exit_code
    old = ToolRunContext(run_id="old-run", session_id="s", owner_task_id="old-task")
    old.record((_start_fact(1),))
    claimed = await _poll(service, old, task_id="old-task", call_id="poll-old", ordinal=2)
    assert not any(fact.historical for fact in claimed.facts)
    old.record(claimed.facts)
    return service, old


@pytest.mark.asyncio
@pytest.mark.parametrize("later_fact", ["change", "failed_validation"])
async def test_historical_pass_does_not_validate_later_changes(
    tmp_path: Path, monkeypatch, later_fact
):
    service, old = await _settled_validator_run(tmp_path, monkeypatch, exit_code=0)
    assert check_completion(old).validation_outcome == "passed"
    new = ToolRunContext(run_id="new-run", session_id="s", owner_task_id="new-task")
    if later_fact == "change":
        fact: ToolFact = ChangeToolFact(
            call_id="write",
            tool_name="write",
            ordinal=1,
            approval_verdict=PolicyVerdict.ALLOW,
            relative_paths=("src/main.py",),
            operation="write",
            status="succeeded",
            changed_lines=1,
            changed_bytes=10,
        )
    else:
        fact = _failed("new-test", 1)
    new.record((fact,))
    reread = await _poll(service, new, task_id="new-task", call_id="poll-new")
    assert len(reread.facts) == 2 and all(item.historical for item in reread.facts)
    new.record(reread.facts)
    check = check_completion(new)
    # An old pass never proves the current version; a new failure survives it.
    assert check.validation_outcome == ("not_run" if later_fact == "change" else "failed")
    assert any("历史结果" in line and "passed" in line for line in check.evidence)


@pytest.mark.asyncio
async def test_historical_failure_does_not_disguise_new_pass(tmp_path: Path, monkeypatch):
    service, old = await _settled_validator_run(tmp_path, monkeypatch, exit_code=1)
    assert check_completion(old).validation_outcome == "failed"
    new = ToolRunContext(run_id="new-run", session_id="s", owner_task_id="new-task")
    new.record((_passed("new-test", 1),))
    reread = await _poll(service, new, task_id="new-task", call_id="poll-new")
    new.record(reread.facts)
    check = check_completion(new)
    assert check.validation_outcome == "passed"
    assert any("历史结果" in line and "failed" in line for line in check.evidence)


def _start_fact(ordinal: int) -> CommandToolFact:
    return CommandToolFact(
        call_id="start",
        tool_name="bash",
        ordinal=ordinal,
        approval_verdict=PolicyVerdict.ALLOW,
        relative_paths=(".",),
        command_class="shell",
        status="running",
        duration_ms=0,
        execution_id=EXECUTION_ID,
    )


def _change(ordinal: int) -> ChangeToolFact:
    return ChangeToolFact(
        call_id="write",
        tool_name="write",
        ordinal=ordinal,
        approval_verdict=PolicyVerdict.ALLOW,
        relative_paths=("src/main.py",),
        operation="write",
        status="succeeded",
        changed_lines=1,
        changed_bytes=10,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("later_fact", ["change", "failed_validation"])
@pytest.mark.parametrize("same_run", [False, True])
async def test_unobserved_old_terminal_does_not_validate_later_facts(
    tmp_path: Path, monkeypatch, later_fact, same_run
):
    # The process exited before anyone polled its terminal; claiming it now
    # must follow the same validity rule as a re-read.
    service, execution = _tracked_service(
        tmp_path,
        monkeypatch,
        lifecycle=TrackedLifecycle.ACCEPTANCE,
        validation_kind="pytest",
        validation_scope=".",
    )
    execution.status, execution.exit_code = TrackedCommandStatus.EXITED, 0
    assert execution.terminal_fact is None
    old = ToolRunContext(run_id="old-run", session_id="s", owner_task_id="t")
    run = (
        old
        if same_run
        else ToolRunContext(run_id="new-run", session_id="s", owner_task_id="new-task")
    )
    if same_run:
        # A start observation precedes termination and the later change/check.
        run.record((_start_fact(1),))
    run.record((_change(2) if later_fact == "change" else _failed("new-test", 2),))
    task_id = "t" if same_run else "new-task"
    result = await _poll(service, run, task_id=task_id, call_id="poll", ordinal=3)
    assert len(result.facts) == 2 and all(fact.historical for fact in result.facts)
    run.record(result.facts)
    check = check_completion(run)
    assert check.validation_outcome != "passed"
    if later_fact == "failed_validation":
        # The delayed first claim must not erase the newer failure.
        assert check.validation_outcome == "failed"
    assert any("历史结果" in line and "passed" in line for line in check.evidence)


@pytest.mark.asyncio
async def test_unobserved_old_failure_does_not_disguise_newer_pass(tmp_path: Path, monkeypatch):
    service, execution = _tracked_service(
        tmp_path,
        monkeypatch,
        lifecycle=TrackedLifecycle.ACCEPTANCE,
        validation_kind="pytest",
        validation_scope=".",
    )
    execution.status, execution.exit_code = TrackedCommandStatus.EXITED, 1
    run = ToolRunContext(run_id="new-run", session_id="s", owner_task_id="new-task")
    run.record((_passed("new-test", 1),))
    result = await _poll(service, run, task_id="new-task", call_id="poll", ordinal=2)
    assert len(result.facts) == 2 and all(fact.historical for fact in result.facts)
    run.record(result.facts)
    assert check_completion(run).validation_outcome == "passed"


@pytest.mark.asyncio
async def test_background_validator_started_after_change_can_pass(tmp_path: Path, monkeypatch):
    service, execution = _tracked_service(
        tmp_path,
        monkeypatch,
        validation_kind="pytest",
        validation_scope=".",
        started_run_id="run",
    )

    async def fake_start(*_args, **_kwargs):
        return EXECUTION_ID

    monkeypatch.setattr(bash_execution, "_start", fake_start)
    run = _run()
    run.record((_change(1),))
    start = await bash_execution.run_bash(
        service,
        PreparedBash(action="start", plan=service.preflight(CommandRequest(shell="pytest"))),
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
    execution.status, execution.exit_code = TrackedCommandStatus.EXITED, 0
    final = await _poll(service, run, call_id="poll", ordinal=3)
    assert len(final.facts) == 2 and not any(fact.historical for fact in final.facts)
    run.record(final.facts)
    assert check_completion(run).validation_outcome == "passed"


@pytest.mark.asyncio
async def test_background_validator_started_before_change_stays_historical(
    tmp_path: Path, monkeypatch
):
    service, execution = _tracked_service(
        tmp_path,
        monkeypatch,
        validation_kind="pytest",
        validation_scope=".",
        started_run_id="run",
    )

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
    # The workspace changed while the validator was already running.
    run.record((_change(2),))
    execution.status, execution.exit_code = TrackedCommandStatus.EXITED, 0
    final = await _poll(service, run, call_id="poll", ordinal=3)
    assert len(final.facts) == 2 and all(fact.historical for fact in final.facts)
    run.record(final.facts)
    assert check_completion(run).validation_outcome == "not_run"
    # Revalidation after the terminal observation recovers passed.
    run.record((_passed("recheck", 4),))
    assert check_completion(run).validation_outcome == "passed"


@pytest.mark.asyncio
@pytest.mark.parametrize("settled_before_change", [False, True])
async def test_empty_later_run_does_not_revive_old_pass(
    tmp_path: Path, monkeypatch, settled_before_change
):
    # A new run's empty fact list is not proof of an unchanged workspace: the
    # prior run's change left no facts here, so the old pass stays historical.
    service, execution = _tracked_service(
        tmp_path,
        monkeypatch,
        lifecycle=TrackedLifecycle.ACCEPTANCE,
        validation_kind="pytest",
        validation_scope=".",
        started_run_id="old-run",
    )
    execution.status, execution.exit_code = TrackedCommandStatus.EXITED, 0
    old = ToolRunContext(run_id="old-run", session_id="s", owner_task_id="t")
    old.record((_start_fact(1),))
    if settled_before_change:
        claimed = await _poll(service, old, call_id="poll-old", ordinal=2)
        assert not any(fact.historical for fact in claimed.facts)
        old.record(claimed.facts)
        assert check_completion(old).validation_outcome == "passed"
    old.record((_change(3),))
    assert check_completion(old).validation_outcome != "passed"
    later = ToolRunContext(run_id="later-run", session_id="s", owner_task_id="t")
    result = await _poll(service, later, call_id="poll-later", ordinal=2)
    assert result.payload.status is TrackedCommandStatus.EXITED
    assert len(result.facts) == 2 and all(fact.historical for fact in result.facts)
    later.record(result.facts)
    later_check = check_completion(later)
    assert later_check.validation_outcome == "not_run"
    assert any("历史结果" in line and "passed" in line for line in later_check.evidence)
    # Revalidation in the new run recovers passed.
    later.record((_passed("recheck", 3),))
    assert check_completion(later).validation_outcome == "passed"
