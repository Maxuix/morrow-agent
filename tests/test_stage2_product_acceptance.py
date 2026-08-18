"""Final product-boundary acceptance through the real terminal orchestration path."""

from __future__ import annotations

from contextlib import nullcontext
from io import StringIO

import pytest
from rich.console import Console

from morrow.adapters.credentials.keyring import MemoryCredentialStore
from morrow.bootstrap import build_application, build_session_application
from morrow.core.models import AgentEvent, AssistantMessage, FunctionToolCall, ModelRef
from morrow.interfaces import terminal as terminal_module
from morrow.testing import ScriptedModelProvider


class _PromptingTerminal(terminal_module.Terminal):
    def __init__(self, inputs, console):
        super().__init__(console=console)
        self.inputs = iter(inputs)
        self.prompt_messages = []
        self.prompt_sessions = []

    async def prompt(self, session, message="你 > "):
        self.prompt_sessions.append(session)
        self.prompt_messages.append(message)
        return next(self.inputs)


class _RecordingOrchestrator:
    def __init__(self, wrapped):
        self.wrapped = wrapped
        self.events = []

    async def stream(self, text):
        async for item in self.wrapped.stream(text):
            if isinstance(item, AgentEvent):
                self.events.append(item)
            yield item

    def __getattr__(self, name):
        return getattr(self.wrapped, name)


@pytest.mark.asyncio
async def test_real_terminal_product_flow_is_ordered_recoverable_and_secret_safe(
    tmp_path, monkeypatch
):
    credentials = MemoryCredentialStore()
    credentials.set("acceptance", "credential-sentinel")
    app = build_application(state_root=tmp_path / "state", credentials=credentials)
    project = tmp_path / "project"
    project.mkdir()
    identity = app.workspace_service.confirm(app.workspace_service.resolve(project))
    provider = ScriptedModelProvider(
        [
            AssistantMessage(
                content="先查",
                tool_calls=(
                    FunctionToolCall(
                        id="secret-call-id",
                        name="lookup_record",
                        arguments=('{"dataset":"plans","key":"argument-sentinel"}'),
                    ),
                ),
            ),
            AssistantMessage(content="工具错误后恢复。"),
            AssistantMessage(content="后续对话正常。"),
        ]
    )
    session_app = build_session_application(
        app,
        identity,
        provider=provider,
        model=ModelRef(provider_id="p", model_id="m"),
    )
    recording = _RecordingOrchestrator(session_app.orchestrator)
    output = StringIO()
    terminal = _PromptingTerminal(
        ["请查询不存在的套餐", "继续聊天", "/handoff", "/continue", "/new", "y", "/exit"],
        Console(file=output, force_terminal=False, color_system=None, width=120),
    )
    monkeypatch.setattr(terminal_module, "Terminal", lambda: terminal)
    monkeypatch.setattr(terminal_module, "PromptSession", lambda: object())
    monkeypatch.setattr(terminal_module, "patch_stdout", nullcontext)

    exit_code = await terminal_module.run_repl(recording, session=session_app.session)

    rendered = output.getvalue()
    assert exit_code == 0
    assert (
        "先查\n↳ 工具步骤 1/1：lookup_record\n"
        "正在等待模型继续响应…（Ctrl+C 取消）\n工具错误后恢复。\n"
    ) in rendered
    assert "事实摘要：工具 1 次" in rendered
    assert rendered.count("工具错误后恢复。") == 1
    assert rendered.count("后续对话正常。") == 1
    assert "未知命令：/handoff" in rendered
    assert "未知命令：/continue" in rendered
    assert "已切换到新的独立会话。" in rendered
    assert session_app.session.log.snapshot().records == ()
    assert provider.complete_calls == []

    public_surface = rendered + str([event.payload for event in recording.events])
    persisted_surface = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (tmp_path / "state").rglob("*")
        if path.is_file()
    )
    for sentinel in (
        "credential-sentinel",
        "argument-sentinel",
        "secret-call-id",
        "result-sentinel",
        "Traceback",
    ):
        assert sentinel not in rendered
        if sentinel != "secret-call-id":
            assert sentinel not in public_surface
        assert sentinel not in persisted_surface


@pytest.mark.asyncio
async def test_real_repl_configuration_uses_shared_terminal_approval_and_dirty_history(tmp_path):
    app = build_application(state_root=tmp_path / "state", credentials=MemoryCredentialStore())
    project = tmp_path / "project"
    project.mkdir()
    identity = app.workspace_service.confirm(app.workspace_service.resolve(project))
    provider = ScriptedModelProvider(
        [
            AssistantMessage(
                tool_calls=(
                    FunctionToolCall(
                        id="config-call",
                        name="update_configuration",
                        arguments=(
                            '{"scope":"session","target":"preferences",'
                            '"operation":"set","path":"language","value":"中文"}'
                        ),
                    ),
                )
            ),
            AssistantMessage(content="本次会话将使用中文。"),
        ]
    )
    prompt_session = object()
    output = StringIO()
    terminal = _PromptingTerminal(
        ["请把这次回复改成中文", "y", "/exit", "y"],
        Console(file=output, force_terminal=False, color_system=None, width=120),
    )
    approval = terminal_module.TerminalApprovalPort(terminal, prompt_session)
    session_app = build_session_application(
        app,
        identity,
        provider=provider,
        model=ModelRef(provider_id="p", model_id="m"),
        approval_port=approval,
    )

    exit_code = await terminal_module.run_repl(
        session_app.orchestrator,
        session=session_app.session,
        terminal=terminal,
        prompt_session=prompt_session,
    )

    assert exit_code == 0
    assert session_app.session.preferences.language == "中文"
    assert session_app.session.dirty is True
    assert session_app.session.log.snapshot().records
    assert "配置预览：" in output.getvalue()
    assert "确认执行？ [y/N] " in terminal.prompt_messages
    assert "确认退出并丢弃当前内存内容？ [y/N] " in terminal.prompt_messages
    assert all(item is prompt_session for item in terminal.prompt_sessions)
