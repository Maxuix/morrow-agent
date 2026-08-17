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


async def run_repl(
    orchestrator, *, handoff_service=None, project_store=None, workspace_id=None, session=None
) -> int:
    terminal = Terminal()
    prompt_session = PromptSession()
    terminal.console.print("Morrow 承序 · Pick up where you left off.")
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
                exit_code = await _exit(
                    orchestrator,
                    handoff_service,
                    project_store,
                    workspace_id,
                    session,
                    terminal,
                    prompt_session,
                )
                if exit_code is not None:
                    return exit_code
                continue
            if result.action == "new" and session:
                _reset_session(orchestrator)
                terminal.console.print("已切换到新的独立会话。")
            if result.action in {"switch_new", "switch_continue"} and session:
                should_switch = await _save_or_discard_before_switch(
                    terminal, prompt_session, handoff_service, project_store, workspace_id, session
                )
                if should_switch == "closed":
                    return _closed_input(terminal)
                if should_switch not in {"saved", "discarded"}:
                    continue
                _reset_session(orchestrator)
                if result.action == "switch_continue" and project_store and workspace_id:
                    loaded = _load_handoff(orchestrator, project_store, workspace_id, session)
                    if loaded.value:
                        terminal.console.print(f"已加载交接 revision {loaded.revision}。")
                else:
                    terminal.console.print("已切换到新的独立会话。")
            if result.action == "continue" and project_store and workspace_id:
                loaded = _load_handoff(orchestrator, project_store, workspace_id, session)
                if loaded.value:
                    terminal.console.print(f"已加载交接 revision {loaded.revision}。")
            if result.action == "update_handoff" and handoff_service and project_store:
                try:
                    saved, degraded = await _await_cancellable(
                        handoff_service.generate_and_publish(
                            session,
                            expected_revision=_handoff_revision(
                                orchestrator, project_store, workspace_id
                            ),
                        )
                    )
                    if saved.status.value == "ok":
                        terminal.console.print(
                            "已更新交接。" + ("（降级版本）" if degraded else "")
                        )
                    else:
                        terminal.console.print("交接保存失败，旧版本保持不变。")
                except asyncio.CancelledError:
                    terminal.console.print("已取消交接更新，未写入状态。")
            if result.action == "clear_handoff" and project_store:
                confirmation = await _confirm(terminal, prompt_session, "确认清除当前 Handoff？")
                if confirmation == "closed":
                    return _closed_input(terminal)
                if confirmation == "yes":
                    cleared = _command_service(orchestrator).clear_handoff()
                    if cleared.status.value == "ok":
                        terminal.console.print("Handoff 已清除。")
            if result.action == "reset_profile" and project_store:
                confirmation = await _confirm(terminal, prompt_session, "确认重置 Profile？")
                if confirmation == "closed":
                    return _closed_input(terminal)
                if confirmation == "yes":
                    reset = _command_service(orchestrator).reset_profile()
                    terminal.console.print(
                        "Profile 已重置。" if reset.status.value == "ok" else "Profile 重置失败。"
                    )
            if result.action == "reset_config" and project_store:
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


def _load_handoff(orchestrator, project_store, workspace_id, session):
    service = getattr(orchestrator, "command_service", None)
    if service and hasattr(service, "load_handoff"):
        return service.load_handoff()
    loaded = project_store.load_handoff(workspace_id)
    if loaded.value:
        session.loaded_handoff = loaded.value.handoff
        session.handoff_source_revision = loaded.revision
    return loaded


def _handoff_revision(orchestrator, project_store, workspace_id) -> int:
    service = getattr(orchestrator, "command_service", None) if orchestrator else None
    if service and hasattr(service, "handoff_revision"):
        return service.handoff_revision()
    return project_store.load_handoff(workspace_id).revision


async def _await_cancellable(awaitable):
    task = asyncio.create_task(awaitable)
    try:
        return await task
    except KeyboardInterrupt:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        raise asyncio.CancelledError from None


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


async def _save_or_discard_before_switch(
    terminal, prompt_session, handoff_service, project_store, workspace_id, session
) -> str:
    if not session.dirty:
        return "saved"
    if session.read_only:
        try:
            answer = await terminal.prompt(
                prompt_session, "工作空间状态只读：丢弃当前内存 / 取消？[d/c] "
            )
        except EOFError:
            return "closed"
        except KeyboardInterrupt:
            return "cancelled"
        return "discarded" if answer.strip().casefold() in {"d", "discard", "丢弃"} else "cancelled"
    if session.is_continuation and handoff_service and project_store:
        try:
            saved, _ = await _await_cancellable(
                handoff_service.generate_and_publish(
                    session,
                    expected_revision=_handoff_revision(None, project_store, workspace_id),
                )
            )
        except asyncio.CancelledError:
            terminal.console.print("保存已取消，保持当前会话。")
            return "cancelled"
        except Exception as exc:
            terminal.console.print(f"保存失败，保持当前会话（{type(exc).__name__}）。")
            return "cancelled"
        if saved.status.value != "ok":
            terminal.console.print("保存失败，保持当前会话。")
            return "cancelled"
        return "saved"
    try:
        answer = await terminal.prompt(prompt_session, "独立会话：保存 / 丢弃 / 取消？[s/d/c] ")
    except EOFError:
        return "closed"
    except KeyboardInterrupt:
        return "cancelled"
    choice = answer.strip().casefold()
    if choice in {"c", "cancel", "取消", ""}:
        return "cancelled"
    if choice in {"d", "discard", "丢弃"}:
        return "discarded"
    if choice in {"s", "save", "保存"} and handoff_service and project_store:
        try:
            saved, _ = await _await_cancellable(
                handoff_service.generate_and_publish(
                    session,
                    expected_revision=_handoff_revision(None, project_store, workspace_id),
                )
            )
        except asyncio.CancelledError as exc:
            terminal.console.print(f"保存失败，保持当前会话（{type(exc).__name__}）。")
            return "cancelled"
        except Exception as exc:
            terminal.console.print(f"保存失败，保持当前会话（{type(exc).__name__}）。")
            return "cancelled"
        if saved.status.value == "ok":
            return "saved"
        terminal.console.print("保存失败，保持当前会话。")
        return "cancelled"
    return "cancelled"


async def _exit(
    orchestrator,
    handoff_service,
    project_store,
    workspace_id,
    session,
    terminal,
    prompt_session,
) -> int | None:
    if session and session.dirty and not session.is_continuation:
        terminal.console.print("独立会话的内容不会自动覆盖旧 Handoff。")
        confirmation = await _confirm(terminal, prompt_session, "确认退出并丢弃当前内存内容？")
        if confirmation == "closed":
            return _closed_input(terminal)
        if confirmation != "yes":
            return None
    if handoff_service and session and session.is_continuation and session.dirty:
        if session.read_only:
            terminal.console.print("[yellow]工作空间状态只读，未尝试保存交接。[/yellow]")
            return 2
        try:
            result, degraded = await _await_cancellable(
                handoff_service.generate_and_publish(
                    session,
                    expected_revision=_handoff_revision(orchestrator, project_store, workspace_id),
                )
            )
        except asyncio.CancelledError:
            terminal.console.print("退出交接已取消，仍保留当前会话。")
            return None
        if result.status.value != "ok":
            terminal.console.print("[red]交接保存失败，旧交接保持不变。[/red]")
            return 2
        if degraded:
            terminal.console.print("[yellow]已使用确定性降级交接保存。[/yellow]")
    terminal.console.print("再见。")
    return 0
