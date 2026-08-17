"""Thin terminal interface using prompt-toolkit input and Rich output."""

from __future__ import annotations

import asyncio

from prompt_toolkit import PromptSession
from prompt_toolkit.patch_stdout import patch_stdout
from rich.console import Console

from morrow.application.orchestrator import DispatchResult
from morrow.core.models import AgentEvent


class Terminal:
    def __init__(self, console: Console | None = None) -> None:
        self.console = console or Console()
        self._text_open = False
        self._tool_activity = False

    def show_event(self, event) -> None:
        if event.type == "turn.started":
            self._text_open = False
            self._tool_activity = False
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
        elif event.type == "error":
            if self._text_open:
                self.console.print()
            self.console.print(f"\n[red]错误：{event.payload.get('message', '模型调用失败')}[/red]")
            self._text_open = False
        elif event.type == "turn.completed":
            if self._text_open:
                self.console.print()
            self._text_open = False
            self._tool_activity = False

    async def prompt(self, session: PromptSession, message: str = "你 > ") -> str:
        return await session.prompt_async(message)


async def run_repl(orchestrator, *, session=None) -> int:
    terminal = Terminal()
    prompt_session = PromptSession()
    terminal.console.print("Morrow 承序 · Workspace terminal agent.")
    with patch_stdout():
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
            if result.action == "reset_profile":
                confirmation = await _confirm(terminal, prompt_session, "确认重置 Profile？")
                if confirmation == "closed":
                    return _closed_input(terminal)
                if confirmation == "yes":
                    reset = _command_service(orchestrator).reset_profile()
                    terminal.console.print(
                        "Profile 已重置。" if reset.status.value == "ok" else "Profile 重置失败。"
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
                        ok = reset is True or reset.status.value == "ok"
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


async def _consume_dispatch(orchestrator, text: str, terminal: Terminal) -> DispatchResult:
    result = DispatchResult()
    async for item in orchestrator.stream(text):
        if isinstance(item, AgentEvent):
            terminal.show_event(item)
        else:
            result = item
            for line in result.lines:
                terminal.console.print(line)
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
    if session and session.dirty:
        confirmation = await _confirm(terminal, prompt_session, "确认退出并丢弃当前内存内容？")
        if confirmation == "closed":
            return _closed_input(terminal)
        if confirmation != "yes":
            return None
    terminal.console.print("再见。")
    return 0
