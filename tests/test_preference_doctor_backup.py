from __future__ import annotations

import sqlite3

from morrow.adapters.state.journal import SqliteOperationalJournal
from morrow.adapters.state.operational import OperationalStore
from morrow.application.backup import OperationalBackupService
from morrow.application.doctor import OperationalDoctor
from morrow.core.domain import DurableTurn
from morrow.testing import FixedClock
from test_preference_store import NOW, _job_and_evidence
from test_stage5_learning_store import _seed_subjects


def _state(tmp_path):
    store = OperationalStore(tmp_path / "state", clock=FixedClock(NOW), maintenance_timeout=0)
    handle = store.initialize()
    journal = SqliteOperationalJournal(handle)
    _seed_subjects(journal)
    journal.create_turn(
        "ws_1",
        DurableTurn(
            turn_id="turn_one",
            session_id="ses_1",
            task_run_id="task_1",
            client_message_id="client-one",
            created_at=NOW,
        ),
    )
    job, evidence = _job_and_evidence()
    journal.put_preference_job_with_evidence("ws_1", job, evidence)
    return store, handle, journal


def test_doctor_reports_bounded_preference_v13_counts_without_mutation(tmp_path):
    store, handle, _journal = _state(tmp_path)
    try:
        database_mtime = store.layout.database.stat().st_mtime_ns

        report = OperationalDoctor(store).inspect("ws_1")

        assert report.counts["preference_review_jobs"] == 1
        assert report.counts["preference_evidence"] == 1
        assert report.counts["preference_proposals"] == 0
        assert report.counts["preference_write_batches"] == 0
        assert "preference_v13_links_and_lifecycle" in report.checks
        assert store.layout.database.stat().st_mtime_ns == database_mtime
        assert all("以后回答" not in issue.message for issue in report.issues)
    finally:
        handle.close()


def test_backup_verification_detects_tampered_preference_snapshot(tmp_path):
    store, handle, journal = _state(tmp_path)
    try:
        backup = OperationalBackupService(store, journal=journal)
        created = backup.create("preference-v13")
        bundle = store.layout.backups_dir / created.bundle_name
        verified = backup.verify(bundle)
        assert verified.ok
        assert verified.preference_references_ok

        connection = sqlite3.connect(bundle / "database.sqlite")
        connection.execute(
            "UPDATE preference_review_jobs SET active_snapshot_digest = ?",
            ("f" * 64,),
        )
        connection.commit()
        connection.close()

        broken = backup.verify(bundle)
        assert not broken.ok
        assert not broken.preference_references_ok
        assert "preference_job_snapshot" in broken.issues
    finally:
        handle.close()


def test_preference_doctor_integrity_helper_detects_snapshot_tampering(tmp_path):
    _store, handle, _journal = _state(tmp_path)
    try:
        handle.run_write(
            lambda executor: executor.execute(
                "UPDATE preference_review_jobs SET active_snapshot_digest = ? WHERE job_id = ?",
                ("f" * 64, "prjob_one"),
            )
        )

        ok, codes = handle.run_read(OperationalDoctor._preference_v13_checks)

        assert not ok
        assert "preference_job_snapshot" in codes
    finally:
        handle.close()


def test_preference_doctor_integrity_helper_detects_bad_lease(tmp_path):
    _store, handle, _journal = _state(tmp_path)
    try:

        def tamper(executor):
            executor.execute("PRAGMA ignore_check_constraints = ON")
            executor.execute(
                "UPDATE preference_review_jobs SET status = 'running' WHERE job_id = ?",
                ("prjob_one",),
            )

        handle.run_write(tamper)

        ok, codes = handle.run_read(OperationalDoctor._preference_v13_checks)

        assert not ok
        assert "preference_job_lease_state" in codes
    finally:
        handle.close()


def test_doctor_requires_exactly_one_preference_evidence_per_job(tmp_path):
    store, handle, _journal = _state(tmp_path)
    try:
        handle.run_write(
            lambda executor: executor.execute(
                "DELETE FROM preference_evidence WHERE job_id = ?", ("prjob_one",)
            )
        )

        report = OperationalDoctor(store).inspect("ws_1")

        assert report.health.value == "needs_repair"
        assert any(issue.code == "preference_job_evidence_cardinality" for issue in report.issues)
    finally:
        handle.close()
