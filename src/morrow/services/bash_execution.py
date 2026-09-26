"""Run one already-admitted bash call.

Foreground uses the bounded command plan. Start, poll, and stop talk to the
tracked registry. Poll and stop never receive a command.
"""

from __future__ import annotations

from morrow.adapters.local.process import HostProcessAdapter, ProcessAdapterError
from morrow.core.capabilities import OperationIntent, OperationKind
from morrow.core.models import ToolEffect
from morrow.services.process import (
    BashExecution,
    PreparedBash,
    ProcessExecutionService,
    ProcessServiceError,
)
from morrow.services.tracked_process import TrackedProcessError


def tracked_intent(service: ProcessExecutionService) -> OperationIntent:
    """Approval intent for poll and stop. No command is prepared."""

    return OperationIntent(
        kind=OperationKind.PROCESS,
        effect=ToolEffect.NONE,
        relative_paths=(".",),
        command_class="shell",
        requires_host=service.requires_host,
        requires_sandbox=service.requires_sandbox,
        preview_summary=("已跟踪进程", "不会启动新进程"),
    )


async def run_bash(
    service: ProcessExecutionService,
    prepared: PreparedBash,
    *,
    session_id: str,
    task_id: str | None,
    result_limit: int,
    run,
    call_id: str,
    tool_name: str,
    ordinal: int,
    approval_verdict,
    truncation_max_bytes: int | None = None,
    truncation_max_lines: int | None = None,
    output_listener=None,
) -> BashExecution:
    if prepared.action == "foreground":
        if prepared.plan is None:
            raise ProcessServiceError("invalid_mode", "前台命令缺少执行计划")
        result, fact, artifact = await service.execute_with_artifact(
            prepared.plan,
            result_limit=result_limit,
            run=run,
            call_id=call_id,
            tool_name=tool_name,
            ordinal=ordinal,
            approval_verdict=approval_verdict,
            truncation_max_bytes=truncation_max_bytes,
            truncation_max_lines=truncation_max_lines,
            output_listener=output_listener,
        )
        validation = service.validation_fact(
            prepared.plan,
            result,
            call_id=call_id,
            tool_name=tool_name,
            ordinal=ordinal,
            approval_verdict=approval_verdict,
        )
        facts = (fact,) if validation is None else (fact, validation)
        return BashExecution(payload=result, facts=facts, artifact=artifact)
    if not task_id:
        raise ProcessServiceError(
            "invalid_mode",
            "当前没有任务，不能启动、查询或停止已跟踪进程。",
        )
    if prepared.action == "start":
        execution_id = await _start(service, prepared, session_id=session_id, task_id=task_id)
        return BashExecution(
            payload=_read(
                service,
                execution_id,
                session_id=session_id,
                task_id=task_id,
                offset=0,
                stderr_offset=0,
                limit=result_limit,
            )
        )
    if prepared.execution_id is None:
        raise ProcessServiceError(
            "invalid_execution",
            "缺少执行编号。poll 和 stop 不会启动新进程。",
        )
    if prepared.action == "poll":
        return BashExecution(
            payload=_read(
                service,
                prepared.execution_id,
                session_id=session_id,
                task_id=task_id,
                offset=prepared.output_offset,
                stderr_offset=prepared.stderr_offset,
                limit=result_limit,
            )
        )
    if prepared.action == "stop":
        return BashExecution(
            payload=await _stop(
                service,
                prepared.execution_id,
                session_id=session_id,
                task_id=task_id,
                offset=prepared.output_offset,
                stderr_offset=prepared.stderr_offset,
                limit=result_limit,
            )
        )
    raise ProcessServiceError("invalid_mode", "未知的 bash 操作")


async def _start(
    service: ProcessExecutionService, prepared: PreparedBash, *, session_id: str, task_id: str
) -> str:
    if service.requires_sandbox:
        raise ProcessServiceError(
            "unsupported_capability",
            "沙箱进程在命令返回后会清理临时快照，不能把长命令或服务留在后台。"
            "请在宿主进程上使用 mode=start。",
        )
    plan = prepared.plan
    if plan is None:
        raise ProcessServiceError("invalid_mode", "后台启动缺少执行计划")
    if not isinstance(service.adapter, HostProcessAdapter):
        raise ProcessServiceError(
            "unsupported_capability",
            "当前进程适配器不能在工具返回后保留进程。",
        )
    try:
        execution = await service.tracked.start(
            service.adapter,
            argv=plan.argv,
            shell=plan.shell,
            cwd=plan.cwd,
            environment=service._minimal_environment(),
            session_id=session_id,
            task_id=task_id,
            lifecycle=prepared.lifecycle,
            command_class=plan.command_class,
            cwd_relative=plan.cwd_relative,
        )
    except ProcessAdapterError as exc:
        raise ProcessServiceError(exc.code, exc.message) from exc
    except TrackedProcessError as exc:
        raise ProcessServiceError(exc.code, exc.message) from exc
    return execution.execution_id


def _read(
    service: ProcessExecutionService,
    execution_id: str,
    *,
    session_id: str,
    task_id: str,
    offset: int,
    stderr_offset: int,
    limit: int,
):
    try:
        return service.tracked.read(
            execution_id,
            session_id=session_id,
            task_id=task_id,
            offset=offset,
            stderr_offset=stderr_offset,
            limit=limit,
            redactor=service.redactor,
        )
    except TrackedProcessError as exc:
        raise ProcessServiceError(exc.code, exc.message) from exc


async def _stop(
    service: ProcessExecutionService,
    execution_id: str,
    *,
    session_id: str,
    task_id: str,
    offset: int,
    stderr_offset: int,
    limit: int,
):
    try:
        return await service.tracked.stop(
            execution_id,
            session_id=session_id,
            task_id=task_id,
            offset=offset,
            stderr_offset=stderr_offset,
            limit=limit,
            redactor=service.redactor,
        )
    except TrackedProcessError as exc:
        raise ProcessServiceError(exc.code, exc.message) from exc
