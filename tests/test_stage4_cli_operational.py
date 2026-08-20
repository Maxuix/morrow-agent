"""CLI state commands use the unified application boundary."""

from __future__ import annotations

import json

from typer.testing import CliRunner

import morrow.interfaces.cli as cli
from morrow.adapters.state.operational import OperationalStore
from morrow.core.application import QueryPage
from morrow.core.artifacts import (
    ArtifactKind,
    ArtifactMetadata,
    ArtifactSensitivity,
    ArtifactState,
)
from morrow.core.domain import DurableTaskRun, sha256_digest
from morrow.interfaces.cli import app
from morrow.testing import FixedClock


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


def test_cli_state_services_share_the_store_clock(tmp_path, monkeypatch):
    root = tmp_path / "state"
    clock = FixedClock()
    store = OperationalStore(root, clock=clock, maintenance_timeout=0)
    monkeypatch.setattr(cli, "OperationalStore", lambda _root: store)

    _application, handle, api, _doctor, _backup = cli._state_services(
        state_root=root,
        workspace_id="ws_1",
        directory=tmp_path,
        write=True,
    )
    try:
        assert api.clock() == clock.value
        assert api.tasks.clock() == clock.value
        assert api.artifacts.clock() == clock.value
        assert api.checkpoints.clock() == clock.value
        assert api.forks.clock() == clock.value
    finally:
        handle.close()


def test_cli_session_status_reads_archived_session_without_resuming_it(tmp_path):
    root = tmp_path / "state"
    OperationalStore(root, maintenance_timeout=0).initialize().close()
    runner = CliRunner()
    created = runner.invoke(
        app,
        [
            "session",
            "create",
            "--workspace-id",
            "ws_1",
            "--session-id",
            "ses_archived",
            "--command-id",
            "cmd_create",
            "--state-root",
            str(root),
        ],
    )
    assert created.exit_code == 0, created.stdout
    archived = runner.invoke(
        app,
        [
            "session",
            "archive",
            "ses_archived",
            "--workspace-id",
            "ws_1",
            "--command-id",
            "cmd_archive",
            "--state-root",
            str(root),
        ],
    )
    assert archived.exit_code == 0, archived.stdout
    before = OperationalStore(root).layout.database.stat().st_mtime_ns

    status = runner.invoke(
        app,
        [
            "session",
            "status",
            "ses_archived",
            "--workspace-id",
            "ws_1",
            "--state-root",
            str(root),
        ],
    )

    assert status.exit_code == 0, status.stdout
    assert "lifecycle: archived" in status.stdout
    assert OperationalStore(root).layout.database.stat().st_mtime_ns == before


def test_cli_doctor_fails_when_missing_store_needs_repair(tmp_path):
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
    assert diagnosed.exit_code == 2, diagnosed.stdout
    assert "health: needs_repair" in diagnosed.stdout


def test_cli_session_list_exposes_next_cursor_and_json_page(tmp_path):
    root = tmp_path / "state"
    handle = OperationalStore(root, maintenance_timeout=0).initialize()
    handle.close()
    runner = CliRunner()
    for number in range(3):
        created = runner.invoke(
            app,
            [
                "session",
                "create",
                "--workspace-id",
                "ws_1",
                "--session-id",
                f"ses_{number}",
                "--command-id",
                f"cmd_{number}",
                "--state-root",
                str(root),
            ],
        )
        assert created.exit_code == 0, created.stdout

    first_page = runner.invoke(
        app,
        [
            "session",
            "list",
            "--workspace-id",
            "ws_1",
            "--limit",
            "2",
            "--state-root",
            str(root),
        ],
    )
    assert first_page.exit_code == 0, first_page.stdout
    assert "next_cursor: 2" in first_page.stdout

    json_page = runner.invoke(
        app,
        [
            "session",
            "list",
            "--workspace-id",
            "ws_1",
            "--limit",
            "2",
            "--json",
            "--state-root",
            str(root),
        ],
    )
    assert json_page.exit_code == 0, json_page.stdout
    payload = json.loads(json_page.stdout)
    assert len(payload["items"]) == 2
    assert payload["next_cursor"] == "2"


def test_cli_task_and_artifact_lists_preserve_page_metadata(monkeypatch):
    calls = {}

    class FakeApi:
        def list_tasks(self, session_id, *, cursor, limit):
            calls["tasks"] = (session_id, cursor, limit)
            return QueryPage(
                (DurableTaskRun(task_run_id="task_1", session_id="ses_1", workspace_id="ws_1"),),
                "7",
            )

        def list_artifacts(self, *, session_id, task_run_id, cursor, limit):
            calls["artifacts"] = (session_id, task_run_id, cursor, limit)
            return QueryPage(
                (
                    ArtifactMetadata(
                        artifact_id="art_1",
                        workspace_id="ws_1",
                        kind=ArtifactKind.TEST_REPORT,
                        sensitivity=ArtifactSensitivity.NON_SENSITIVE,
                        state=ArtifactState.AVAILABLE,
                        sha256=sha256_digest(b"report"),
                        byte_size=6,
                    ),
                ),
                "9",
            )

    monkeypatch.setattr(
        cli,
        "_state_services",
        lambda **_kwargs: (None, None, FakeApi(), None, None),
    )
    runner = CliRunner()
    tasks = runner.invoke(
        app,
        [
            "task",
            "list",
            "ses_1",
            "--workspace-id",
            "ws_1",
            "--cursor",
            "5",
            "--limit",
            "1",
        ],
    )
    assert tasks.exit_code == 0, tasks.stdout
    assert "next_cursor: 7" in tasks.stdout
    assert calls["tasks"] == ("ses_1", "5", 1)

    artifacts = runner.invoke(
        app,
        [
            "artifact",
            "list",
            "--workspace-id",
            "ws_1",
            "--cursor",
            "8",
            "--limit",
            "1",
            "--json",
        ],
    )
    assert artifacts.exit_code == 0, artifacts.stdout
    payload = json.loads(artifacts.stdout)
    assert payload["items"][0]["artifact_id"] == "art_1"
    assert payload["next_cursor"] == "9"
    assert calls["artifacts"] == (None, None, "8", 1)


def test_recovery_resolve_help_explains_generated_id_and_provider_boundary():
    result = CliRunner().invoke(app, ["recovery", "resolve", "--help"])

    assert result.exit_code == 0, result.stdout
    assert "自动生成" in result.stdout
    assert "不调用 Provider" in result.stdout
