"""Bash tool surface: schema, admission, and one execution call.

Poll and stop never become command plans. Only foreground and start are preflighted.
"""

from __future__ import annotations

import math
from typing import Literal

from pydantic import model_validator

from morrow.adapters.local.shell import PinnedShell
from morrow.application.local_tools import _CompatibilityArguments, _tool_error
from morrow.core.capabilities import (
    OperationIntent,
    ProcessIsolation,
    ToolCallContext,
    ToolHandlerOutcome,
)
from morrow.core.execution import tool_declaration
from morrow.core.local_tools import CommandRequest, TrackedLifecycle
from morrow.core.runtime_policy import (
    FOREGROUND_COMMAND_MAX_SECONDS,
    FOREGROUND_COMMAND_MIN_SECONDS,
)
from morrow.runtime.tool_arguments import CuratedArgumentError
from morrow.runtime.tool_output import current_output_listener
from morrow.runtime.tools import (
    ApprovalPreviewBudget,
    RegisteredTool,
    ToolErrorCode,
    ToolExecutionError,
    make_tool,
)
from morrow.services.bash_execution import run_bash, tracked_intent
from morrow.services.files import LocalFileError
from morrow.services.process import (
    PreparedBash,
    ProcessExecutionService,
    ProcessServiceError,
    foreground_timeout_message,
)

_NO_TASK = "当前没有任务，不能启动、查询或停止已跟踪进程。"
_MISSING_COMMAND = "前台和 start 需要 command。poll 和 stop 不使用 command。"
_MISSING_EXECUTION = "poll 和 stop 需要 execution_id，并且不会启动新进程。"
_TIMEOUT_ONLY_FOREGROUND = (
    "只有前台命令接受 timeout。mode=start 会一直运行，直到 mode=stop、"
    "用户取消或任务终止；lifecycle=acceptance 在任务验收后仍保持运行。"
)
_LIFECYCLE_ONLY_START = "lifecycle=acceptance 只能用于 mode=start。"


class BashArguments(_CompatibilityArguments):
    command: str = ""
    timeout: float | None = None
    mode: Literal["foreground", "start", "poll", "stop"] = "foreground"
    execution_id: str | None = None
    offset: int | None = None
    stderr_offset: int | None = None
    lifecycle: Literal["task", "acceptance"] = "task"

    @model_validator(mode="after")
    def fields_match_mode(self) -> BashArguments:
        if self.mode in {"foreground", "start"}:
            if not self.command.strip():
                raise CuratedArgumentError("missing_command", _MISSING_COMMAND)
        else:
            if not self.execution_id:
                raise CuratedArgumentError("missing_execution", _MISSING_EXECUTION)
        if self.mode != "foreground" and self.timeout is not None:
            raise CuratedArgumentError("timeout_not_applicable", _TIMEOUT_ONLY_FOREGROUND)
        if self.lifecycle != "task" and self.mode != "start":
            raise CuratedArgumentError("lifecycle_not_applicable", _LIFECYCLE_ONLY_START)
        for name, value in (("offset", self.offset), ("stderr_offset", self.stderr_offset)):
            if value is not None and (isinstance(value, bool) or value < 0):
                raise CuratedArgumentError("invalid_offset", f"{name} 不是有效的输出读取位置。")
        return self


def _object_schema(properties: dict[str, object], *, required: tuple[str, ...] = ()) -> dict:
    schema: dict[str, object] = {"type": "object", "properties": properties}
    if required:
        schema["required"] = list(required)
    return schema


def _timeout_property(maximum: float) -> dict[str, object]:
    rendered = f"{maximum:g}"
    floor = f"{FOREGROUND_COMMAND_MIN_SECONDS:g}"
    return {
        "type": "number",
        "minimum": FOREGROUND_COMMAND_MIN_SECONDS,
        "maximum": maximum,
        "description": (
            f"Foreground timeout in seconds. Minimum {floor}, maximum {rendered}. "
            "Values outside this range are rejected and are not reduced."
        ),
    }


def _bash_command_schema(*, bash: bool, maximum: float) -> dict[str, object]:
    if bash:
        command = {
            "type": "string",
            "description": (
                "Bash command to execute for foreground or start. Omit it for poll and stop."
            ),
        }
    else:
        command = {
            "type": "string",
            "description": (
                "POSIX sh command for foreground or start. Bash-only syntax is not available. "
                "Omit it for poll and stop."
            ),
        }
    return _object_schema(
        {
            "command": command,
            "timeout": _timeout_property(maximum),
            "mode": {
                "type": "string",
                "enum": ["foreground", "start", "poll", "stop"],
                "description": (
                    "foreground runs until exit. start, poll, and stop are the long-command "
                    "lifecycle. poll and stop use execution_id and do not run command."
                ),
            },
            "execution_id": {
                "type": "string",
                "description": "Stable id returned by mode=start. Required for poll and stop.",
            },
            "offset": {
                "type": "integer",
                "minimum": 0,
                "description": "Stdout byte offset for mode=poll. Omit to read from the start.",
            },
            "stderr_offset": {
                "type": "integer",
                "minimum": 0,
                "description": "Stderr byte offset for mode=poll.",
            },
            "lifecycle": {
                "type": "string",
                "enum": ["task", "acceptance"],
                "description": (
                    "For mode=start. task stops when the task is cancelled, fails, or is "
                    "accepted. acceptance keeps running after final acceptance."
                ),
            },
        }
    )


def _lifecycle_rules(shell: PinnedShell, maximum: float) -> str:
    if shell.path:
        shell_line = f" Shell: {shell.path} ({shell.version or shell.family})."
    else:
        shell_line = " No shell is available."
    floor = f"{FOREGROUND_COMMAND_MIN_SECONDS:g}"
    rendered = f"{maximum:g}"
    ceiling = f"{FOREGROUND_COMMAND_MAX_SECONDS:g}"
    return (
        " Each call starts in the workspace root. cd and environment assignments do not "
        f"persist across calls; the process gets a minimal environment and closed stdin, "
        f"with no terminal.{shell_line}"
        f" Foreground timeout is {floor}–{rendered} seconds"
        f" (this run's tool limit; the code ceiling is {ceiling})."
        " A value outside that range is rejected and is not reduced."
        " Long work uses mode=start, then mode=poll and mode=stop with the returned "
        "execution_id. poll reads stdout from offset and stderr from stderr_offset and "
        "does not start a process. poll and stop do not read command."
        " The execution belongs to the current task and session."
        " User cancel, task failure, and task abandon stop owned processes and leave "
        "other tasks alone."
        " lifecycle=acceptance stays running after the task is accepted; it is not killed "
        "when start returns or when the final answer is delivered. Stop it with mode=stop."
        " Foreground commands still reap their process group on exit, so a backgrounded "
        "child in a foreground command does not survive."
        " A restarted process does not adopt or signal old process ids; poll of an unknown "
        "id does not start a process."
    )


_BASH_TOOL_DESCRIPTION = (
    "Run a shell command in the workspace and return stdout, stderr, and status."
)
_POSIX_SH_TOOL_DESCRIPTION = (
    "Run a POSIX sh command in the workspace and return stdout, stderr, and status. "
    "Bash-only syntax is not available. A non-zero exit_code means the shell started "
    "and the script failed; invalid_command means the process was not started."
)
BASH_PROVIDER_SCHEMA = _bash_command_schema(bash=True, maximum=FOREGROUND_COMMAND_MAX_SECONDS)
_POSIX_SH_SCHEMA = _bash_command_schema(bash=False, maximum=FOREGROUND_COMMAND_MAX_SECONDS)


def bash_provider_schema(
    shell: PinnedShell, *, foreground_max: float = FOREGROUND_COMMAND_MAX_SECONDS
) -> dict[str, object]:
    """Schema whose command wording and timeout maximum match this run."""

    bash = shell.family == "bash" and shell.available
    if foreground_max == FOREGROUND_COMMAND_MAX_SECONDS:
        return BASH_PROVIDER_SCHEMA if bash else _POSIX_SH_SCHEMA
    return _bash_command_schema(bash=bash, maximum=foreground_max)


def bash_tool_description(
    shell: PinnedShell, *, foreground_max: float = FOREGROUND_COMMAND_MAX_SECONDS
) -> str:
    base = (
        _BASH_TOOL_DESCRIPTION
        if shell.family == "bash" and shell.available
        else _POSIX_SH_TOOL_DESCRIPTION
    )
    return base + _lifecycle_rules(shell, foreground_max)


def _admitted_timeout(process: ProcessExecutionService, value: float | None) -> float:
    maximum = process.foreground_timeout_seconds
    if value is None:
        return maximum
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value < FOREGROUND_COMMAND_MIN_SECONDS
        or value > maximum
    ):
        raise ToolExecutionError(
            ToolErrorCode.INVALID_ARGUMENTS,
            foreground_timeout_message(maximum),
        )
    return float(value)


def prepare_bash(process: ProcessExecutionService, arguments: BashArguments) -> PreparedBash:
    """Admit one call. Poll and stop stop here, before any command preflight."""

    if arguments.mode in {"poll", "stop"}:
        return PreparedBash(
            action=arguments.mode,
            execution_id=arguments.execution_id,
            output_offset=arguments.offset or 0,
            stderr_offset=arguments.stderr_offset or 0,
        )
    timeout = (
        process.foreground_timeout_seconds
        if arguments.mode == "start"
        else _admitted_timeout(process, arguments.timeout)
    )
    plan = process.preflight(
        CommandRequest(shell=arguments.command, cwd=".", timeout_seconds=timeout)
    )
    if arguments.mode == "start":
        return PreparedBash(
            action="start",
            plan=plan,
            lifecycle=TrackedLifecycle(arguments.lifecycle),
        )
    return PreparedBash(action="foreground", plan=plan)


def _preview(process: ProcessExecutionService, prepared: PreparedBash) -> tuple[str, ...]:
    if prepared.action == "poll":
        return (
            f"轮询：{prepared.execution_id}",
            "不会启动新进程",
            f"stdout 位置：{prepared.output_offset}",
            f"stderr 位置：{prepared.stderr_offset}",
        )
    if prepared.action == "stop":
        return (f"停止：{prepared.execution_id}", "只停止这个执行所属的进程组")
    plan = prepared.plan
    if plan is None:
        return ("无法生成宿主命令预览",)
    lines = [
        f"命令：{process.approval_command(plan)}",
        f"命令类别：{plan.command_class}",
    ]
    if plan.shell is not None and process.shell.path:
        version = process.shell.version or process.shell.family
        lines.append(f"Shell：{process.shell.path} {version}")
    lines.append(f"工作目录：{plan.cwd_relative}")
    if prepared.action == "start":
        lines.append(
            "验收后保持运行"
            if prepared.lifecycle is TrackedLifecycle.ACCEPTANCE
            else "归属当前任务；任务取消、失败或验收时停止"
        )
        lines.append("无前台超时；进程在本次工具返回后继续运行")
    else:
        lines.append(f"超时上限：{plan.request.timeout_seconds:g} 秒")
    lines.append(
        "原生沙箱进程（临时快照）；真实工作空间不会以可写方式暴露"
        if process.requires_sandbox
        else "非沙箱宿主进程；批准后可能访问工作空间外文件或网络"
    )
    return tuple(lines)


COMMAND_PREVIEW_BUDGET = ApprovalPreviewBudget(
    max_lines=8,
    max_line_chars=200,
    max_bytes=1600,
)


def make_bash_tool(process: ProcessExecutionService) -> RegisteredTool:
    """Expose one command string. Tracked calls do not pass through command preflight."""

    def resolve(arguments: BashArguments, context: ToolCallContext) -> OperationIntent:
        if arguments.mode != "foreground" and not context.run.owner_task_id:
            raise ToolExecutionError(ToolErrorCode.INVALID_ARGUMENTS, _NO_TASK)
        try:
            prepared = prepare_bash(process, arguments)
        except (LocalFileError, ProcessServiceError) as exc:
            raise _tool_error(exc) from exc
        except ValueError:
            raise ToolExecutionError(
                ToolErrorCode.INVALID_ARGUMENTS,
                "命令未启动，进程没有执行。命令参数超出执行端边界",
            ) from None
        process.cache_bash(context.run.run_id, context.call_id, prepared)
        if prepared.plan is not None:
            return process.intent(prepared.plan)
        return tracked_intent(process)

    def preview(arguments: BashArguments, context: ToolCallContext) -> tuple[str, ...]:
        del arguments
        prepared = process.cached_bash(context.run.run_id, context.call_id)
        if prepared is None:
            return ("无法生成宿主命令预览",)
        return _preview(process, prepared)

    async def handler(arguments: BashArguments, context: ToolCallContext):
        del arguments
        prepared = process.cached_bash(context.run.run_id, context.call_id)
        if prepared is None:
            raise ToolExecutionError(ToolErrorCode.PREFLIGHT_FAILED, "命令预检不存在")
        try:
            done = await run_bash(
                process,
                prepared,
                session_id=context.run.session_id,
                task_id=context.run.owner_task_id,
                result_limit=context.result_limit,
                run=context.run,
                call_id=context.call_id,
                tool_name=context.tool_name,
                ordinal=context.ordinal,
                approval_verdict=context.approval_verdict,
                truncation_max_bytes=context.truncation_max_bytes,
                truncation_max_lines=context.truncation_max_lines,
                output_listener=current_output_listener(),
            )
        except ProcessServiceError as exc:
            raise _tool_error(exc) from exc
        return ToolHandlerOutcome(
            payload=done.payload.model_dump(mode="json"),
            facts=done.facts,
            artifact_content=done.artifact,
        )

    return make_tool(
        name="bash",
        description=bash_tool_description(
            process.shell, foreground_max=process.foreground_timeout_seconds
        ),
        arguments_model=BashArguments,
        provider_schema=bash_provider_schema(
            process.shell, foreground_max=process.foreground_timeout_seconds
        ),
        handler=handler,
        context_handler=handler,
        intent_resolver=resolve,
        context_approval_preview=preview,
        context_cleanup=lambda context: process.discard_bash(context.run.run_id, context.call_id),
        approval_preview_budget=COMMAND_PREVIEW_BUDGET,
        recovery_declaration=tool_declaration(
            "bash",
            process_isolation=(
                ProcessIsolation.NATIVE_SANDBOX
                if process.requires_sandbox
                else ProcessIsolation.HOST
            ),
        ),
    )
