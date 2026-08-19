"""CLI state commands use the unified application boundary."""

from __future__ import annotations

from typer.testing import CliRunner

from morrow.adapters.state.operational import OperationalStore
from morrow.interfaces.cli import app


def test_cli_session_and_doctor_flows_share_the_operational_store(tmp_path):
    root = tmp_path / "state"
    handle = OperationalStore(root, maintenance_timeout=0).initialize()
    handle.close()
    runner = CliRunner()
    created = runner.invoke(
        app,
        [
            "session",
            "create",
            "--workspace-id",
            "ws_1",
            "--session-id",
            "ses_1",
            "--command-id",
            "cmd_cli",
            "--state-root",
            str(root),
        ],
    )
    assert created.exit_code == 0, created.stdout
    listed = runner.invoke(
        app,
        ["session", "list", "--workspace-id", "ws_1", "--state-root", str(root)],
    )
    assert listed.exit_code == 0, listed.stdout
    assert "ses_1" in listed.stdout
    diagnosed = runner.invoke(
        app,
        ["state", "doctor", "--workspace-id", "ws_1", "--state-root", str(root)],
    )
    assert diagnosed.exit_code == 0, diagnosed.stdout
    assert "health: ok" in diagnosed.stdout


def test_cli_doctor_can_report_a_missing_store_without_opening_it(tmp_path):
    runner = CliRunner()
    diagnosed = runner.invoke(
        app,
        [
            "state",
            "doctor",
            "--workspace-id",
            "ws_1",
            "--state-root",
            str(tmp_path / "missing-state"),
        ],
    )
    assert diagnosed.exit_code == 0, diagnosed.stdout
    assert "health: needs_repair" in diagnosed.stdout
