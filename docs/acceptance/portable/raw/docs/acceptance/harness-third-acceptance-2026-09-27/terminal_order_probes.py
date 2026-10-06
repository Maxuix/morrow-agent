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
@pytest.mark.parametrize("later_fact", ["change", "failed_validation"])
async def test_historical_pass_does_not_validate_later_changes(tmp_path, monkeypatch, later_fact):
    service = ProcessExecutionService(WorkspaceFileService(WorkspacePathResolver(tmp_path)))
    execution_id = "exec_" + "a" * 24
    execution = TrackedExecution(
        execution_id=execution_id,
        session_id="s",
        task_id="old-task",
        lifecycle=TrackedLifecycle.ACCEPTANCE,
        command_class="shell",
        cwd_relative=".",
        adapter=HostProcessAdapter(),
        started_run_id="old-run",
        spawned=SimpleNamespace(
            stdout=_CursorBuffer(1024), stderr=_CursorBuffer(1024), started=time.monotonic()
        ),
        status=TrackedCommandStatus.EXITED,
        exit_code=0,
        validation_kind="pytest",
        validation_scope=".",
    )
    service.tracked._items[execution_id] = execution
    monkeypatch.setattr(service.tracked, "_refresh", lambda item: None)

    async def poll(run, task_id):
        result = await bash_execution.run_bash(
            service,
            PreparedBash(action="poll", execution_id=execution_id),
            session_id="s",
            task_id=task_id,
            result_limit=8192,
            run=run,
            call_id="poll-" + run.run_id,
            tool_name="bash",
            ordinal=2,
            approval_verdict=PolicyVerdict.ALLOW,
        )
        run.record(result.facts)

    old = ToolRunContext(run_id="old-run", session_id="s", owner_task_id="old-task")
    # The old run started the process: its in-run validity needs the real
    # origin and the start observation, not an unknown-source empty chain.
    old.record(
        (
            CommandToolFact(
                call_id="start",
                tool_name="bash",
                ordinal=1,
                approval_verdict=PolicyVerdict.ALLOW,
                relative_paths=(".",),
                command_class="shell",
                status="running",
                duration_ms=0,
                execution_id=execution_id,
            ),
        )
    )
    await poll(old, "old-task")
    assert check_completion(old).validation_outcome == "passed"
    new = ToolRunContext(run_id="new-run", session_id="s", owner_task_id="new-task")
    change = ChangeToolFact(
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
    failure = ValidationFact(
        call_id="new-test",
        tool_name="bash",
        ordinal=1,
        approval_verdict=PolicyVerdict.ALLOW,
        relative_paths=(".",),
        validator_kind="pytest",
        scope=".",
        status="failed",
        exit_code=1,
        evidence_summary="exit_nonzero",
    )
    new.record((change if later_fact == "change" else failure,))
    await poll(new, "new-task")
    assert check_completion(new).validation_outcome != "passed", (
        "poll replayed a previous run passing validation as proof of subsequently changed files"
    )
