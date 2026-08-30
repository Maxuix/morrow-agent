from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

from typer.testing import CliRunner

from morrow.adapters.state.operational import OperationalStore
from morrow.application.preferences.queries import (
    PreferenceContextStatusView,
    PreferenceScopeStatus,
)
from morrow.bootstrap import build_application
from morrow.core.models import (
    CredentialRef,
    LastTestResult,
    ModelErrorCode,
    ModelFailure,
    ModelFailureOrigin,
    ModelProviderError,
    ModelRef,
    ProviderConfig,
    ProviderModelConfig,
)
from morrow.core.store import StoreOpenMode
from morrow.interfaces import cli as cli_module
from morrow.interfaces.cli import app

FAKE_MCP_SERVER = Path(__file__).parent / "spikes" / "fake_mcp_stdio_server.py"


def test_preferences_status_keeps_injection_and_memory_selection_separate(monkeypatch):
    class FakeApi:
        def preference_context_status(self, *, session_id):
            assert session_id == "ses_1"
            return PreferenceContextStatusView(
                global_preferences=PreferenceScopeStatus(
                    revision=2, active=1, disabled=0, deleted=0
                ),
                workspace_preferences=PreferenceScopeStatus(
                    revision=3, active=2, disabled=1, deleted=0
                ),
                injected_count=2,
                injected_digest="a" * 64,
                omitted_count=1,
                source_scopes=("global", "workspace"),
                memory_selection_id="msel_1",
                memory_selection_revision=4,
                memory_selection_item_count=0,
            )

    monkeypatch.setattr(
        cli_module,
        "_state_services",
        lambda **_kwargs: (None, "handle", FakeApi(), None, None),
    )
    monkeypatch.setattr(cli_module, "_close_state", lambda _handle: None)

    result = CliRunner().invoke(
        app,
        ["preferences", "status", "--session-id", "ses_1", "--json"],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["injected_count"] == 2
    assert payload["omitted_count"] == 1
    assert payload["memory_selection_id"] == "msel_1"
    assert payload["memory_selection_item_count"] == 0


def test_preferences_stale_revision_reports_actionable_conflict(tmp_path):
    runner = CliRunner()
    state_root = tmp_path / "state"
    common = [
        "--scope",
        "global",
        "--workspace-id",
        "ws_test",
        "--state-root",
        str(state_root),
        "--json",
    ]
    first = runner.invoke(
        app,
        ["preferences", "add", "first", "--expected-revision", "0", *common],
    )
    stale = runner.invoke(
        app,
        ["preferences", "add", "second", "--expected-revision", "0", *common],
    )

    assert first.exit_code == 0, first.output
    assert stale.exit_code == 2
    assert "preference_conflict: Preference document revision is stale" in stale.output
    assert "application command failed" not in stale.output


def test_mcp_invalid_server_id_reports_validation_detail(tmp_path):
    result = CliRunner().invoke(
        app,
        [
            "mcp",
            "add",
            "invalid-id",
            "--executable",
            sys.executable,
            "--state-root",
            str(tmp_path / "state"),
        ],
    )

    assert result.exit_code == 2
    assert "invalid_configuration" in result.output
    assert "server_id" in result.output
    assert "must match mcp_" in result.output
    assert "MCP operation failed" not in result.output


def test_mcp_add_initializes_operational_store_before_publishing_definition(tmp_path):
    state_root = tmp_path / "state"
    result = CliRunner().invoke(
        app,
        [
            "mcp",
            "add",
            "mcp_test",
            "--executable",
            sys.executable,
            "--arg",
            str(FAKE_MCP_SERVER),
            "--cwd-policy",
            "managed",
            "--visibility",
            "read_write",
            "--allow-tool",
            "echo",
            "--tool-effect",
            "echo=none",
            "--state-root",
            str(state_root),
        ],
    )

    assert result.exit_code == 0, result.output
    with OperationalStore(state_root).open(StoreOpenMode.READ_ONLY) as handle:
        assert handle.schema_version > 0


def test_mcp_queries_open_initialized_store_read_only(tmp_path, monkeypatch):
    state_root = tmp_path / "state"
    runner = CliRunner()
    added = runner.invoke(
        app,
        [
            "mcp",
            "add",
            "mcp_test",
            "--executable",
            sys.executable,
            "--arg",
            str(FAKE_MCP_SERVER),
            "--cwd-policy",
            "managed",
            "--visibility",
            "read_write",
            "--allow-tool",
            "echo",
            "--tool-effect",
            "echo=none",
            "--state-root",
            str(state_root),
        ],
    )
    assert added.exit_code == 0, added.output

    modes = []
    real_open = OperationalStore.open

    def recording_open(store, mode):
        modes.append(mode)
        return real_open(store, mode)

    monkeypatch.setattr(OperationalStore, "open", recording_open)
    listed = runner.invoke(app, ["mcp", "list", "--state-root", str(state_root)])

    assert listed.exit_code == 0, listed.output
    assert modes == [StoreOpenMode.READ_ONLY]


def test_local_provider_and_model_commands_are_offline(tmp_path):
    runner = CliRunner()
    provider = runner.invoke(app, ["provider", "list", "--state-root", str(tmp_path / "state")])
    current = runner.invoke(app, ["model", "current", "--state-root", str(tmp_path / "state")])
    assert provider.exit_code == 0, provider.output
    assert current.exit_code == 0, current.output
    assert "未配置" in current.output


def test_failed_provider_test_returns_non_zero_exit(monkeypatch):
    class ProviderServiceStub:
        def test(self, provider_id):
            return LastTestResult(
                ok=False,
                error_code=ModelErrorCode.AUTH,
                message="认证失败",
            )

    application = type("Application", (), {"provider_service": ProviderServiceStub()})()
    monkeypatch.setattr(cli_module, "build_application", lambda **kwargs: application)

    result = CliRunner().invoke(app, ["provider", "test", "opencode-go"])

    assert result.exit_code == 2
    assert "auth" in result.output
    assert "API Key" in result.output


def test_unknown_provider_test_returns_controlled_error(tmp_path):
    result = CliRunner().invoke(
        app,
        ["provider", "test", "nonexistent", "--state-root", str(tmp_path / "state")],
    )

    assert result.exit_code == 2
    assert "未知 Provider: nonexistent" in result.output
    assert "ValueError" not in result.output
    assert "凭据" not in result.output
    assert "Traceback" not in result.output


def test_provider_test_missing_credential_returns_controlled_error(tmp_path):
    state_root = tmp_path / "state"
    written = build_application(state_root=state_root).global_store.update(
        lambda value: value.model_copy(
            update={
                "providers": {
                    "demo": ProviderConfig(
                        adapter="openai-compatible",
                        base_url="https://example.test",
                        models={"m": ProviderModelConfig(api_model_id="m")},
                    )
                }
            }
        )
    )
    assert written.status.value == "ok"

    result = CliRunner().invoke(
        app,
        ["provider", "test", "demo", "--state-root", str(state_root)],
    )

    assert result.exit_code == 2
    assert "Provider 凭据不可用" in result.output
    assert "ValueError" not in result.output
    assert "Traceback" not in result.output


def test_unknown_provider_show_returns_controlled_error(tmp_path):
    result = CliRunner().invoke(
        app,
        ["provider", "show", "nonexistent", "--state-root", str(tmp_path / "state")],
    )

    assert result.exit_code == 2
    assert "未知 Provider: nonexistent" in result.output
    assert "Traceback" not in result.output


def test_provider_show_keychain_failure_is_sanitized(tmp_path, monkeypatch):
    state_root = tmp_path / "state"
    written = build_application(state_root=state_root).global_store.update(
        lambda value: value.model_copy(
            update={
                "providers": {
                    "demo": ProviderConfig(
                        adapter="openai-compatible",
                        base_url="https://example.test",
                        credential_ref=CredentialRef(ref="provider:demo:test"),
                        models={"m": ProviderModelConfig(api_model_id="m")},
                    )
                }
            }
        )
    )
    assert written.status.value == "ok"

    monkeypatch.setitem(
        sys.modules,
        "keyring",
        SimpleNamespace(
            get_password=lambda *args, **kwargs: (_ for _ in ()).throw(
                RuntimeError("(-50, Unknown Error)")
            )
        ),
    )

    result = CliRunner().invoke(
        app,
        ["provider", "show", "demo", "--state-root", str(state_root)],
    )

    assert result.exit_code == 2
    assert "不可用" in result.output
    assert "解锁 Keychain" in result.output
    assert "Traceback" not in result.output
    assert "(-50" not in result.output
    assert "Unknown Error" not in result.output


def test_provider_presets_lists_known_presets():
    result = CliRunner().invoke(app, ["provider", "presets"])

    assert result.exit_code == 0, result.output
    assert "opencode-go\topencode-go/deepseek-v4-flash" in result.output
    assert "opencode-go-mimo\topencode-go/mimo-v2.5" in result.output


def test_provider_add_help_lists_presets():
    result = CliRunner().invoke(app, ["provider", "add", "--help"])

    assert result.exit_code == 0, result.output
    compact = result.output.replace("\n", "").replace(" ", "")
    assert "opencode-go" in compact
    assert "opencode-go-mimo" in compact
    assert "provider presets" in result.output


def test_provider_add_reports_whether_active_model_switched(monkeypatch):
    class ProviderServiceStub:
        def add(self, preset, secret, **kwargs):
            del preset, secret, kwargs
            return ModelRef(provider_id="opencode-go", model_id="mimo-v2.5")

        def current_model(self):
            return ModelRef(provider_id="opencode-go", model_id="deepseek-v4-flash")

    monkeypatch.setattr(
        cli_module,
        "build_application",
        lambda **kwargs: type("Application", (), {"provider_service": ProviderServiceStub()})(),
    )
    monkeypatch.setattr(cli_module, "_secret", lambda provider_id="opencode-go": "secret")

    result = CliRunner().invoke(app, ["provider", "add", "--preset", "opencode-go-mimo"])

    assert result.exit_code == 0, result.output
    assert "已配置 opencode-go/mimo-v2.5" in result.output
    assert "当前模型未切换：opencode-go/deepseek-v4-flash" in result.output


def test_provider_add_reports_typed_sanitized_connection_failure(monkeypatch):
    class ProviderServiceStub:
        def add(self, preset, secret, **kwargs):
            del preset, secret, kwargs
            raise ModelProviderError(
                ModelFailure(
                    code=ModelErrorCode.NETWORK,
                    origin=ModelFailureOrigin.PROVIDER,
                    retryable=True,
                    message="raw transport detail",
                )
            )

    monkeypatch.setattr(
        cli_module,
        "build_application",
        lambda **kwargs: type("Application", (), {"provider_service": ProviderServiceStub()})(),
    )
    monkeypatch.setattr(cli_module, "_secret", lambda provider_id="opencode-go": "secret")

    result = CliRunner().invoke(app, ["provider", "add", "--preset", "opencode-go"])

    assert result.exit_code == 2
    assert "Provider 添加失败（network）" in result.output
    assert "检查网络或代理设置后重试" in result.output
    assert "ModelProviderError" not in result.output
    assert "raw transport detail" not in result.output


def test_unknown_workspace_relink_returns_controlled_error(tmp_path):
    target = tmp_path / "project"
    target.mkdir()

    result = CliRunner().invoke(
        app,
        [
            "workspace",
            "relink",
            "nonexistent",
            "--dir",
            str(target),
            "--state-root",
            str(tmp_path / "state"),
        ],
        input="y\n",
    )

    assert result.exit_code == 2
    assert "未知工作空间: nonexistent" in result.output
    assert "Traceback" not in result.output
