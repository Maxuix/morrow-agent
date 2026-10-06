"""A new run's empty fact list is not proof of an unchanged workspace."""

import time
from types import SimpleNamespace

import pytest

from morrow.adapters.local.process import HostProcessAdapter, _CursorBuffer
from morrow.core.capabilities import ChangeToolFact, CommandToolFact, PolicyVerdict, ToolRunContext
from morrow.core.local_tools import TrackedCommandStatus, TrackedLifecycle
from morrow.runtime.completion_check import check_completion
from morrow.services import bash_execution
from morrow.services.files import WorkspaceFileService, WorkspacePathResolver
from morrow.services.process import PreparedBash, ProcessExecutionService
from morrow.services.tracked_process import TrackedExecution


@pytest.mark.asyncio
@pytest.mark.parametrize("settled_before_change", [False, True])
async def test_empty_later_run_does_not_revive_old_pass(
    tmp_path, monkeypatch, settled_before_change
):
    service = ProcessExecutionService(WorkspaceFileService(WorkspacePathResolver(tmp_path)))
    execution_id = "exec_" + "a" * 24
    execution = TrackedExecution(
        execution_id=execution_id,
        session_id="s",
        task_id="task",
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
    old = ToolRunContext(run_id="old-run", session_id="s", owner_task_id="task")
    old.record(
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

    async def poll(run):
        result = await bash_execution.run_bash(
            service,
            PreparedBash(action="poll", execution_id=execution_id),
            session_id="s",
            task_id="task",
            result_limit=8192,
            run=run,
            call_id="poll-" + run.run_id,
            tool_name="bash",
            ordinal=2,
            approval_verdict=PolicyVerdict.ALLOW,
        )
        run.record(result.facts)
        return result

    if settled_before_change:
        await poll(old)
        assert check_completion(old).validation_outcome == "passed"
    # A real modification is represented in the run where it occurred.
    old.record(
        (
            ChangeToolFact(
                call_id="write",
                tool_name="write",
                ordinal=3,
                approval_verdict=PolicyVerdict.ALLOW,
                relative_paths=("src/main.py",),
                operation="write",
                status="succeeded",
                changed_lines=1,
                changed_bytes=10,
            ),
        )
    )
    assert check_completion(old).validation_outcome != "passed"
    # AgentLoop gives the next run a fresh ToolRunContext; workspace and registry persist.
    later = ToolRunContext(run_id="later-run", session_id="s", owner_task_id="task")
    result = await poll(later)
    assert result.payload.status is TrackedCommandStatus.EXITED
    assert check_completion(later).validation_outcome != "passed", (
        "new run forgot the prior change and promoted old successful execution to fresh proof"
    )
