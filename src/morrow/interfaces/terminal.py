"""Thin terminal interface using prompt-toolkit input and Rich output."""

from __future__ import annotations

import asyncio

from prompt_toolkit import PromptSession
from prompt_toolkit.patch_stdout import patch_stdout
from rich.console import Console

from morrow.application.commands import RecoveryCommandRequest
from morrow.application.orchestrator import DispatchResult
from morrow.core.capabilities import CommandToolFact
from morrow.core.models import AgentEvent, ToolApprovalDecision, ToolApprovalRequest
from morrow.core.permissions import UNCONFINED_HOST_APPROVAL_LANGUAGE

_MODEL_WAIT_MESSAGE = "正在连接模型并等待首个响应…（Ctrl+C 取消）"
_MODEL_CONTINUE_MESSAGE = "正在等待模型继续响应…（Ctrl+C 取消）"
_MODEL_RETRY_MESSAGE = "模型暂时不可用，正在重试…（Ctrl+C 取消）"
_STOP_HINTS = {
    "provider_auth": "请检查 API Key 或重新配置 Provider。",
    "provider_network": "请检查网络后重试。",
    "provider_rate_limit": "请稍后重试。",
    "provider_timeout": "可按 Ctrl+C 取消后重试。",
    "run_timeout": "任务超过总运行时间。",
}


class Terminal:
    def __init__(self, console: Console | None = None) -> None:
        self.console = console or Console()
        self._text_open = False
        self._tool_activity = False

    def show_event(self, event) -> None:
        if event.type == "turn.started":
            self._text_open = False
            self._tool_activity = False
            self.console.print(_MODEL_WAIT_MESSAGE)
        elif event.type == "status.changed" and event.payload.get("status") == "retrying":
            if self._text_open:
                self.console.print()
            self.console.print(_MODEL_RETRY_MESSAGE)
            self._text_open = False
        elif event.type == "text.delta":
            self.console.print(event.payload.get("text", ""), end="")
            self._text_open = True
        elif event.type == "tool.status" and event.payload.get("status") == "running":
            if self._text_open:
                self.console.print()
            ordinal = event.payload.get("ordinal", "?")
            total = event.payload.get("total", "?")
            name = str(event.payload.get("name", "tool"))[:64]
            self.console.print(f"↳ 工具步骤 {ordinal}/{total}：{name}")
            self._text_open = False
            self._tool_activity = True
        elif event.type == "tool.status":
            ordinal = event.payload.get("ordinal")
            total = event.payload.get("total")
            if ordinal == total and event.payload.get("status") in {
                "succeeded",
                "failed",
                "cancelled",
                "skipped",
            }:
                if self._text_open:
                    self.console.print()
                self.console.print(_MODEL_CONTINUE_MESSAGE)
                self._text_open = False
        elif event.type == "error":
            if self._text_open:
                self.console.print()
            message = str(event.payload.get("message") or "模型调用失败")
            hint = _STOP_HINTS.get(str(event.payload.get("stop_code") or ""))
            rendered = f"错误：{message}" if not hint else f"错误：{message} {hint}"
            self.console.print(f"\n[red]{rendered}[/red]")
            self._text_open = False
        elif event.type == "turn.completed":
            if self._text_open:
                self.console.print()
            self._text_open = False
            self._tool_activity = False

    def show_run_summary(self, session) -> None:
        """Render one bounded fact summary without exposing Diff or tool payloads."""

        metrics = getattr(session, "latest_metrics", None)
        facts = getattr(session, "latest_tool_facts", ())
        if metrics is None or metrics.tool_calls == 0:
            return
        validation = {
            "not_run": "未运行",
            "passed": "通过",
            "failed": "失败",
            "timeout": "超时",
            "cancelled": "取消",
        }.get(metrics.validation_outcome, "未知")
        markers: list[str] = []
        command_facts = tuple(fact for fact in facts if isinstance(fact, CommandToolFact))
        if any(fact.output_truncated for fact in command_facts):
            markers.append("输出截断")
        if any(fact.redaction_count for fact in command_facts):
            markers.append("输出脱敏")
        suffix = f"；{'、'.join(markers)}" if markers else ""
        line = (
            f"事实摘要：工具 {metrics.tool_calls} 次，成功 {metrics.successful_tool_calls}，"
            f"失败 {metrics.failed_tool_calls}，修改 {metrics.changed_file_count} 个文件，"
            f"验证 {validation}{suffix}"
        )
        self.console.print(line[:200])

    async def prompt(self, session: PromptSession, message: str = "你 > ") -> str:
        return await session.prompt_async(message)


class TerminalApprovalPort:
    """Terminal-only adapter for the generic Core approval boundary."""

    def __init__(self, terminal: Terminal, prompt_session: PromptSession) -> None:
        self.terminal = terminal
        self.prompt_session = prompt_session

    async def request(self, request: ToolApprovalRequest) -> ToolApprovalDecision:
        lines = request.preview or ("未提供额外预览。",)
        self.terminal.console.print("\n".join(lines))
        elevated = any(line.startswith("unconfined_host:") for line in lines)
        if elevated:
            self.terminal.console.print(UNCONFINED_HOST_APPROVAL_LANGUAGE)
        self.terminal.console.print(f"副作用级别：{request.effect.value}")
        if request.approval_id:
            self.terminal.console.print(f"审批编号：{request.approval_id}")
        try:
            prompt = (
                "确认执行这条未受操作系统隔离的 Host 命令？ [y/N] "
                if elevated
                else "确认执行？ [y/N] "
            )
            answer = await self.terminal.prompt(self.prompt_session, prompt)
        except (EOFError, KeyboardInterrupt):
            raise asyncio.CancelledError from None
        return ToolApprovalDecision(approved=answer.strip().casefold() in {"y", "yes", "是"})


async def run_repl(
    orchestrator,
    *,
    session=None,
    terminal: Terminal | None = None,
    prompt_session: PromptSession | None = None,
    resume_current_turn: bool = False,
) -> int:
    terminal = terminal or Terminal()
    prompt_session = prompt_session or PromptSession()
    terminal.console.print("Morrow 承序 · Workspace terminal agent.")
    with patch_stdout():
        if resume_current_turn:
            try:
                async for item in orchestrator.resume_recovery():
                    terminal.show_event(item)
            except (RuntimeError, ValueError) as exc:
                terminal.console.print(f"Recovery 继续失败：{exc}")
            else:
                if session is not None:
                    terminal.show_run_summary(session)
        while True:
            try:
                text = await terminal.prompt(prompt_session)
            except EOFError:
                text = "/exit"
            except KeyboardInterrupt:
                terminal.console.print("\n已取消输入。")
                continue
            if not text.strip():
                continue
            dispatch_task = asyncio.create_task(
                _consume_dispatch(orchestrator, text.strip(), terminal)
            )
            try:
                result = await dispatch_task
            except KeyboardInterrupt:
                dispatch_task.cancel()
                await asyncio.gather(dispatch_task, return_exceptions=True)
                terminal.console.print("\n已取消当前操作。")
                continue
            if result.action == "exit":
                exit_code = await _exit(session, terminal, prompt_session)
                if exit_code is not None:
                    return exit_code
                continue
            if result.action == "new" and session:
                _reset_session(orchestrator)
                terminal.console.print("已切换到新的独立会话。")
            if result.action == "discard_new" and session:
                confirmation = await _confirm(
                    terminal, prompt_session, "确认丢弃当前进程内对话并开始新会话？"
                )
                if confirmation == "closed":
                    return _closed_input(terminal)
                if confirmation != "yes":
                    continue
                _reset_session(orchestrator)
                terminal.console.print("已切换到新的独立会话。")
            if result.action == "resolve_recovery":
                request = result.value
                if not isinstance(request, RecoveryCommandRequest):
                    terminal.console.print("Recovery 请求无效。")
                    continue
                confirmation = await _confirm(
                    terminal,
                    prompt_session,
                    f"确认执行 Recovery {request.resolution.value}？",
                )
                if confirmation == "closed":
                    return _closed_input(terminal)
                if confirmation != "yes":
                    continue
                try:
                    saved = _command_service(orchestrator).resolve_recovery(request)
                except (ValueError, RuntimeError) as exc:
                    terminal.console.print(f"Recovery 处理失败：{exc}")
                    continue
                terminal.console.print(f"Recovery 已处理：{saved.status.value}。")
                if request.resolution.value == "resume":
                    async for item in orchestrator.resume_recovery():
                        terminal.show_event(item)
                    if session is not None:
                        terminal.show_run_summary(session)
            if result.action == "reset_profile":
                confirmation = await _confirm(terminal, prompt_session, "确认重置 Profile？")
                if confirmation == "closed":
                    return _closed_input(terminal)
                if confirmation == "yes":
                    try:
                        reset = _command_service(orchestrator).reset_profile()
                    except (ValueError, RuntimeError) as exc:
                        terminal.console.print(f"Profile 重置失败：{exc}")
                    else:
                        terminal.console.print(
                            "Profile 已重置。"
                            if reset.status.value in {"applied", "unchanged"}
                            else "Profile 重置失败。"
                        )
            if result.action == "reset_config":
                scope = str(result.value)
                confirmation = await _confirm(
                    terminal, prompt_session, f"确认清除 {scope} 层 Preferences？"
                )
                if confirmation == "closed":
                    return _closed_input(terminal)
                if confirmation == "yes":
                    try:
                        reset = _command_service(orchestrator).reset_preferences(scope)
                    except (ValueError, RuntimeError) as exc:
                        terminal.console.print(f"Preferences 重置失败：{exc}")
                    else:
                        ok = reset is True or reset.status.value in {"applied", "unchanged"}
                        terminal.console.print(
                            f"{scope} 层 Preferences 已重置。"
                            if ok
                            else f"{scope} 层 Preferences 重置失败。"
                        )
            if result.action == "config_preview":
                confirmation = await _confirm(terminal, prompt_session, "确认保存这项配置？")
                if confirmation == "closed":
                    return _closed_input(terminal)
                if confirmation == "yes":
                    try:
                        _command_service(orchestrator).config_service.apply(result.value)
                    except (ValueError, RuntimeError) as exc:
                        terminal.console.print(f"配置保存失败：{exc}")
                    else:
                        terminal.console.print("配置已保存。")
            if result.action == "arm_full_access_grant":
                confirmation = await _confirm(
                    terminal,
                    prompt_session,
                    "确认在下一次前台 AgentRun 授予未受操作系统隔离的 Host 权限？",
                )
                if confirmation == "closed":
                    return _closed_input(terminal)
                if confirmation == "yes":
                    try:
                        _command_service(orchestrator).arm_full_access_grant()
                    except (ValueError, RuntimeError) as exc:
                        terminal.console.print(f"权限授予准备失败：{exc}")
                    else:
                        terminal.console.print("已准备下一次前台 AgentRun 的 Host 权限授予。")


async def _consume_dispatch(orchestrator, text: str, terminal: Terminal) -> DispatchResult:
    result = DispatchResult()
    completed = False
    async for item in orchestrator.stream(text):
        if isinstance(item, AgentEvent):
            terminal.show_event(item)
            completed = item.type == "turn.completed"
        else:
            result = item
            for line in result.lines:
                terminal.console.print(line)
    if completed and getattr(orchestrator, "session", None) is not None:
        terminal.show_run_summary(orchestrator.session)
    return result


def _command_service(orchestrator):
    return orchestrator.command_service


def _reset_session(orchestrator) -> None:
    orchestrator.reset_session()


def _closed_input(terminal) -> int:
    terminal.console.print("[yellow]输入已关闭，未执行待确认操作。[/yellow]")
    return 2


async def _confirm(terminal, prompt_session, question: str) -> str:
    try:
        answer = await terminal.prompt(prompt_session, question + " [y/N] ")
    except EOFError:
        return "closed"
    except KeyboardInterrupt:
        return "no"
    return "yes" if answer.strip().casefold() in {"y", "yes", "是"} else "no"


async def _exit(
    session,
    terminal,
    prompt_session,
) -> int | None:
    if session and session.dirty and not getattr(session, "persisted", False):
        confirmation = await _confirm(terminal, prompt_session, "确认退出并丢弃当前内存内容？")
        if confirmation == "closed":
            return _closed_input(terminal)
        if confirmation != "yes":
            return None
    closer = getattr(getattr(session, "committer", None), "close", None)
    if closer is not None:
        closer()
    terminal.console.print("再见。")
    return 0
