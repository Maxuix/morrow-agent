from __future__ import annotations

import pytest

from morrow.adapters.credentials.keyring import MemoryCredentialStore
from morrow.application.commands import CommandService
from morrow.application.context import ContextBuilder
from morrow.application.orchestrator import SessionOrchestrator
from morrow.bootstrap import build_application, build_session_application
from morrow.core.models import (
    ConfigPatch,
    ConfigPatchOperation,
    Decision,
    Handoff,
    ModelRef,
    Preferences,
    Profile,
)
from morrow.interfaces.terminal import _save_or_discard_before_switch
from morrow.runtime.agent import AgentRuntime
from morrow.runtime.session import Session
from morrow.services.preferences import ConfigIntentGate, ConfigPatchService
from morrow.testing import ScriptedModelProvider


@pytest.mark.parametrize(
    "text",
    ["这个项目用什么框架？", "以后再改", "记住刚才的报错", "这次先这样", "请帮我修复这个问题"],
)
def test_config_gate_does_not_trigger_on_ordinary_or_ambiguous_chat(text):
    assert not ConfigIntentGate().match(text).matched


def test_config_gate_triggers_only_for_standalone_persistence(text="请记住这个项目以后用中文回复"):
    assert ConfigIntentGate().match(text).matched
    assert ConfigIntentGate().match("把这条约束写进项目档案").matched
    assert ConfigIntentGate().match("请记住这个项目以后用中文回复并修复代码").mixed_task


def test_config_patch_updates_workspace_preferences_only_after_validation(tmp_path):
    app = build_application(state_root=tmp_path / "state", credentials=MemoryCredentialStore())
    project = tmp_path / "project"
    project.mkdir()
    identity = app.workspace_service.confirm(app.workspace_service.resolve(project))
    service = ConfigPatchService(app.project_store, app.global_store, identity.workspace_id)
    patch = ConfigPatch(
        scope="workspace",
        target="preferences",
        operations=[ConfigPatchOperation(op="set", path="language", value="中文")],
    )
    service.apply(patch)
    assert (
        app.project_store.load_preferences(identity.workspace_id).value.preferences.language
        == "中文"
    )
    invalid = ConfigPatch(
        scope="workspace",
        target="preferences",
        operations=[ConfigPatchOperation(op="set", path="workspace_id", value="bad")],
    )
    with pytest.raises(ValueError):
        service.apply(invalid)


def test_successful_config_patch_refreshes_next_turn_snapshot_and_unset_reveals_lower_layer(
    tmp_path,
):
    app = build_application(state_root=tmp_path / "state", credentials=MemoryCredentialStore())
    project = tmp_path / "project"
    project.mkdir()
    identity = app.workspace_service.confirm(app.workspace_service.resolve(project))
    session = Session(session_id="s", global_preferences=Preferences(language="中文"))
    service = ConfigPatchService(
        app.project_store, app.global_store, identity.workspace_id, session
    )
    service.apply(
        ConfigPatch(
            scope="workspace",
            target="preferences",
            operations=[ConfigPatchOperation(op="set", path="language", value="English")],
        )
    )
    assert session.workspace_preferences.language == "English"
    service.apply(
        ConfigPatch(
            scope="workspace",
            target="preferences",
            operations=[ConfigPatchOperation(op="unset", path="language")],
        )
    )
    assert session.workspace_preferences.language is None
    assert (
        ContextBuilder.merge_preferences(
            session.global_preferences, session.workspace_preferences, session.preferences
        ).language
        == "中文"
    )


def test_handoff_decision_remove_matches_decision_text_and_does_not_load_independent_session(
    tmp_path,
):
    app = build_application(state_root=tmp_path / "state", credentials=MemoryCredentialStore())
    project = tmp_path / "project"
    project.mkdir()
    identity = app.workspace_service.confirm(app.workspace_service.resolve(project))
    app.project_store.write_handoff(
        identity.workspace_id,
        Handoff(current_goal="goal", decisions=[Decision(decision="Use YAML")]),
    )
    session = Session(session_id="s")
    service = ConfigPatchService(
        app.project_store, app.global_store, identity.workspace_id, session
    )
    service.apply(
        ConfigPatch(
            scope="workspace",
            target="handoff",
            operations=[ConfigPatchOperation(op="remove", path="decisions", value=" use yaml ")],
        )
    )
    assert app.project_store.load_handoff(identity.workspace_id).value.handoff.decisions == []
    assert session.loaded_handoff is None


def test_command_service_routes_deterministic_edits_to_one_patch_path(tmp_path):
    app = build_application(state_root=tmp_path / "state", credentials=MemoryCredentialStore())
    project = tmp_path / "project"
    project.mkdir()
    identity = app.workspace_service.confirm(app.workspace_service.resolve(project))
    app.project_store.write_profile(identity.workspace_id, Profile(name="demo"))
    app.project_store.write_handoff(identity.workspace_id, Handoff(current_goal="old"))
    session = Session(session_id="s")
    patch_service = ConfigPatchService(
        app.project_store, app.global_store, identity.workspace_id, session
    )
    commands = CommandService(
        session=session,
        identity=identity,
        project_store=app.project_store,
        handoff_service=None,
        config_service=patch_service,
    )
    assert commands.execute("/config edit workspace language 中文").lines == ["配置已保存。"]
    assert commands.execute("/workspace edit summary a demo").lines == ["Profile 已更新。"]
    assert commands.execute("/handoff edit current_goal new goal").lines == ["Handoff 字段已更新。"]
    assert (
        app.project_store.load_handoff(identity.workspace_id).value.handoff.current_goal
        == "new goal"
    )


def test_dirty_session_transitions_are_explicit_and_state_clear_is_scoped(tmp_path):
    app = build_application(state_root=tmp_path / "state", credentials=MemoryCredentialStore())
    project = tmp_path / "project"
    project.mkdir()
    identity = app.workspace_service.confirm(app.workspace_service.resolve(project))
    app.project_store.write_profile(identity.workspace_id, Profile(name="keep"))
    app.project_store.write_handoff(identity.workspace_id, Handoff(current_goal="keep"))
    session = Session(session_id="s")
    session.accept_user("dirty")
    commands = CommandService(
        session=session,
        identity=identity,
        project_store=app.project_store,
        handoff_service=None,
    )
    assert commands.execute("/new").action == "switch_new"
    assert commands.execute("/continue").action == "switch_continue"
    cleared = app.project_store.clear_handoff(identity.workspace_id, expected_revision=1)
    assert cleared.status.value == "ok"
    assert app.project_store.load_profile(identity.workspace_id).value.profile.name == "keep"


@pytest.mark.asyncio
async def test_stage_1a_orchestrator_has_zero_config_calls_without_gate(tmp_path):
    provider = ScriptedModelProvider(["ok"])
    session = Session(session_id="s")

    class Commands:
        def execute(self, raw):
            return type("Result", (), {"lines": [], "action": None})()

    runtime = AgentRuntime(provider, ModelRef(provider_id="p", model_id="m"), ContextBuilder())
    orchestrator = SessionOrchestrator(
        session=session,
        runtime=runtime,
        command_service=Commands(),
        context_builder=ContextBuilder(),
    )
    result = await orchestrator.dispatch("普通聊天")
    assert provider.complete_calls == []
    assert any(event.type == "text.delta" for event in result.events)


@pytest.mark.asyncio
async def test_orchestrator_stream_exposes_events_before_terminal_result():
    provider = ScriptedModelProvider([["a", "b"]])
    session = Session(session_id="s")

    class Commands:
        def execute(self, raw):
            return type("Result", (), {"lines": [], "action": None})()

    orchestrator = SessionOrchestrator(
        session=session,
        runtime=AgentRuntime(provider, ModelRef(provider_id="p", model_id="m"), ContextBuilder()),
        command_service=Commands(),
        context_builder=ContextBuilder(),
    )
    items = [item async for item in orchestrator.stream("普通聊天")]

    assert [item.type for item in items[:-1]] == [
        "turn.started",
        "text.delta",
        "text.delta",
        "turn.completed",
    ]
    assert items[-1].events == []


@pytest.mark.asyncio
async def test_stage_1b_explicit_config_gate_previews_before_apply(tmp_path):
    app = build_application(state_root=tmp_path / "state", credentials=MemoryCredentialStore())
    project = tmp_path / "project"
    project.mkdir()
    identity = app.workspace_service.confirm(app.workspace_service.resolve(project))
    provider = ScriptedModelProvider(
        [
            '{"result":"config_patch","patch":{"scope":"workspace","target":"preferences","operations":[{"op":"set","path":"language","value":"中文"}]}}'
        ]
    )
    session, _, _, _, orchestrator = build_session_application(
        app,
        identity,
        provider=provider,
        model=ModelRef(provider_id="p", model_id="m"),
    )
    result = await orchestrator.dispatch("请记住这个项目以后用中文回复")
    assert result.action == "config_preview"
    assert result.lines == ["配置预览：即将写入一项经过字段白名单校验的配置。"]
    assert len(provider.complete_calls) == 1
    assert (
        app.project_store.load_preferences(identity.workspace_id).value.preferences.language is None
    )
    orchestrator.command_service.config_service.apply(result.value)
    assert (
        app.project_store.load_preferences(identity.workspace_id).value.preferences.language
        == "中文"
    )


@pytest.mark.asyncio
async def test_dirty_independent_save_failure_preserves_session(tmp_path):
    class Console:
        def print(self, *args, **kwargs):
            pass

    class TerminalStub:
        console = Console()

        async def prompt(self, session, message):
            return "s"

    class FailedSave:
        status = type("Status", (), {"value": "failed"})()

    class HandoffStub:
        async def generate_and_publish(self, session, *, expected_revision):
            return FailedSave(), False

    class StoreStub:
        def load_handoff(self, workspace_id):
            return type("Loaded", (), {"revision": 0})()

    session = Session(session_id="s")
    session.accept_user("keep me")
    choice = await _save_or_discard_before_switch(
        TerminalStub(), object(), HandoffStub(), StoreStub(), "ws", session
    )
    assert choice == "cancelled"
    assert session.messages[0].content == "keep me"
    assert session.dirty is True
