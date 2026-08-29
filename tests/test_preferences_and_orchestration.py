from __future__ import annotations

import pytest

from morrow.adapters.credentials.keyring import MemoryCredentialStore
from morrow.application.commands import CommandService
from morrow.application.configuration import ConfigurationCommand
from morrow.application.orchestrator import SessionOrchestrator
from morrow.bootstrap import build_application, build_session_application
from morrow.core.capabilities import PermissionPreset, PermissionProfile
from morrow.core.models import ModelRef, Preferences, Profile
from morrow.runtime.agent import AgentRuntime
from morrow.runtime.session import Session
from morrow.services.preferences import ConfigPatchService
from morrow.testing import ScriptedModelProvider, make_context_builder, seed_user_turn


def test_command_service_routes_deterministic_edits_to_one_patch_path(tmp_path):
    app = build_application(state_root=tmp_path / "state", credentials=MemoryCredentialStore())
    project = tmp_path / "project"
    project.mkdir()
    identity = app.workspace_service.confirm(app.workspace_service.resolve(project))
    app.project_store.write_profile(identity.workspace_id, Profile(name="demo"))
    session = Session(session_id="s")
    patch_service = ConfigPatchService(
        app.project_store, app.global_store, identity.workspace_id, session
    )
    commands = CommandService(
        session=session,
        identity=identity,
        project_store=app.project_store,
        config_service=patch_service,
    )
    paths = [
        app.data_root.workspaces_path / identity.workspace_id / name
        for name in ("preferences.yaml", "profile.yaml")
    ]
    before = {path.name: path.read_bytes() if path.exists() else None for path in paths}

    results = [
        commands.execute("/config edit workspace language 中文"),
        commands.execute("/workspace edit summary a demo"),
    ]

    assert results[0].action is None
    assert results[1].action == "config_preview"
    assert {path.name: path.read_bytes() if path.exists() else None for path in paths} == before
    assert results[0].lines == [
        "Preferences 的 /config edit/reset 已退役；请使用 /preferences 管理原子规则。"
    ]
    assert results[1].lines[-1] == "- set summary = a demo"
    patch_service.apply_command(results[1].value)


def test_dirty_session_transition_requires_discard_and_removed_commands_are_unknown(tmp_path):
    app = build_application(state_root=tmp_path / "state", credentials=MemoryCredentialStore())
    project = tmp_path / "project"
    project.mkdir()
    identity = app.workspace_service.confirm(app.workspace_service.resolve(project))
    app.project_store.write_profile(identity.workspace_id, Profile(name="keep"))
    session = Session(session_id="s")
    seed_user_turn(session, "dirty")
    commands = CommandService(
        session=session,
        identity=identity,
        project_store=app.project_store,
    )
    assert commands.execute("/new").action == "discard_new"
    assert app.project_store.load_profile(identity.workspace_id).value.profile.name == "keep"
    for raw in ("/continue", "/handoff", "/handoff update"):
        refused = commands.execute(raw)
        assert refused.action is None
        assert refused.value is None
        assert refused.lines == [f"未知命令：{raw.split()[0]}"]


def test_full_access_grant_command_is_local_confirmation_gated_and_one_run_armed(tmp_path):
    app = build_application(state_root=tmp_path / "state", credentials=MemoryCredentialStore())
    project = tmp_path / "project"
    project.mkdir()
    identity = app.workspace_service.confirm(app.workspace_service.resolve(project))
    session = Session(
        session_id="s",
        permission_profile=PermissionProfile.from_preset(PermissionPreset.FULL_ACCESS_MANUAL),
    )
    commands = CommandService(
        session=session,
        identity=identity,
        project_store=app.project_store,
    )

    preview = commands.execute("/grant")
    assert preview.action == "arm_full_access_grant"
    assert session.pending_full_access_grant is False
    assert "unconfined_host_process" in " ".join(preview.lines)

    commands.arm_full_access_grant()
    assert session.pending_full_access_grant is True
    assert commands.execute("/grant").action is None

    session.permission_profile = PermissionProfile()
    session.pending_full_access_grant = False
    assert commands.execute("/grant").action is None


@pytest.mark.asyncio
async def test_stage_1a_orchestrator_has_zero_config_calls_without_gate(tmp_path):
    provider = ScriptedModelProvider(["ok"])
    session = Session(session_id="s")

    class Commands:
        def execute(self, raw):
            return type("Result", (), {"lines": [], "action": None})()

    runtime = AgentRuntime(
        provider, ModelRef(provider_id="p", model_id="m"), make_context_builder()
    )
    orchestrator = SessionOrchestrator(
        session=session,
        runtime=runtime,
        command_service=Commands(),
        context_builder=make_context_builder(),
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
        runtime=AgentRuntime(
            provider, ModelRef(provider_id="p", model_id="m"), make_context_builder()
        ),
        command_service=Commands(),
        context_builder=make_context_builder(),
    )
    items = [item async for item in orchestrator.stream("普通聊天")]

    assert [item.type for item in items[:-1]] == [
        "turn.started",
        "text.delta",
        "text.delta",
        "turn.completed",
    ]
    assert items[-1].events == []


def test_reset_clears_only_process_local_history():
    session = Session(session_id="s")
    seed_user_turn(session, "old", assistant="answer")

    session.reset("s2")

    assert session.session_id == "s2"
    assert session.log.snapshot().records == ()


@pytest.mark.asyncio
async def test_corrupt_workspace_preferences_is_an_isolated_non_overwritable_empty_layer(tmp_path):
    app = build_application(state_root=tmp_path / "state", credentials=MemoryCredentialStore())
    project = tmp_path / "project"
    project.mkdir()
    identity = app.workspace_service.confirm(app.workspace_service.resolve(project))
    workspace_id = identity.workspace_id
    app.project_store.write_preferences(workspace_id, Preferences(language="中文"))
    app.project_store.write_profile(workspace_id, Profile(name="valid profile"))
    preferences_path = app.data_root.workspaces_path / workspace_id / "preferences.yaml"
    preferences_path.write_bytes(b"not: [valid")
    before_preferences = preferences_path.read_bytes()
    provider = ScriptedModelProvider(["chat remains available"])

    inspection = app.workspace_state_service.inspect(workspace_id)
    session_app = build_session_application(
        app,
        identity,
        provider=provider,
        model=ModelRef(provider_id="p", model_id="m"),
    )
    session = session_app.session
    commands = session_app.commands
    orchestrator = session_app.orchestrator

    assert inspection.read_only is False
    assert inspection.preferences_read_only is True
    assert session.read_only is False
    assert session.workspace_preferences_read_only is True
    assert session.workspace_preferences == Preferences()
    assert session.profile.name == "valid profile"
    with pytest.raises(RuntimeError):
        commands.config_service.apply_command(
            ConfigurationCommand(
                scope="workspace",
                target="preferences",
                operation="set",
                path="language",
                value="English",
            )
        )
    assert commands.execute("/config reset workspace").action is None
    assert "已退役" in commands.execute("/config reset workspace").lines[0]
    assert commands.execute("/config edit workspace language English").action is None
    commands.config_service.apply_command(
        ConfigurationCommand(
            scope="workspace",
            target="profile",
            operation="set",
            path="summary",
            value="allowed",
        )
    )
    assert app.project_store.load_profile(workspace_id).value.profile.summary == "allowed"
    assert preferences_path.read_bytes() == before_preferences
    assert app.provider_service.list().providers == {}
    result = await orchestrator.dispatch("ordinary chat")
    assert result.events[-1].payload["finish_reason"] == "stop"
    status = session_app.api.preference_context_status(session_id=session.session_id)
    assert status.workspace_preferences.load_status == "corrupt"
    assert status.workspace_preferences.active == 0
    assert status.refresh_status == "degraded"
    assert status.refresh_error == "workspace_corrupt"
    assert status.injected_count == 0
