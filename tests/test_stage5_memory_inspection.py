from __future__ import annotations

import json
import sqlite3

import pytest
from typer.testing import CliRunner

from morrow.adapters.state.operational import OperationalStore
from morrow.application.api import OperationalApplicationService
from morrow.application.backup import OperationalBackupService
from morrow.application.doctor import OperationalDoctor
from morrow.application.learning.memory_backup import verify_memory_references
from morrow.application.learning.memory_selector import memory_selection_digest
from morrow.interfaces import cli as cli_module
from morrow.interfaces import learning_cli
from morrow.testing import FixedIdSource
from test_stage5_learning_cli import _command_service
from test_stage5_memory_agent_run import (
    _open,
    _persistence,
    _seed_terms,
    _session,
)


def _admitted(tmp_path, *, goal: str = "Operational SQLite"):
    handle, journal, clock = _open(tmp_path)
    _seed_terms(journal)
    session = _session()
    ids = FixedIdSource()
    ids.counts["task"] = 1
    persistence = _persistence(journal, handle, ids=ids, clock=clock, session=session)
    persistence.submit_user(
        session,
        goal,
        "client_inspection",
        turn_id="turn_inspection",
        agent_run_id="arun_inspection",
    )
    api = OperationalApplicationService(journal=journal, workspace_id="ws_1", id_source=ids)
    return handle, journal, api, session


def test_memory_selection_queries_include_agent_run_references_and_reasons(tmp_path):
    handle, journal, api, _session = _admitted(tmp_path)
    try:
        page = api.list_memory_selections(limit=50)
        assert len(page.items) == 1
        summary = page.items[0]
        assert summary.agent_run_ids == ("arun_inspection",)
        assert summary.selection_digest

        view = api.get_memory_selection(summary.selection_id)
        assert view is not None
        assert view.agent_run_ids == ("arun_inspection",)
        assert view.selection.selected_items[0].reason_codes
        assert memory_selection_digest(view.selection) == view.selection.selection_digest
        assert journal.get_agent_run("ws_1", "arun_inspection") is not None
    finally:
        handle.close()


@pytest.mark.parametrize(
    "goal",
    (
        "where is the API key stored?",
        "use sudo to inspect the unit",
        "long input " * 600,
        "inspect the status\u200b before continuing",
    ),
)
def test_memory_admission_treats_user_text_as_bounded_retrieval_input(tmp_path, goal):
    handle, journal, _api, _session = _admitted(tmp_path, goal=goal)
    try:
        assert journal.get_turn("ws_1", "turn_inspection") is not None
        run = journal.get_agent_run("ws_1", "arun_inspection")
        assert run is not None
        assert run.snapshot.memory_selection_id is not None
    finally:
        handle.close()


def test_repl_memory_selection_surface_is_bounded_and_explainable(tmp_path):
    handle, _journal, api, _session = _admitted(tmp_path)
    try:
        command_service, _ = _command_service(api, tmp_path)
        listed = command_service._memory_command(["/memory", "selection"])
        assert "Memory Selection：1 条" in listed.lines[0]
        selection_id = listed.value.items[0].selection_id
        shown = command_service._memory_command(["/memory", "selection", "show", selection_id])
        assert "AgentRuns：arun_inspection" in shown.lines[2]
        assert "reasons=" in shown.lines[3]
        assert "Operational state is persisted" not in "\n".join(shown.lines)
    finally:
        handle.close()


def test_memory_typer_selection_commands_are_registered():
    runner = CliRunner()
    result = runner.invoke(cli_module.app, ["memory", "selection", "--help"])
    assert result.exit_code == 0
    assert "list" in result.stdout
    assert "show" in result.stdout
    assert learning_cli.selection_app.registered_commands


def test_doctor_checks_selection_references_and_rebuildable_terms(tmp_path):
    handle, journal, _api, _session = _admitted(tmp_path)
    try:
        store = OperationalStore(tmp_path / "state")
        report = OperationalDoctor(store).inspect("ws_1")
        assert report.health.value in {"ok", "needs_recovery"}
        assert report.counts["memory_selections"] == 1
        assert report.counts["memory_agent_runs"] == 1
        assert not any(issue.code.startswith("memory_") for issue in report.issues)

        handle.run_write(
            lambda executor: executor.execute(
                "DELETE FROM memory_search_terms WHERE knowledge_revision_id = ?",
                ("krv_1",),
            )
        )
        broken = OperationalDoctor(store).inspect("ws_1")
        assert broken.health.value == "needs_repair"
        assert any(issue.code == "memory_terms_rebuild" for issue in broken.issues)
    finally:
        handle.close()


def test_doctor_detects_tampered_selection_content_digest(tmp_path):
    handle, journal, _api, _session = _admitted(tmp_path)
    try:
        selection = journal.list_memory_selections("ws_1")[0]
        handle.run_write(
            lambda executor: executor.execute(
                "UPDATE memory_selection_items SET rendered_content_digest = ? "
                "WHERE selection_id = ? AND ordinal = 1",
                ("d" * 64, selection.selection_id),
            )
        )
        report = OperationalDoctor(OperationalStore(tmp_path / "state")).inspect("ws_1")
        assert report.health.value == "needs_repair"
        assert any(issue.code == "memory_selection_content_digest" for issue in report.issues)
        assert any(issue.code == "memory_selection_digest" for issue in report.issues)
    finally:
        handle.close()


def test_backup_verification_checks_memory_selection_and_knowledge_links(tmp_path):
    handle, journal, _api, _session = _admitted(tmp_path)
    try:
        store = OperationalStore(tmp_path / "state")
        backup = OperationalBackupService(store, journal=journal)
        report = backup.create("memory-inspection")
        bundle = store.layout.backups_dir / report.bundle_name
        verified = backup.verify(bundle)
        assert verified.ok
        assert verified.memory_references_ok

        connection = sqlite3.connect(bundle / "database.sqlite")
        connection.execute(
            "UPDATE memory_selections SET selection_digest = ?",
            ("d" * 64,),
        )
        connection.commit()
        connection.close()
        broken = backup.verify(bundle)
        assert not broken.ok
        assert not broken.memory_references_ok
        assert "memory_selection_digest" in broken.issues
    finally:
        handle.close()


def test_backup_memory_verification_decodes_historical_fixed_preference_snapshot(tmp_path):
    handle, journal, _api, _session = _admitted(tmp_path)
    try:
        store = OperationalStore(tmp_path / "state")
        backup = OperationalBackupService(store, journal=journal)
        report = backup.create("memory-legacy-agent-run")
        bundle = store.layout.backups_dir / report.bundle_name
        with sqlite3.connect(bundle / "database.sqlite") as connection:
            row = connection.execute(
                "SELECT agent_run_id, snapshot_json FROM agent_runs LIMIT 1"
            ).fetchone()
            raw = json.loads(str(row[1]))
            raw["preferences"] = {
                "language": "中文",
                "response_detail": None,
                "instructions": ["只解释关键设计。"],
            }
            connection.execute(
                "UPDATE agent_runs SET snapshot_json = ? WHERE agent_run_id = ?",
                (json.dumps(raw, ensure_ascii=False, sort_keys=True), str(row[0])),
            )
            connection.commit()

        verified = backup.verify(bundle)

        assert verified.ok
        assert verified.memory_references_ok
    finally:
        handle.close()


def test_backup_memory_verification_fails_closed_for_missing_v12_tables():
    connection = sqlite3.connect(":memory:")
    try:
        connection.execute("PRAGMA user_version = 12")
        ok, issues = verify_memory_references(connection)
        assert not ok
        assert issues == ("memory_schema_tables_missing",)
    finally:
        connection.close()
