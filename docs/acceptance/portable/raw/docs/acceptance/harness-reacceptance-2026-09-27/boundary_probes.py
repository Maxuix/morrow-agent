import time
from types import SimpleNamespace

import pytest

from morrow.adapters.local.process import HostProcessAdapter, _CursorBuffer
from morrow.core.capabilities import PolicyVerdict, ToolRunContext, ValidationFact
from morrow.core.local_tools import TrackedLifecycle
from morrow.runtime.completion_check import check_completion
from morrow.services import bash_execution
from morrow.services.files import WorkspaceFileService, WorkspacePathResolver
from morrow.services.process import PreparedBash, ProcessExecutionService
from morrow.services.tracked_process import TrackedExecution


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "lifecycle,next_task",
    [
        (TrackedLifecycle.TASK, "previous-task"),
        (TrackedLifecycle.ACCEPTANCE, "next-task"),
    ],
)
async def test_running_process_polled_in_later_run_invalidates_validation(
    tmp_path, monkeypatch, lifecycle, next_task
):
    service = ProcessExecutionService(WorkspaceFileService(WorkspacePathResolver(tmp_path)))
    execution_id = "exec_" + "a" * 24
    execution = TrackedExecution(
        execution_id=execution_id,
        session_id="s",
        task_id="previous-task",
        lifecycle=lifecycle,
        command_class="shell",
        cwd_relative=".",
        adapter=HostProcessAdapter(),
        spawned=SimpleNamespace(
            stdout=_CursorBuffer(1024), stderr=_CursorBuffer(1024), started=time.monotonic()
        ),
    )
    service.tracked._items[execution_id] = execution
    monkeypatch.setattr(service.tracked, "_refresh", lambda item: None)
    # Each AgentLoop.run_task gets a fresh ToolRunContext. The registry survives.
    run = ToolRunContext(run_id="next-run", session_id="s", owner_task_id=next_task)
    run.record(
        (
            ValidationFact(
                call_id="validate",
                tool_name="bash",
                ordinal=1,
                approval_verdict=PolicyVerdict.ALLOW,
                relative_paths=(".",),
                validator_kind="pytest",
                scope=".",
                status="passed",
                exit_code=0,
                evidence_summary="exit_zero",
            ),
        )
    )
    result = await bash_execution.run_bash(
        service,
        PreparedBash(action="poll", execution_id=execution_id),
        session_id="s",
        task_id=next_task,
        result_limit=8192,
        run=run,
        call_id="poll",
        tool_name="bash",
        ordinal=2,
        approval_verdict=PolicyVerdict.ALLOW,
    )
    assert result.payload.status.value == "running"
    run.record(result.facts)
    assert check_completion(run).validation_outcome != "passed", (
        "running process visible through poll is missing from the new run facts"
    )
