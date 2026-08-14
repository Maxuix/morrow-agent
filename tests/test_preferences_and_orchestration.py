from __future__ import annotations

import pytest

from morrow.adapters.credentials.keyring import MemoryCredentialStore
from morrow.application.commands import CommandService
from morrow.application.context import ContextBuilder
from morrow.application.orchestrator import SessionOrchestrator
from morrow.bootstrap import build_application, build_session_application
from morrow.core.models import (
    ConfigExtractionResult,
    ConfigPatch,
    ConfigPatchOperation,
    Decision,
    Handoff,
    ModelRef,
    Preferences,
    Profile,
)
from morrow.interfaces.terminal import _exit, _save_or_discard_before_switch
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


@pytest.mark.parametrize(
    "text",
    [
        "请把回复语言设置为 English",
        "保存到全局偏好：详细程度设为 concise",
        "把这条约束写进工作空间档案",
    ],
)
def test_config_gate_must_trigger_corpus(text):
    decision = ConfigIntentGate().match(text)
    assert decision.matched is True
    assert decision.mixed_task is False
    assert decision.forbidden is False


@pytest.mark.parametrize(
    "text",
    [
        "请解释 Provider 是什么",
        "模型凭据应该如何安全保存？",
        "API Key 为什么不能写进配置？",
        "请分析权限和安全规则的设计",
    ],
)
def test_sensitive_vocabulary_discussion_is_not_a_forbidden_config_attempt(text):
    decision = ConfigIntentGate().match(text)
    assert decision.matched is False
    assert decision.mixed_task is False
    assert decision.forbidden is False


def test_forbidden_fields_are_rejected_only_after_persistence_intent_is_identified():
    decision = ConfigIntentGate().match("请把 Provider 凭据保存到工作空间配置")
    assert decision.matched is False
    assert decision.forbidden is True


@pytest.mark.parametrize(
    "payload",
    [
        {"result": "config_patch"},
        {"result": "config_patch", "question": "extra", "patch": None},
        {"result": "clarification_required"},
        {
            "result": "clarification_required",
            "question": "which scope?",
            "patch": {
                "scope": "session",
                "target": "preferences",
                "operations": [{"op": "set", "path": "language", "value": "中文"}],
            },
        },
        {"result": "no_change", "question": "extra"},
        {
            "result": "no_change",
            "patch": {
                "scope": "session",
                "target": "preferences",
                "operations": [{"op": "set", "path": "language", "value": "中文"}],
            },
        },
    ],
)
def test_config_extraction_result_rejects_inconsistent_discriminated_shapes(payload):
    with pytest.raises(ValueError):
        ConfigExtractionResult.model_validate(payload)


@pytest.mark.parametrize(
    "payload",
    [
        {"result": "no_change"},
        {"result": "clarification_required", "question": "保存到哪个作用域？"},
        {
            "result": "config_patch",
            "patch": {
                "scope": "session",
                "target": "preferences",
                "operations": [{"op": "set", "path": "language", "value": "中文"}],
            },
        },
    ],
)
def test_config_extraction_result_accepts_only_consistent_shapes(payload):
    assert ConfigExtractionResult.model_validate(payload).result == payload["result"]


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
    paths = [
        app.data_root.workspaces_path / identity.workspace_id / name
        for name in ("preferences.yaml", "profile.yaml", "handoff.yaml")
    ]
    before = {path.name: path.read_bytes() if path.exists() else None for path in paths}

    results = [
        commands.execute("/config edit workspace language 中文"),
        commands.execute("/workspace edit summary a demo"),
        commands.execute("/handoff edit current_goal new goal"),
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
    assert results[2].lines[-1] == "- set current_goal = new goal"
    for result in results:
        patch_service.apply(result.value)
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
    refused = commands.execute("/continue")
    assert refused.action is None
    assert refused.lines == ["当前没有可继续的交接。"]


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
    assert result.lines == [
        "配置预览：",
        "作用域：workspace",
        "目标：preferences",
        "- set language = 中文",
    ]
    assert len(provider.complete_calls) == 1
    assert app.project_store.load_preferences(identity.workspace_id).value is None
    orchestrator.command_service.config_service.apply(result.value)
    assert (
        app.project_store.load_preferences(identity.workspace_id).value.preferences.language
        == "中文"
    )


@pytest.mark.asyncio
async def test_multi_operation_preview_shows_every_exact_mutation_before_write(tmp_path):
    app = build_application(state_root=tmp_path / "state", credentials=MemoryCredentialStore())
    project = tmp_path / "project"
    project.mkdir()
    identity = app.workspace_service.confirm(app.workspace_service.resolve(project))
    provider = ScriptedModelProvider(
        [
            '{"result":"config_patch","patch":{"scope":"session","target":"preferences","operations":[{"op":"set","path":"language","value":"中文"},{"op":"append","path":"instructions","value":"先给结论"}]}}'
        ]
    )
    session, _, _, _, orchestrator = build_session_application(
        app,
        identity,
        provider=provider,
        model=ModelRef(provider_id="p", model_id="m"),
    )

    result = await orchestrator.dispatch("请设置这次回复语言并加入这条指令")

    assert result.action == "config_preview"
    assert result.lines == [
        "配置预览：",
        "作用域：session",
        "目标：preferences",
        "- set language = 中文",
        "- append instructions = 先给结论",
    ]
    assert session.preferences == Preferences()


@pytest.mark.asyncio
async def test_sensitive_vocabulary_discussion_streams_without_extraction(tmp_path):
    app = build_application(state_root=tmp_path / "state", credentials=MemoryCredentialStore())
    project = tmp_path / "project"
    project.mkdir()
    identity = app.workspace_service.confirm(app.workspace_service.resolve(project))
    provider = ScriptedModelProvider(["ordinary answer"])
    _, _, _, _, orchestrator = build_session_application(
        app,
        identity,
        provider=provider,
        model=ModelRef(provider_id="p", model_id="m"),
    )

    result = await orchestrator.dispatch("请解释 Provider 凭据为什么需要安全保存")

    assert result.events[-1].payload["finish_reason"] == "stop"
    assert len(provider.stream_calls) == 1
    assert provider.complete_calls == []


@pytest.mark.asyncio
async def test_invalid_config_extraction_shape_repairs_once_before_preview(tmp_path):
    app = build_application(state_root=tmp_path / "state", credentials=MemoryCredentialStore())
    project = tmp_path / "project"
    project.mkdir()
    identity = app.workspace_service.confirm(app.workspace_service.resolve(project))
    provider = ScriptedModelProvider(
        [
            '{"result":"config_patch","patch":null}',
            '{"result":"config_patch","patch":{"scope":"workspace","target":"preferences","operations":[{"op":"set","path":"language","value":"中文"}]}}',
        ]
    )
    _, _, _, _, orchestrator = build_session_application(
        app,
        identity,
        provider=provider,
        model=ModelRef(provider_id="p", model_id="m"),
    )

    result = await orchestrator.dispatch("请记住这个项目以后用中文回复")

    assert result.action == "config_preview"
    assert len(provider.complete_calls) == 2
    assert provider.stream_calls == []
    assert app.project_store.load_preferences(identity.workspace_id).value is None


@pytest.mark.asyncio
async def test_two_invalid_config_extraction_shapes_fail_closed_without_chat_or_write(tmp_path):
    app = build_application(state_root=tmp_path / "state", credentials=MemoryCredentialStore())
    project = tmp_path / "project"
    project.mkdir()
    identity = app.workspace_service.confirm(app.workspace_service.resolve(project))
    provider = ScriptedModelProvider(
        [
            '{"result":"config_patch","patch":null}',
            '{"result":"clarification_required","question":null}',
        ]
    )
    _, _, _, _, orchestrator = build_session_application(
        app,
        identity,
        provider=provider,
        model=ModelRef(provider_id="p", model_id="m"),
    )

    result = await orchestrator.dispatch("请记住这个项目以后用中文回复")

    assert result.lines == ["配置结果不明确；请直接使用 /config edit。"]
    assert len(provider.complete_calls) == 2
    assert provider.stream_calls == []
    assert app.project_store.load_preferences(identity.workspace_id).value is None


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


@pytest.mark.parametrize(
    ("invalid_document", "invalid_bytes"),
    [
        ("profile.yaml", b"not: [valid"),
        ("handoff.yaml", b"schema_version: 99\nrevision: 5\n"),
    ],
)
@pytest.mark.asyncio
async def test_profile_or_handoff_failure_enforces_workspace_wide_degraded_mode(
    tmp_path, invalid_document, invalid_bytes
):
    app = build_application(state_root=tmp_path / "state", credentials=MemoryCredentialStore())
    project = tmp_path / "project"
    project.mkdir()
    identity = app.workspace_service.confirm(app.workspace_service.resolve(project))
    workspace_id = identity.workspace_id
    app.project_store.write_preferences(workspace_id, Preferences(language="中文"))
    app.project_store.write_profile(workspace_id, Profile(name="valid profile"))
    app.project_store.write_handoff(workspace_id, Handoff(current_goal="valid handoff"))
    invalid_path = app.data_root.workspaces_path / workspace_id / invalid_document
    invalid_path.write_bytes(invalid_bytes)
    before = {
        path.name: path.read_bytes()
        for path in (app.data_root.workspaces_path / workspace_id).glob("*.yaml")
    }
    provider = ScriptedModelProvider(["chat remains available"])

    inspection = app.workspace_state_service.inspect(workspace_id)
    session, _, _, commands, orchestrator = build_session_application(
        app,
        identity,
        provider=provider,
        model=ModelRef(provider_id="p", model_id="m"),
    )

    assert inspection.read_only is True
    assert session.read_only is True
    assert session.profile is None
    assert session.loaded_handoff is None
    assert commands.execute("/continue").action is None
    assert commands.execute("/handoff update").action is None
    assert commands.execute("/handoff clear").action is None
    assert commands.execute("/handoff edit current_goal changed").action is None
    assert commands.execute("/workspace reset").action is None
    assert commands.execute("/workspace edit summary changed").action is None
    assert commands.execute("/config reset workspace").action is None
    assert commands.execute("/config edit workspace language English").action is None
    assert (
        app.workspace_state_service.onboard(
            workspace_id,
            display_name="blocked",
            summary="blocked",
            current_goal="blocked",
        )
        is None
    )
    with pytest.raises(RuntimeError):
        commands.clear_handoff()
    with pytest.raises(RuntimeError):
        commands.reset_profile()
    with pytest.raises(RuntimeError):
        commands.reset_preferences("workspace")
    with pytest.raises(RuntimeError):
        commands.config_service.apply(
            ConfigPatch(
                scope="workspace",
                target="preferences",
                operations=[ConfigPatchOperation(op="set", path="language", value="English")],
            )
        )
    assert (
        commands.config_service.apply(
            ConfigPatch(
                scope="session",
                target="preferences",
                operations=[ConfigPatchOperation(op="set", path="language", value="English")],
            )
        )
        == "ok"
    )
    commands.config_service.apply(
        ConfigPatch(
            scope="global",
            target="preferences",
            operations=[ConfigPatchOperation(op="set", path="response_detail", value="concise")],
        )
    )
    assert app.provider_service.list().providers == {}
    blocked_save, _ = await commands.handoff_service.generate_and_publish(
        session, expected_revision=app.project_store.load_handoff(workspace_id).revision
    )
    assert blocked_save.status.value == "failed"
    result = await orchestrator.dispatch("ordinary chat")
    assert result.events[-1].payload["finish_reason"] == "stop"

    class Console:
        def print(self, *args, **kwargs):
            pass

    class ConfirmingTerminal:
        console = Console()

        async def prompt(self, prompt_session, message):
            return "y"

    assert (
        await _exit(
            orchestrator,
            commands.handoff_service,
            app.project_store,
            workspace_id,
            session,
            ConfirmingTerminal(),
            object(),
        )
        == 0
    )
    after = {
        path.name: path.read_bytes()
        for path in (app.data_root.workspaces_path / workspace_id).glob("*.yaml")
    }
    assert after == before


@pytest.mark.asyncio
async def test_corrupt_workspace_preferences_is_an_isolated_non_overwritable_empty_layer(tmp_path):
    app = build_application(state_root=tmp_path / "state", credentials=MemoryCredentialStore())
    project = tmp_path / "project"
    project.mkdir()
    identity = app.workspace_service.confirm(app.workspace_service.resolve(project))
    workspace_id = identity.workspace_id
    app.project_store.write_preferences(workspace_id, Preferences(language="中文"))
    app.project_store.write_profile(workspace_id, Profile(name="valid profile"))
    app.project_store.write_handoff(workspace_id, Handoff(current_goal="valid handoff"))
    preferences_path = app.data_root.workspaces_path / workspace_id / "preferences.yaml"
    preferences_path.write_bytes(b"not: [valid")
    before_preferences = preferences_path.read_bytes()
    provider = ScriptedModelProvider(["chat remains available"])

    inspection = app.workspace_state_service.inspect(workspace_id)
    session, _, _, commands, orchestrator = build_session_application(
        app,
        identity,
        provider=provider,
        model=ModelRef(provider_id="p", model_id="m"),
    )

    assert inspection.read_only is False
    assert inspection.preferences_read_only is True
    assert session.read_only is False
    assert session.workspace_preferences_read_only is True
    assert session.workspace_preferences == Preferences()
    assert session.profile.name == "valid profile"
    assert commands.execute("/continue").action == "continue"
    assert commands.load_handoff().value.handoff.current_goal == "valid handoff"
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
    assert commands.execute("/handoff update").action == "update_handoff"
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
