from __future__ import annotations

from typer.testing import CliRunner

from morrow.interfaces.cli import app


def test_local_provider_and_model_commands_are_offline(tmp_path):
    runner = CliRunner()
    provider = runner.invoke(app, ["provider", "list", "--state-root", str(tmp_path / "state")])
    current = runner.invoke(app, ["model", "current", "--state-root", str(tmp_path / "state")])
    assert provider.exit_code == 0, provider.output
    assert current.exit_code == 0, current.output
    assert "未配置" in current.output
