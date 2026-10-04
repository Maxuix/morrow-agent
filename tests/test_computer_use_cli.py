"""Real CLI/config round trips without importing the optional SDK or granting a run."""

import json

from typer.testing import CliRunner

from morrow.adapters import computer_use
from morrow.bootstrap import build_application
from morrow.core.computer_use import ComputerUsePreflight
from morrow.interfaces import computer_cli
from morrow.interfaces.cli import app


def test_cli_config_occ_budgets_and_disabled_probe(tmp_path, monkeypatch):
    state = tmp_path / "state"
    application = build_application(state_root=state)
    original = application.global_store.load().value
    probes = []

    class Diagnostic:
        def preflight(self):
            probes.append(True)
            return ComputerUsePreflight(status="unavailable", reason="native_unverified")

    monkeypatch.setattr(computer_cli, "build_computer_use_lifecycle", lambda *_: Diagnostic())
    count = computer_use.DRIVER_CONSTRUCTION_COUNT
    runner = CliRunner()
    common = ["--state-root", str(state), "--json"]
    status = runner.invoke(app, ["computer", "status", *common])
    assert status.exit_code == 0, status.output
    initial = json.loads(status.output)
    assert initial["host"]["reason"] == "disabled"
    assert probes == []
    request = [
        "computer",
        "configure",
        "--enable",
        "--mode",
        "hybrid",
        "--max-call-seconds",
        "7",
        "--max-observation-age-seconds",
        "5",
        "--expected-revision",
        str(initial["revision"]),
        *common,
    ]
    saved = runner.invoke(app, request)
    assert saved.exit_code == 0, saved.output
    saved = json.loads(saved.output)
    assert saved["settings"]["enabled"] is True
    assert saved["settings"]["max_call_seconds"] == 7
    assert saved["settings"]["max_observation_age_seconds"] == 5
    assert saved["host"]["reason"] == "native_unverified"
    assert saved["applies_to"] == "future_runs"
    assert runner.invoke(app, request).exit_code == 2
    config = application.global_store.load().value
    assert config.providers == original.providers
    assert config.chat_settings == original.chat_settings
    assert config.active_model == original.active_model
    assert computer_use.DRIVER_CONSTRUCTION_COUNT == count
    assert not application.data_root.store_path.exists()
    disabled = runner.invoke(
        app,
        [
            "computer",
            "configure",
            "--disable",
            "--expected-revision",
            str(saved["revision"]),
            *common,
        ],
    )
    assert disabled.exit_code == 0, disabled.output
    disabled = json.loads(disabled.output)
    assert disabled["settings"]["max_call_seconds"] == 7
    before = len(probes)
    assert runner.invoke(app, ["computer", "status", *common]).exit_code == 0
    assert len(probes) == before
    invalid = runner.invoke(
        app,
        [
            "computer",
            "configure",
            "--max-call-seconds",
            "61",
            "--expected-revision",
            str(disabled["revision"]),
            *common,
        ],
    )
    assert invalid.exit_code == 2
    assert application.global_store.load().revision == disabled["revision"]
