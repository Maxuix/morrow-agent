from __future__ import annotations

import asyncio
import json

import pytest
from pydantic import ValidationError

from morrow.adapters.credentials.keyring import MemoryCredentialStore
from morrow.application.configuration import (
    ConfigurationChangeStatus,
    ConfigurationCommand,
    UpdateConfigurationArguments,
    make_configuration_tool,
)
from morrow.application.orchestrator import DispatchResult
from morrow.bootstrap import build_application, build_session_application
from morrow.core.models import (
    AgentEvent,
    AssistantMessage,
    ConfigPatch,
    ConfigPatchOperation,
    FinishReason,
    FunctionToolCall,
    ModelRef,
    Preferences,
    Profile,
    StatePresence,
    StateWriteResult,
    StateWriteStatus,
    ToolApprovalDecision,
    ToolApprovalRequest,
)
from morrow.interfaces.terminal import TerminalApprovalPort
from morrow.runtime.agent import AgentLoop
from morrow.runtime.policy import ToolApproval
from morrow.runtime.session import Session
from morrow.runtime.tools import ToolErrorCode, ToolExecutor, ToolRegistry
from morrow.services.preferences import (
    ConfigPatchService,
    ConfigurationConflictError,
    ConfigurationNotFoundError,
    ConfigurationReadOnlyError,
    ConfigurationStateError,
    ConfigurationValidationError,
)
from morrow.testing import ScriptedModelProvider, make_context_builder, make_run_policy


class _Approval:
    def __init__(self, approved: bool) -> None:
        self.approved = approved
        self.requests: list[ToolApprovalRequest] = []

    async def request(self, request: ToolApprovalRequest) -> ToolApprovalDecision:
        self.requests.append(request)
        return ToolApprovalDecision(approved=self.approved)


class _BlockingApproval:
    def __init__(self) -> None:
        self.started = asyncio.Event()

    async def request(self, request: ToolApprovalRequest) -> ToolApprovalDecision:
        del request
        self.started.set()
        await asyncio.Event().wait()
        return ToolApprovalDecision(approved=True)


class _SequenceApproval:
    def __init__(self, decisions: list[bool]) -> None:
        self.decisions = list(decisions)
        self.requests: list[ToolApprovalRequest] = []

    async def request(self, request: ToolApprovalRequest) -> ToolApprovalDecision:
        self.requests.append(request)
        return ToolApprovalDecision(approved=self.decisions.pop(0))


class _ApplyThenBlockApproval:
    def __init__(self) -> None:
        self.calls = 0
        self.second_started = asyncio.Event()

    async def request(self, request: ToolApprovalRequest) -> ToolApprovalDecision:
        del request
        self.calls += 1
        if self.calls == 1:
            return ToolApprovalDecision(approved=True)
        self.second_started.set()
        await asyncio.Event().wait()
        return ToolApprovalDecision(approved=True)


class _BlockingApprovalTerminal:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.cancelled = False
        self.console = self

    def print(self, *values, **kwargs) -> None:
        del values, kwargs

    async def prompt(self, session, message="你 > "):
        del session, message
        self.started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise


def _products(tmp_path, *, profile: Profile | None = None):
    app = build_application(state_root=tmp_path / "state", credentials=MemoryCredentialStore())
    project = tmp_path / "project"
    project.mkdir()
    identity = app.workspace_service.confirm(app.workspace_service.resolve(project))
    if profile is not None:
        app.project_store.write_profile(identity.workspace_id, profile)
    session = Session(
        session_id="s",
        profile=profile,
        global_preferences=Preferences(),
        workspace_preferences=Preferences(),
    )
    service = ConfigPatchService(
        app.project_store, app.global_store, identity.workspace_id, session
    )
    return app, identity, session, service


def _configuration_call(call_id: str, payload: dict) -> FunctionToolCall:
    return FunctionToolCall(
        id=call_id,
        name="update_configuration",
        arguments=json.dumps(payload, ensure_ascii=False),
    )


@pytest.mark.parametrize(
    "payload",
    [
        {"scope": "workspace", "target": "profile", "operation": "reset", "path": "name"},
        {"scope": "workspace", "target": "profile", "operation": "set", "path": "name"},
        {
            "scope": "session",
            "target": "preferences",
            "operation": "unset",
            "path": "language",
            "value": "unexpected",
        },
        {
            "scope": "workspace",
            "target": "preferences",
            "operation": "unset",
            "path": "instructions",
        },
        {
            "scope": "global",
            "target": "profile",
            "operation": "set",
            "path": "name",
            "value": "bad",
        },
        {
            "scope": "workspace",
            "target": "preferences",
            "operation": "append",
            "path": "language",
            "value": "bad",
        },
        {
            "scope": "workspace",
            "target": "preferences",
            "operation": "set",
            "path": "workspace_id",
            "value": "bad",
        },
        {
            "scope": "workspace",
            "target": "preferences",
            "operation": "set",
            "path": "language",
            "value": "bad",
            "extra": True,
        },
    ],
)
def test_update_configuration_arguments_reject_invalid_combinations(payload):
    with pytest.raises(ValidationError):
        UpdateConfigurationArguments.model_validate(payload, strict=True)


@pytest.mark.parametrize(
    "payload",
    [
        {
            "scope": "workspace",
            "target": "profile",
            "operation": "reset",
            "path": None,
            "value": None,
        },
        {
            "scope": "session",
            "target": "preferences",
            "operation": "unset",
            "path": "language",
            "value": None,
        },
    ],
)
def test_update_configuration_arguments_treat_null_unused_fields_as_omitted(payload):
    arguments = UpdateConfigurationArguments.model_validate(payload, strict=True)

    assert arguments.to_command().operation in {"reset", "unset"}


def test_update_configuration_arguments_are_flat_and_sensitive_targets_are_absent():
    schema = UpdateConfigurationArguments.model_json_schema()
    assert schema["additionalProperties"] is False
    assert set(schema["properties"]) == {"scope", "target", "operation", "path", "value"}
    properties = json.dumps(schema["properties"], ensure_ascii=False).casefold()
    assert "workspace_id" not in properties
    assert "provider" not in properties


def test_service_applies_one_typed_command_and_returns_minimal_result(tmp_path):
    app, identity, session, service = _products(tmp_path, profile=Profile(name="demo"))

    result = service.apply_command(
        ConfigurationCommand(
            scope="workspace",
            target="preferences",
            operation="set",
            path="language",
            value="中文",
        )
    )

    assert result.model_dump(mode="json") == {
        "status": ConfigurationChangeStatus.APPLIED,
        "scope": "workspace",
        "target": "preferences",
        "operation": "set",
        "path": "language",
        "revision": 1,
    }
    assert session.workspace_preferences.language == "中文"
    assert (
        app.project_store.load_preferences(identity.workspace_id).value.preferences.language
        == "中文"
    )
    assert set(result.model_dump()) == {
        "status",
        "scope",
        "target",
        "operation",
        "path",
        "revision",
    }


def test_service_noop_matrix_does_not_write_or_increment_revision(tmp_path):
    app, identity, session, service = _products(tmp_path, profile=Profile(name="demo"))
    applied = service.apply_command(
        ConfigurationCommand(
            scope="workspace",
            target="preferences",
            operation="set",
            path="language",
            value="中文",
        )
    )
    before = app.project_store.load_preferences(identity.workspace_id)
    same = service.apply_command(
        ConfigurationCommand(
            scope="workspace",
            target="preferences",
            operation="set",
            path="language",
            value="中文",
        )
    )
    empty_unset = service.apply_command(
        ConfigurationCommand(
            scope="workspace",
            target="preferences",
            operation="unset",
            path="response_detail",
        )
    )
    assert applied.revision == 1
    assert same.status == ConfigurationChangeStatus.UNCHANGED
    assert same.revision == before.revision == 1
    assert empty_unset.status == ConfigurationChangeStatus.UNCHANGED
    assert app.project_store.load_preferences(identity.workspace_id).revision == 1

    session.preferences = Preferences(instructions=["先给 结论"])
    duplicate = service.apply_command(
        ConfigurationCommand(
            scope="session",
            target="preferences",
            operation="append",
            path="instructions",
            value=" 先给   结论 ",
        )
    )
    zero_remove = service.apply_command(
        ConfigurationCommand(
            scope="session",
            target="preferences",
            operation="remove",
            path="instructions",
            value="不存在",
        )
    )
    assert duplicate.status == ConfigurationChangeStatus.UNCHANGED
    assert zero_remove.status == ConfigurationChangeStatus.UNCHANGED
    assert session.preferences == Preferences(instructions=["先给 结论"])


def test_reset_preserves_tombstone_and_session_projections(tmp_path):
    app, identity, session, service = _products(tmp_path, profile=Profile(name="demo"))
    service.apply_command(
        ConfigurationCommand(
            scope="workspace",
            target="preferences",
            operation="set",
            path="language",
            value="中文",
        )
    )
    service.apply_command(
        ConfigurationCommand(scope="workspace", target="profile", operation="reset")
    )
    preferences_reset = service.apply_command(
        ConfigurationCommand(scope="workspace", target="preferences", operation="reset")
    )

    assert preferences_reset.status == ConfigurationChangeStatus.APPLIED
    assert (
        app.project_store.load_preferences(identity.workspace_id).presence == StatePresence.CLEARED
    )
    assert app.project_store.load_profile(identity.workspace_id).presence == StatePresence.CLEARED
    assert session.workspace_preferences == Preferences()
    assert session.profile is None
    unchanged = service.apply_command(
        ConfigurationCommand(scope="workspace", target="preferences", operation="reset")
    )
    assert unchanged.status == ConfigurationChangeStatus.UNCHANGED
    assert unchanged.revision == preferences_reset.revision


def test_preflight_has_no_write_and_rejects_missing_or_read_only_state(tmp_path):
    app, identity, _, service = _products(tmp_path / "valid", profile=Profile(name="demo"))
    command = ConfigurationCommand(
        scope="workspace",
        target="profile",
        operation="set",
        path="summary",
        value="summary",
    )
    before = set(app.data_root.root.rglob("*"))
    assert service.preflight(command).status == ConfigurationChangeStatus.APPLIED
    assert set(app.data_root.root.rglob("*")) == before

    _, _, _, missing_service = _products(tmp_path / "missing")
    with pytest.raises(ConfigurationNotFoundError):
        missing_service.preflight(command)

    app.project_store.write_profile(identity.workspace_id, Profile(name="demo"))
    profile_path = app.data_root.workspaces_path / identity.workspace_id / "profile.yaml"
    profile_path.write_text("schema_version: 99\nrevision: 3\n", encoding="utf-8")
    with pytest.raises(ConfigurationReadOnlyError):
        service.preflight(command)


@pytest.mark.parametrize(
    ("path", "value"),
    [("language", 42), ("response_detail", "verbose")],
)
def test_assignment_validation_errors_are_mapped_to_configuration_validation(tmp_path, path, value):
    _, _, _, service = _products(tmp_path, profile=Profile(name="demo"))
    command = ConfigurationCommand(
        scope="workspace",
        target="preferences",
        operation="set",
        path=path,
        value=value,
    )

    with pytest.raises(ConfigurationValidationError):
        service.apply_command(command)


def test_legacy_patch_uses_one_publication_and_returns_per_operation_results(tmp_path, monkeypatch):
    app, identity, _, service = _products(tmp_path, profile=Profile(name="demo"))
    calls = []
    original = app.project_store.write_preferences

    def counted(*args, **kwargs):
        calls.append((args, kwargs))
        return original(*args, **kwargs)

    monkeypatch.setattr(app.project_store, "write_preferences", counted)
    results = service.apply(
        ConfigPatch(
            scope="workspace",
            target="preferences",
            operations=[
                ConfigPatchOperation(op="set", path="language", value="中文"),
                ConfigPatchOperation(op="append", path="instructions", value="先给结论"),
            ],
        )
    )

    assert len(calls) == 1
    assert [result.status for result in results] == [
        ConfigurationChangeStatus.APPLIED,
        ConfigurationChangeStatus.APPLIED,
    ]
    assert all(result.revision == 1 for result in results)
    assert app.project_store.load_preferences(
        identity.workspace_id
    ).value.preferences == Preferences(language="中文", instructions=["先给结论"])


def test_service_maps_revision_conflict_and_write_failure_without_projection_changes(
    tmp_path, monkeypatch
):
    app, _, session, service = _products(tmp_path, profile=Profile(name="demo"))
    command = ConfigurationCommand(
        scope="workspace",
        target="preferences",
        operation="set",
        path="language",
        value="中文",
    )
    before = session.workspace_preferences

    monkeypatch.setattr(
        app.project_store,
        "write_preferences",
        lambda *args, **kwargs: StateWriteResult(
            status=StateWriteStatus.REVISION_CONFLICT, revision=9
        ),
    )
    with pytest.raises(ConfigurationConflictError):
        service.apply_command(command)
    assert session.workspace_preferences == before

    monkeypatch.setattr(
        app.project_store,
        "write_preferences",
        lambda *args, **kwargs: StateWriteResult(status=StateWriteStatus.FAILED, error="injected"),
    )
    with pytest.raises(ConfigurationStateError):
        service.apply_command(command)
    assert session.workspace_preferences == before


@pytest.mark.asyncio
async def test_configuration_tool_approves_and_returns_only_bounded_result(tmp_path):
    app, identity, _, service = _products(tmp_path, profile=Profile(name="demo"))
    tool = make_configuration_tool(service)
    assert tool.execution_policy.approval == ToolApproval.REQUIRED
    assert tool.execution_policy.effect.value == "persistent_write"
    assert tool.definition.function.name == "update_configuration"
    assert "保存" in tool.definition.function.description
    assert "一次性" in tool.definition.function.description

    registry = ToolRegistry()
    registry.register(tool)
    approval = _Approval(approved=True)
    outcome = await ToolExecutor(
        registry.snapshot(), make_run_policy(), approval_port=approval
    ).execute(
        FunctionToolCall(
            id="c1",
            name="update_configuration",
            arguments=json.dumps(
                {
                    "scope": "workspace",
                    "target": "preferences",
                    "operation": "set",
                    "path": "language",
                    "value": "中文",
                }
            ),
        )
    )

    assert outcome.ok is True
    result = json.loads(outcome.envelope)["result"]
    assert result == {
        "operation": "set",
        "path": "language",
        "revision": 1,
        "scope": "workspace",
        "status": "applied",
        "target": "preferences",
    }
    assert not any(key in result for key in ("providers", "profile", "path_on_disk", "arguments"))
    assert len(approval.requests) == 1
    assert (
        app.project_store.load_preferences(identity.workspace_id).value.preferences.language
        == "中文"
    )


@pytest.mark.asyncio
async def test_configuration_tool_denial_unavailability_and_preflight_failure_do_not_write(
    tmp_path,
):
    app, identity, _, service = _products(tmp_path, profile=Profile(name="demo"))
    tool = make_configuration_tool(service)
    registry = ToolRegistry()
    registry.register(tool)
    call = FunctionToolCall(
        id="c1",
        name="update_configuration",
        arguments=json.dumps(
            {
                "scope": "workspace",
                "target": "preferences",
                "operation": "set",
                "path": "language",
                "value": "中文",
            }
        ),
    )
    denied = await ToolExecutor(
        registry.snapshot(), make_run_policy(), approval_port=_Approval(False)
    ).execute(call)
    unavailable = await ToolExecutor(registry.snapshot(), make_run_policy()).execute(call)
    assert denied.error_code == ToolErrorCode.APPROVAL_REJECTED
    assert unavailable.error_code == ToolErrorCode.APPROVAL_UNAVAILABLE
    assert app.project_store.load_preferences(identity.workspace_id).value is None

    profile_path = app.data_root.workspaces_path / identity.workspace_id / "profile.yaml"
    profile_path.write_text("schema_version: 99\nrevision: 3\n", encoding="utf-8")
    blocked_approval = _Approval(True)
    blocked = await ToolExecutor(
        registry.snapshot(), make_run_policy(), approval_port=blocked_approval
    ).execute(
        FunctionToolCall(
            id="c2",
            name="update_configuration",
            arguments=json.dumps(
                {
                    "scope": "workspace",
                    "target": "profile",
                    "operation": "set",
                    "path": "summary",
                    "value": "blocked",
                }
            ),
        )
    )
    assert blocked.error_code == ToolErrorCode.EXECUTION_FAILED
    assert blocked_approval.requests == []


@pytest.mark.asyncio
async def test_configuration_sensitive_path_fails_before_approval(tmp_path):
    app, identity, _, service = _products(tmp_path, profile=Profile(name="demo"))
    tool = make_configuration_tool(service)
    registry = ToolRegistry()
    registry.register(tool)
    approval = _Approval(approved=True)

    outcome = await ToolExecutor(
        registry.snapshot(), make_run_policy(), approval_port=approval
    ).execute(
        FunctionToolCall(
            id="sensitive",
            name="update_configuration",
            arguments=(
                '{"scope":"workspace","target":"preferences","operation":"set",'
                '"path":"workspace_id","value":"secret"}'
            ),
        )
    )

    assert outcome.error_code == ToolErrorCode.INVALID_ARGUMENTS
    assert approval.requests == []
    assert app.project_store.load_preferences(identity.workspace_id).value is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "user_input, reply",
    [
        ("解释一下当前工作空间的状态", "当前状态是……"),
        ("这次回答请简洁一点", "好的，我会在本次回答中简洁说明。"),
        ("不要把中文偏好保存下来", "好的，不会保存这项偏好。"),
        ("“请记住中文”只是一个例子，不要执行", "这是一个例子，不会执行配置。"),
        ("假设我想把默认语言改成中文，会发生什么？", "这只是一个假设，不会修改配置。"),
        ("把默认回复改成中文", "你希望只在本次会话还是当前工作空间保存？"),
    ],
)
async def test_non_persistence_or_ambiguous_inputs_stay_in_ordinary_chat(
    tmp_path, user_input, reply
):
    app = build_application(state_root=tmp_path / "state", credentials=MemoryCredentialStore())
    project = tmp_path / "project"
    project.mkdir()
    identity = app.workspace_service.confirm(app.workspace_service.resolve(project))
    provider = ScriptedModelProvider([AssistantMessage(content=reply)])
    approval = _Approval(approved=True)
    session_app = build_session_application(
        app,
        identity,
        provider=provider,
        model=ModelRef(provider_id="p", model_id="m"),
        approval_port=approval,
    )

    items = [item async for item in session_app.orchestrator.stream(user_input)]

    assert items[-1].action is None
    assert provider.stream_tools[0]
    assert approval.requests == []
    assert session_app.session.preferences == Preferences()
    assert app.project_store.load_preferences(identity.workspace_id).value is None


@pytest.mark.asyncio
async def test_mixed_work_and_configuration_calls_share_one_public_turn(tmp_path):
    app = build_application(state_root=tmp_path / "state", credentials=MemoryCredentialStore())
    project = tmp_path / "project"
    project.mkdir()
    identity = app.workspace_service.confirm(app.workspace_service.resolve(project))
    provider = ScriptedModelProvider(
        [
            AssistantMessage(
                tool_calls=(
                    FunctionToolCall(
                        id="lookup",
                        name="lookup_record",
                        arguments='{"dataset":"plans","key":"pro"}',
                    ),
                    _configuration_call(
                        "config",
                        {
                            "scope": "session",
                            "target": "preferences",
                            "operation": "set",
                            "path": "language",
                            "value": "中文",
                        },
                    ),
                )
            ),
            AssistantMessage(content="已查询方案并设置本次回复语言。"),
        ]
    )
    approval = _Approval(approved=True)
    session_app = build_session_application(
        app,
        identity,
        provider=provider,
        model=ModelRef(provider_id="p", model_id="m"),
        approval_port=approval,
    )

    items = [
        item
        async for item in session_app.orchestrator.stream("查询 Pro 方案，并把本次回复改成中文")
    ]

    assert items[-1].action is None
    assert session_app.session.preferences.language == "中文"
    assert len(approval.requests) == 1
    assert [message.role for message in session_app.session.messages] == [
        "user",
        "assistant",
        "tool",
        "tool",
        "assistant",
    ]


@pytest.mark.asyncio
async def test_configuration_tool_cancellation_does_not_reach_handler(tmp_path):
    _, _, _, service = _products(tmp_path, profile=Profile(name="demo"))
    tool = make_configuration_tool(service)
    registry = ToolRegistry()
    registry.register(tool)
    approval = _BlockingApproval()
    executor = ToolExecutor(registry.snapshot(), make_run_policy(), approval_port=approval)
    call = FunctionToolCall(
        id="c1",
        name="update_configuration",
        arguments='{"scope":"session","target":"preferences","operation":"set","path":"language","value":"中文"}',
    )
    task = asyncio.create_task(executor.execute(call))
    await approval.started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_terminal_approval_timeout_cancels_prompt_and_returns_ordinary_timeout(tmp_path):
    _, _, session, service = _products(tmp_path, profile=Profile(name="demo"))
    builder = make_context_builder(tool_timeout_seconds=0.01)
    registry = ToolRegistry()
    registry.register(make_configuration_tool(service))
    terminal = _BlockingApprovalTerminal()
    provider = ScriptedModelProvider(
        [
            AssistantMessage(
                tool_calls=(
                    _configuration_call(
                        "timeout",
                        {
                            "scope": "session",
                            "target": "preferences",
                            "operation": "set",
                            "path": "language",
                            "value": "中文",
                        },
                    ),
                )
            ),
            AssistantMessage(content="审批超时后恢复。"),
        ]
    )
    executor = ToolExecutor(
        registry.snapshot(),
        builder.run_policy,
        approval_port=TerminalApprovalPort(terminal, object()),
    )

    events = [
        item
        async for item in AgentLoop(
            provider,
            ModelRef(provider_id="p", model_id="m"),
            builder,
            tool_executor=executor,
        ).run_task(session, "请修改本次会话语言")
    ]

    tool_messages = [message for message in session.messages if message.role == "tool"]
    assert terminal.cancelled is True
    assert json.loads(tool_messages[0].content)["error"]["code"] == "timeout"
    assert events[-1].payload["finish_reason"] == FinishReason.STOP.value


@pytest.mark.asyncio
async def test_production_composition_uses_one_agent_loop_and_refreshes_state_projection(tmp_path):
    app = build_application(state_root=tmp_path / "state", credentials=MemoryCredentialStore())
    project = tmp_path / "project"
    project.mkdir()
    identity = app.workspace_service.confirm(app.workspace_service.resolve(project))
    provider = ScriptedModelProvider(
        [
            AssistantMessage(
                tool_calls=(
                    _configuration_call(
                        "c1",
                        {
                            "scope": "session",
                            "target": "preferences",
                            "operation": "set",
                            "path": "language",
                            "value": "中文",
                        },
                    ),
                )
            ),
            AssistantMessage(content="已更新本次会话偏好。"),
        ]
    )
    approval = _Approval(True)
    session_app = build_session_application(
        app,
        identity,
        provider=provider,
        model=ModelRef(provider_id="p", model_id="m"),
        approval_port=approval,
    )

    items = [item async for item in session_app.orchestrator.stream("请把这次回复改成中文")]
    events = [item for item in items if isinstance(item, AgentEvent)]

    assert events[-1].payload["finish_reason"] == FinishReason.STOP.value
    assert session_app.session.preferences.language == "中文"
    assert session_app.session.dirty is True
    assert [message.role for message in session_app.session.messages] == [
        "user",
        "assistant",
        "tool",
        "assistant",
    ]
    assert {tool.function.name for tool in provider.stream_tools[0]} == {
        "list_directory",
        "read_file",
        "find_files",
        "search_text",
        "update_configuration",
        "apply_patch",
        "write_file",
        "show_changes",
        "run_command",
        "git_status",
        "git_diff",
    }
    assert "中文" in str(provider.stream_calls[1])
    assert len(approval.requests) == 1
    assert '"scope"' not in str(approval.requests[0])
    assert not any(
        isinstance(item, DispatchResult) and item.action == "config_preview" for item in items
    )


@pytest.mark.asyncio
async def test_multiple_configuration_calls_are_serial_and_partially_persistent(tmp_path):
    app = build_application(state_root=tmp_path / "state", credentials=MemoryCredentialStore())
    project = tmp_path / "project"
    project.mkdir()
    identity = app.workspace_service.confirm(app.workspace_service.resolve(project))
    app.project_store.write_profile(identity.workspace_id, Profile(name="demo"))
    provider = ScriptedModelProvider(
        [
            AssistantMessage(
                tool_calls=(
                    _configuration_call(
                        "c1",
                        {
                            "scope": "workspace",
                            "target": "preferences",
                            "operation": "set",
                            "path": "language",
                            "value": "中文",
                        },
                    ),
                    _configuration_call(
                        "c2",
                        {
                            "scope": "workspace",
                            "target": "profile",
                            "operation": "set",
                            "path": "summary",
                            "value": "不会写入",
                        },
                    ),
                )
            ),
            AssistantMessage(content="第一项已应用，第二项未获批准。"),
        ]
    )
    approval = _SequenceApproval([True, False])
    session_app = build_session_application(
        app,
        identity,
        provider=provider,
        model=ModelRef(provider_id="p", model_id="m"),
        approval_port=approval,
    )

    items = [item async for item in session_app.orchestrator.stream("完成两项配置")]
    tool_messages = [message for message in session_app.session.messages if message.role == "tool"]

    assert len(approval.requests) == 2
    assert len(tool_messages) == 2
    assert json.loads(tool_messages[0].content)["result"]["status"] == "applied"
    assert json.loads(tool_messages[1].content)["error"]["code"] == "approval_rejected"
    assert (
        app.project_store.load_preferences(identity.workspace_id).value.preferences.language
        == "中文"
    )
    assert app.project_store.load_profile(identity.workspace_id).value.profile.summary is None
    assert session_app.session.log.unresolved_call_ids == ()
    assert any(isinstance(item, DispatchResult) for item in items)


@pytest.mark.asyncio
async def test_configuration_cancellation_after_first_call_closes_only_pending_call(tmp_path):
    app = build_application(state_root=tmp_path / "state", credentials=MemoryCredentialStore())
    project = tmp_path / "project"
    project.mkdir()
    identity = app.workspace_service.confirm(app.workspace_service.resolve(project))
    provider = ScriptedModelProvider(
        [
            AssistantMessage(
                tool_calls=(
                    _configuration_call(
                        "c1",
                        {
                            "scope": "session",
                            "target": "preferences",
                            "operation": "set",
                            "path": "language",
                            "value": "中文",
                        },
                    ),
                    _configuration_call(
                        "c2",
                        {
                            "scope": "session",
                            "target": "preferences",
                            "operation": "set",
                            "path": "response_detail",
                            "value": "detailed",
                        },
                    ),
                )
            )
        ]
    )
    approval = _ApplyThenBlockApproval()
    session_app = build_session_application(
        app,
        identity,
        provider=provider,
        model=ModelRef(provider_id="p", model_id="m"),
        approval_port=approval,
    )

    async def collect():
        return [item async for item in session_app.orchestrator.stream("执行两项会话配置")]

    task = asyncio.create_task(collect())
    await approval.second_started.wait()
    task.cancel()
    items = await task
    events = [item for item in items if isinstance(item, AgentEvent)]
    assert events[-1].payload["finish_reason"] == FinishReason.CANCELLED.value
    assert session_app.session.preferences.language == "中文"
    assert session_app.session.preferences.response_detail is None
    tool_messages = [message for message in session_app.session.messages if message.role == "tool"]
    assert json.loads(tool_messages[0].content)["result"]["status"] == "applied"
    assert json.loads(tool_messages[1].content)["error"]["code"] == "cancelled"
    assert session_app.session.log.unresolved_call_ids == ()


def test_service_error_types_are_stable_and_legacy_model_has_no_reset():
    assert "reset" not in ConfigPatchOperation.model_json_schema()["properties"]["op"]["enum"]
    assert ConfigurationConflictError.__name__
    assert ConfigurationValidationError.__name__
