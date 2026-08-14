from __future__ import annotations

from typer.testing import CliRunner

from morrow.core.models import LastTestResult, ModelErrorCode
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
