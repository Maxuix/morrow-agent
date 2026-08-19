from __future__ import annotations

import pytest

from morrow.adapters.credentials.keyring import MemoryCredentialStore
from morrow.application.commands import CommandService
from morrow.application.context import ContextBuilder
from morrow.application.orchestrator import SessionOrchestrator
from morrow.bootstrap import build_application, build_session_application
from morrow.core.capabilities import PermissionPreset, PermissionProfile
from morrow.core.models import (
    ConfigPatch,
    ConfigPatchOperation,
    ModelRef,
    Preferences,
    Profile,
)
from morrow.runtime.agent import AgentRuntime
from morrow.runtime.session import Session
from morrow.services.preferences import ConfigPatchService
from morrow.services.workspace import WorkspaceStateService
from morrow.testing import ScriptedModelProvider, make_context_builder, seed_user_turn


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

    assert all(result.action == "config_preview" for result in results)
    assert {path.name: path.read_bytes() if path.exists() else None for path in paths} == before
    assert results[0].lines == [
        "配置预览：",
        "作用域：workspace",
        "目标：preferences",
        "- set language = 中文",
    ]
    assert results[1].lines[-1] == "- set summary = a demo"
    for result in results:
        patch_service.apply(result.value)


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


def test_legacy_handoff_files_are_ignored_and_remain_byte_identical(tmp_path):
    app = build_application(state_root=tmp_path / "state", credentials=MemoryCredentialStore())
    project = tmp_path / "project"
    project.mkdir()
    identity = app.workspace_service.confirm(app.workspace_service.resolve(project))
    workspace_dir = app.data_root.workspaces_path / identity.workspace_id
    workspace_dir.mkdir(parents=True, exist_ok=True)
    primary = workspace_dir / "handoff.yaml"
    backup = workspace_dir / "handoff.yaml.bak"
    primary.write_bytes(b"schema_version: 99\nrevision: 5\n")
    backup.write_bytes(b"not: [valid")
    before = (primary.read_bytes(), backup.read_bytes())

    class FailOnLegacyAccess:
        def __init__(self, wrapped):
            self.wrapped = wrapped

        def __getattr__(self, name):
            return getattr(self.wrapped, name)

        def load_handoff(self, workspace_id):
            raise AssertionError(f"legacy primary read attempted: {workspace_id}")

        def load_handoff_backup(self, workspace_id):
            raise AssertionError(f"legacy backup read attempted: {workspace_id}")

        def write_handoff(self, *args, **kwargs):
            raise AssertionError("legacy write attempted")

        def clear_handoff(self, *args, **kwargs):
            raise AssertionError("legacy clear attempted")

    guarded_store = FailOnLegacyAccess(app.project_store)
    workspace_state = WorkspaceStateService(guarded_store)
    inspection = workspace_state.inspect(identity.workspace_id)
    result = workspace_state.onboard(identity.workspace_id, display_name="demo", summary="summary")
    config = ConfigPatchService(
        guarded_store, app.global_store, identity.workspace_id, Session(session_id="s")
    )
    config.apply(
        ConfigPatch(
            scope="workspace",
            target="preferences",
            operations=[ConfigPatchOperation(op="set", path="language", value="中文")],
        )
    )

    assert inspection.read_only is False
    assert result == 1
    assert app.project_store.load_profile(identity.workspace_id).value.profile.name == "demo"
    assert (
        app.project_store.load_preferences(identity.workspace_id).value.preferences.language
        == "中文"
    )
    assert (primary.read_bytes(), backup.read_bytes()) == before


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
        commands.config_service.apply(
            ConfigPatch(
                scope="workspace",
                target="preferences",
                operations=[ConfigPatchOperation(op="set", path="language", value="English")],
            )
        )
    with pytest.raises(RuntimeError):
        commands.reset_preferences("workspace")
    assert commands.execute("/config reset workspace").action is None
    assert commands.execute("/config edit workspace language English").action is None
    commands.config_service.apply(
        ConfigPatch(
            scope="workspace",
            target="profile",
            operations=[ConfigPatchOperation(op="set", path="summary", value="allowed")],
        )
    )
    assert app.project_store.load_profile(workspace_id).value.profile.summary == "allowed"
    assert preferences_path.read_bytes() == before_preferences
    assert app.provider_service.list().providers == {}
    result = await orchestrator.dispatch("ordinary chat")
    assert result.events[-1].payload["finish_reason"] == "stop"
