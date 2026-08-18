from pathlib import Path

from typer.testing import CliRunner

from morrow.adapters.credentials.keyring import MemoryCredentialStore
from morrow.application.configuration import UpdateConfigurationArguments
from morrow.application.context import render_system_boundary
from morrow.bootstrap import build_application, build_session_application
from morrow.core.capabilities import PermissionPreset, PermissionProfile
from morrow.core.models import (
    ConfigPatch,
    ModelRef,
    Preferences,
    Profile,
    ToolDefinition,
    ToolFunction,
)
from morrow.interfaces.cli import app
from morrow.testing import ScriptedModelProvider


def test_capability_boundary_is_derived_from_tool_inventory_without_internal_policy_details():
    tool = ToolDefinition(
        function=ToolFunction(
            name="read_file",
            description="读取工作空间内的文本文件。",
            parameters={"type": "object", "properties": {}},
        )
    )
    boundary = render_system_boundary((tool,))
    assert "read_file" in boundary
    assert "读取工作空间内的文本文件" in boundary
    assert "工作空间外" in boundary
    assert "网络" in boundary
    assert "PermissionProfile" not in boundary
    assert "manual" not in boundary
    assert "当前请求未提供可执行工具" in render_system_boundary()


def test_production_session_freezes_cli_selected_profile_and_workspace_root(tmp_path):
    app_instance = build_application(
        state_root=tmp_path / "state", credentials=MemoryCredentialStore()
    )
    project = tmp_path / "project"
    project.mkdir()
    identity = app_instance.workspace_service.confirm(
        app_instance.workspace_service.resolve(project)
    )
    selected = PermissionProfile.from_preset(PermissionPreset.AUTO_SAFE)
    session_app = build_session_application(
        app_instance,
        identity,
        provider=ScriptedModelProvider(["done"]),
        model=ModelRef(provider_id="p", model_id="m"),
        permission_profile=selected,
    )

    assert session_app.session.permission_profile == selected
    assert session_app.session.workspace_capability.root == Path(identity.path)
    policy = session_app.orchestrator.runtime.loop.tool_executor.capability_policy
    assert policy.profile == selected
    assert policy.workspace.root == Path(identity.path)


def test_auto_sandboxed_cli_fails_closed_without_a_backend(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    from morrow.adapters.local.sandbox import SandboxCapability

    monkeypatch.setattr(
        "morrow.interfaces.cli.default_sandbox_backend",
        lambda: type(
            "UnavailableBackend",
            (),
            {
                "probe": lambda self: SandboxCapability(
                    platform="test",
                    backend="test",
                    supported=False,
                    reason="test backend unavailable",
                )
            },
        )(),
    )
    result = CliRunner().invoke(
        app,
        ["--dir", str(project), "--permission-mode", "auto-sandboxed"],
    )

    assert result.exit_code == 2
    assert "Auto Sandboxed" in result.output
    assert "不会启动交互 Agent" in result.output
    assert "不会回退到 Host" in result.output


def test_configuration_and_state_models_cannot_select_or_elevate_permission_mode():
    for model in (UpdateConfigurationArguments, ConfigPatch, Preferences, Profile):
        fields = set(model.model_fields)
        assert fields.isdisjoint({"permission", "permission_mode", "access_scope", "full_access"})
