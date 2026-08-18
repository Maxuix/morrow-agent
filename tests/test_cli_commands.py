from __future__ import annotations

import sys
from types import SimpleNamespace

from typer.testing import CliRunner

from morrow.bootstrap import build_application
from morrow.core.models import (
    CredentialRef,
    LastTestResult,
    ModelErrorCode,
    ModelRef,
    ProviderConfig,
    ProviderModelConfig,
)
from morrow.interfaces import cli as cli_module
from morrow.interfaces.cli import app


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
