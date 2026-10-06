"""Independent contract matrix for tracked validation provenance and ordering."""

import time
from types import SimpleNamespace

import pytest

from morrow.adapters.local.process import HostProcessAdapter, _CursorBuffer
from morrow.core.capabilities import (
    ChangeToolFact,
    CommandToolFact,
    PolicyVerdict,
    ToolRunContext,
    ValidationFact,
)
from morrow.core.local_tools import TrackedCommandStatus, TrackedLifecycle
from morrow.runtime.completion_check import check_completion
from morrow.services import bash_execution
from morrow.services.files import WorkspaceFileService, WorkspacePathResolver
from morrow.services.process import PreparedBash, ProcessExecutionService
from morrow.services.tracked_process import TrackedExecution


@pytest.mark.asyncio
@pytest.mark.parametrize("origin", ["same", "same_missing_anchor", "other", "unknown"])
@pytest.mark.parametrize("exit_code", [0, 1])
@pytest.mark.parametrize("already_settled", [False, True])
@pytest.mark.parametrize("later_fact", ["none", "change", "passed", "failed"])
async def test_evidence_contract(
    tmp_path, monkeypatch, origin, exit_code, already_settled, later_fact
):
    service = ProcessExecutionService(WorkspaceFileService(WorkspacePathResolver(tmp_path)))
    execution_id = "exec_" + "a" * 24
    origin_id = "run" if origin.startswith("same") else ("old-run" if origin == "other" else None)
    execution = TrackedExecution(
        execution_id=execution_id,
        session_id="s",
        task_id="t",
        lifecycle=TrackedLifecycle.ACCEPTANCE,
        command_class="shell",
        cwd_relative=".",
        adapter=HostProcessAdapter(),
        started_run_id=origin_id,
        spawned=SimpleNamespace(
            stdout=_CursorBuffer(1024), stderr=_CursorBuffer(1024), started=time.monotonic()
        ),
        status=TrackedCommandStatus.EXITED,
        exit_code=exit_code,
        validation_kind="pytest",
        validation_scope=".",
    )
    service.tracked._items[execution_id] = execution
    monkeypatch.setattr(service.tracked, "_refresh", lambda item: None)
    run = ToolRunContext(run_id="run", session_id="s", owner_task_id="t")
    if origin == "same":
        run.record(
            (
                CommandToolFact(
                    call_id="start",
                    tool_name="bash",
                    ordinal=1,
                    approval_verdict=PolicyVerdict.ALLOW,
                    command_class="shell",
                    status="running",
                    duration_ms=0,
                    execution_id=execution_id,
                ),
            )
        )

    def validation(status, ordinal):
        return ValidationFact(
            call_id="validation-" + str(ordinal),
            tool_name="bash",
            ordinal=ordinal,
            approval_verdict=PolicyVerdict.ALLOW,
            relative_paths=(".",),
            validator_kind="pytest",
            scope=".",
            status=status,
            exit_code=0 if status == "passed" else 1,
            evidence_summary="synthetic",
        )

    if later_fact == "change":
        run.record(
            (
                ChangeToolFact(
                    call_id="write",
                    tool_name="write",
                    ordinal=2,
                    approval_verdict=PolicyVerdict.ALLOW,
                    relative_paths=("src/main.py",),
                    operation="write",
                    status="succeeded",
                    changed_lines=1,
                    changed_bytes=10,
                ),
            )
        )
    elif later_fact != "none":
        run.record((validation(later_fact, 2),))
    if already_settled:
        assert (
            service.tracked.claim_terminal_fact(execution_id, session_id="s", task_id="t")
            is not None
        )

    async def poll(ordinal):
        return await bash_execution.run_bash(
            service,
            PreparedBash(action="poll", execution_id=execution_id),
            session_id="s",
            task_id="t",
            result_limit=8192,
            run=run,
            call_id="poll-" + str(ordinal),
            tool_name="bash",
            ordinal=ordinal,
            approval_verdict=PolicyVerdict.ALLOW,
        )

    result = await poll(3)
    is_current = origin == "same" and later_fact == "none"
    assert len(result.facts) == 2
    assert all(fact.historical is (not is_current) for fact in result.facts)
    run.record(result.facts)
    expected = (
        ("passed" if exit_code == 0 else "failed")
        if is_current
        else (later_fact if later_fact in {"passed", "failed"} else "not_run")
    )
    assert check_completion(run).validation_outcome == expected
    assert run.metrics("stop").validation_outcome == expected
    settled = execution.terminal_fact
    assert (await poll(4)).facts == ()
    assert execution.terminal_fact == settled
    run.record((validation("passed", 5),))
    assert check_completion(run).validation_outcome == "passed"
    assert (await poll(6)).facts == ()
    assert check_completion(run).validation_outcome == "passed"
