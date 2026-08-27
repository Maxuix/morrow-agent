"""S54.5 Stage 5 doctor and isolated SQLite backup acceptance."""

from __future__ import annotations

import json
import sqlite3

import pytest

from morrow.adapters.state.operational import OperationalStore
from morrow.application.backup import OperationalBackupService
from morrow.application.doctor import OperationalDoctor
from morrow.core.learning import (
    LearningCandidateType,
    LearningScope,
    LearningSuppression,
    LearningSuppressionStatus,
)
from morrow.testing import FixedClock
from test_stage5_project_knowledge import _accept_command, _project_candidate


@pytest.mark.asyncio
async def test_doctor_reports_stage5_counts_without_writing_the_database(tmp_path):
    session, journal, _api, candidate, _reviewer = await _project_candidate(tmp_path)
    try:
        database = OperationalStore(tmp_path / "state").layout.database
        before = database.stat().st_mtime_ns
        report = OperationalDoctor(OperationalStore(tmp_path / "state")).inspect("ws_1")
        after = database.stat().st_mtime_ns

        assert report.health.value in {"ok", "needs_recovery"}
        assert before == after
        assert report.counts["learning_reviews"] == 1
        assert report.counts["learning_evidence"] >= 1
        assert report.counts["learning_candidates"] == 1
        assert report.counts["learning_knowledge_heads"] == 0
        assert all(issue.code != "learning_candidate" for issue in report.issues)
    finally:
        session.close()


@pytest.mark.asyncio
async def test_doctor_detects_candidate_evidence_and_suppression_target_drift(tmp_path):
    session, journal, api, candidate, _reviewer = await _project_candidate(tmp_path)
    try:
        journal.put_learning_suppression(
            "ws_1",
            LearningSuppression(
                suppression_id="lsp_doctor",
                workspace_id="ws_1",
                candidate_type=LearningCandidateType.PROJECT_KNOWLEDGE,
                scope=LearningScope.WORKSPACE,
                semantic_key="different.target",
                source_candidate_id=candidate.candidate_id,
                reason="synthetic doctor drift",
                status=LearningSuppressionStatus.ACTIVE,
            ),
        )
        session.run_write(
            lambda executor: executor.execute(
                "DELETE FROM learning_candidate_evidence WHERE candidate_id = ?",
                (candidate.candidate_id,),
            )
        )

        report = OperationalDoctor(OperationalStore(tmp_path / "state")).inspect("ws_1")

        codes = {issue.code for issue in report.issues}
        assert report.health.value == "needs_repair"
        assert "learning_candidate_evidence" in codes
        assert "learning_suppression_target" in codes
        assert any(candidate.candidate_id in issue.summary for issue in report.issues)
        assert any("lsp_doctor" in issue.summary for issue in report.issues)
    finally:
        session.close()


@pytest.mark.asyncio
async def test_stage5_sqlite_backup_preserves_learning_state_in_isolation(tmp_path):
    session, journal, api, candidate, _reviewer = await _project_candidate(tmp_path)
    try:
        api.accept_learning_candidate(_accept_command(candidate, "cmd_backup_accept"))
        store = OperationalStore(tmp_path / "state", clock=FixedClock())
        backup = OperationalBackupService(store, journal=journal)
        report = backup.create("stage5-acceptance")
        bundle = store.layout.backups_dir / report.bundle_name
        verified = backup.verify(bundle)

        assert report.integrity_ok
        assert verified.ok
        assert verified.learning_references_ok
        manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
        assert candidate.candidate_id
        assert manifest["schema_version"] == 19
        assert not any(
            path.name in {"config.yaml", "workspace-index.yaml", "credentials", "keyring"}
            for path in bundle.rglob("*")
        )

        tables = (
            "learning_reviews",
            "learning_evidence",
            "learning_candidates",
            "learning_candidate_evidence",
            "project_knowledge_heads",
            "memory_workspace_state",
        )
        with sqlite3.connect(bundle / "database.sqlite") as connection:
            counts = {
                table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
                for table in tables
            }
        assert counts["learning_reviews"] == 1
        assert counts["learning_evidence"] >= 1
        assert counts["learning_candidates"] == 1
        assert counts["learning_candidate_evidence"] == 1
        assert counts["project_knowledge_heads"] == 1
        assert counts["memory_workspace_state"] == 1
    finally:
        session.close()


@pytest.mark.asyncio
async def test_stage5_backup_rejects_tampered_decision_reference(tmp_path):
    session, journal, api, candidate, _reviewer = await _project_candidate(tmp_path)
    try:
        api.accept_learning_candidate(_accept_command(candidate, "cmd_tamper_accept"))
        store = OperationalStore(tmp_path / "state", clock=FixedClock())
        backup = OperationalBackupService(store, journal=journal)
        report = backup.create("stage5-tamper")
        bundle = store.layout.backups_dir / report.bundle_name
        with sqlite3.connect(bundle / "database.sqlite") as connection:
            connection.execute(
                "UPDATE learning_candidate_decisions SET original_proposal_digest = ?",
                ("d" * 64,),
            )
            connection.commit()

        verified = backup.verify(bundle)

        assert not verified.ok
        assert not verified.learning_references_ok
        assert "learning_decision_candidate" in verified.issues
    finally:
        session.close()
