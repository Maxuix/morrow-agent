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

    async def prompt(self, session, message="你 > "):
        del session, message
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
            (
                '{"current_goal":"continue safely","progress":[],"decisions":[],'
                '"blockers":[],"open_questions":[],"next_actions":[],'
                '"recovery_note":null}'
            ),
        ]
    )
    session, _, handoff, _, orchestrator = build_session_application(
        app,
        identity,
        provider=provider,
        model=ModelRef(provider_id="p", model_id="m"),
    )
    recording = _RecordingOrchestrator(orchestrator)
    output = StringIO()
    terminal = _PromptingTerminal(
        ["请查询不存在的套餐", "继续聊天", "/handoff update", "/new", "/exit"],
        Console(file=output, force_terminal=False, color_system=None, width=120),
    )
    monkeypatch.setattr(terminal_module, "Terminal", lambda: terminal)
    monkeypatch.setattr(terminal_module, "PromptSession", lambda: object())
    monkeypatch.setattr(terminal_module, "patch_stdout", nullcontext)

    exit_code = await terminal_module.run_repl(
        recording,
        handoff_service=handoff,
        project_store=app.project_store,
        workspace_id=identity.workspace_id,
        session=session,
    )

    rendered = output.getvalue()
    assert exit_code == 0
    assert "先查\n↳ 工具步骤 1/1：lookup_record\n工具错误后恢复。\n" in rendered
    assert rendered.count("工具错误后恢复。") == 1
    assert rendered.count("后续对话正常。") == 1
    assert "已更新交接。" in rendered
    assert "已切换到新的独立会话。" in rendered
    assert session.log.snapshot().records == ()
    saved = app.project_store.load_handoff(identity.workspace_id).value.handoff
    assert saved.current_goal == "continue safely"

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
