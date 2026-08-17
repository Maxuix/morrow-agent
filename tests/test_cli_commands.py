from __future__ import annotations

from typer.testing import CliRunner

from morrow.bootstrap import build_application
from morrow.core.models import LastTestResult, ModelErrorCode, ProviderConfig, ProviderModelConfig
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
