from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

import pytest

from morrow.adapters.state.journal import SqliteOperationalJournal
from morrow.adapters.state.migrations import (
    V1,
    V2,
    V3,
    V4,
    V5,
    V6,
    V7,
    V8,
    V9,
    V10,
    V11,
    V12,
    MigrationRegistry,
)
from morrow.adapters.state.operational import OperationalStore
from morrow.adapters.state.preference_journal import SqlitePreferenceJournal
from morrow.core.domain import DurableTurn
from morrow.core.preference_models import (
    PreferenceEvidence,
    PreferenceOperation,
    PreferenceProposal,
    PreferenceReviewJob,
    PreferenceReviewSnapshot,
    PreferenceWriteBatch,
    preference_operation_fingerprint,
)
from morrow.core.store import StorageError, StorageErrorCode, StoreOpenMode
from morrow.testing import FixedClock
from test_stage5_learning_store import _seed_subjects

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _store(tmp_path) -> OperationalStore:
    return OperationalStore(tmp_path / "state", clock=FixedClock(NOW), maintenance_timeout=0)


def _job_and_evidence() -> tuple[PreferenceReviewJob, PreferenceEvidence]:
    snapshot = PreferenceReviewSnapshot().serialized_bytes
    job = PreferenceReviewJob(
        job_id="prjob_one",
        workspace_id="ws_1",
        session_id="ses_1",
        turn_id="turn_one",
        active_snapshot_json=snapshot.decode("utf-8"),
        active_snapshot_count=0,
        active_snapshot_bytes=len(snapshot),
        active_snapshot_digest=hashlib.sha256(snapshot).hexdigest(),
        created_at=NOW,
    )
    excerpt = "以后回答代码问题先给出可运行代码。"
    evidence_bytes = len(excerpt.encode("utf-8"))
    evidence = PreferenceEvidence(
        evidence_id="pev_one",
        workspace_id="ws_1",
        job_id=job.job_id,
        turn_id=job.turn_id,
        excerpt_redacted=excerpt,
        excerpt_bytes=evidence_bytes,
        content_digest=hashlib.sha256(excerpt.encode("utf-8")).hexdigest(),
        observed_at=NOW,
        created_at=NOW,
    )
    return job, evidence


def _open_journal(tmp_path):
    store = _store(tmp_path)
    session = store.initialize()
    operational = SqliteOperationalJournal(session)
    _seed_subjects(operational)
    operational.create_turn(
        "ws_1",
        DurableTurn(
            turn_id="turn_one",
            session_id="ses_1",
            task_run_id="task_1",
            client_message_id="client-one",
            created_at=NOW,
        ),
    )
    return store, session, SqlitePreferenceJournal(session)


def test_v13_schema_is_created_with_preference_tables(tmp_path):
    store = _store(tmp_path)
    session = store.initialize()
    names = session.run_read(
        lambda executor: executor.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name LIKE 'preference_%' ORDER BY name"
        )
    )
    assert {row[0] for row in names} == {
        "preference_evidence",
        "preference_proposal_evidence",
        "preference_proposals",
        "preference_review_jobs",
        "preference_write_batch_proposals",
        "preference_write_batches",
    }
    assert session.schema_version == 13
    session.close()


def test_v13_migration_rolls_back_all_preference_ddl_on_failure(tmp_path):
    registry = MigrationRegistry(supported_version=12)
    for migration in (V1, V2, V3, V4, V5, V6, V7, V8, V9, V10, V11, V12):
        registry.add(migration)
    legacy = OperationalStore(tmp_path / "state", registry=registry, clock=FixedClock(NOW))
    legacy.initialize().close()

    def fail(point: str) -> None:
        if point == "before_migration_commit":
            raise RuntimeError("injected migration failure")

    upgraded = OperationalStore(
        tmp_path / "state", failure_injector=fail, clock=FixedClock(NOW), maintenance_timeout=0
    )
    with pytest.raises(RuntimeError):
        upgraded.migrate()
    with upgraded.open(StoreOpenMode.READ_WRITE) as session:
        assert session.schema_version == 12
        tables = session.run_read(
            lambda executor: executor.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name LIKE 'preference_%'"
            )
        )
        assert tables == ()


def test_job_and_exactly_one_current_user_evidence_are_transactional(tmp_path):
    store, session, journal = _open_journal(tmp_path)
    job, evidence = _job_and_evidence()
    stored_job, stored_evidence = journal.put_preference_job_with_evidence("ws_1", job, evidence)
    assert stored_job.job_id == job.job_id
    assert stored_evidence.evidence_id == evidence.evidence_id
    assert journal.count_preference_review_jobs("ws_1") == 1
    assert len(journal.list_preference_evidence("ws_1", job_id=job.job_id)) == 1
    with pytest.raises(StorageError, match="already has Evidence"):
        journal.put_preference_evidence(
            "ws_1", evidence.model_copy(update={"evidence_id": "pev_two"})
        )
    session.close()
    store.layout.database.exists()


def test_proposal_and_write_batch_codecs_are_strict_and_workspace_bound(tmp_path):
    store, session, journal = _open_journal(tmp_path)
    job, evidence = _job_and_evidence()
    journal.put_preference_job_with_evidence("ws_1", job, evidence)
    operation = PreferenceOperation(
        operation="add",
        scope="workspace",
        statement="先给出可运行代码，再解释关键设计。",
        evidence_ids=(evidence.evidence_id,),
    )
    proposal = PreferenceProposal(
        proposal_id="pprop_one",
        workspace_id="ws_1",
        job_id=job.job_id,
        evidence_id=evidence.evidence_id,
        operation=operation,
        fingerprint=preference_operation_fingerprint(operation),
        expected_document_revision=0,
        created_at=NOW,
    )
    stored = journal.put_preference_proposal("ws_1", proposal)
    assert stored.operation.statement == operation.statement
    batch = PreferenceWriteBatch(
        batch_id="pbat_one",
        workspace_id="ws_1",
        scope="workspace",
        command_id="cmd_one",
        operations=(operation,),
        allocated_add_ids=("pref_new",),
        proposal_ids=(proposal.proposal_id,),
        expected_document_revision=0,
        before_document_revision=0,
        before_document_digest="a" * 64,
        after_document_revision=1,
        after_document_digest="b" * 64,
        created_at=NOW,
        prepared_at=NOW,
    )
    assert journal.put_preference_write_batch("ws_1", batch).batch_id == batch.batch_id

    with pytest.raises(StorageError, match="outside the workspace"):
        journal.get_preference_proposal("ws_2", proposal.proposal_id)
    session.run_write(
        lambda executor: executor.execute(
            "UPDATE preference_proposals SET operation_json = ? WHERE proposal_id = ?",
            (json.dumps({"bad": True}), proposal.proposal_id),
        )
    )
    with pytest.raises(StorageError) as error:
        journal.get_preference_proposal("ws_1", proposal.proposal_id)
    assert error.value.code is StorageErrorCode.NEEDS_REPAIR
    session.close()
