from __future__ import annotations

import hashlib
from datetime import UTC, datetime

import pytest

from morrow.adapters.state.journal import SqliteOperationalJournal
from morrow.adapters.state.operational import OperationalStore
from morrow.adapters.state.preference_journal import SqlitePreferenceJournal
from morrow.application.preferences.proposals import (
    PreferenceProposalPipeline,
    PreferenceProposalPipelineError,
)
from morrow.core.domain import DurableTurn, sha256_digest
from morrow.core.preference_documents import FrozenPreferenceSummary, PreferenceReviewSnapshot
from morrow.core.preference_models import PreferenceOperation, PreferenceScope, PreferenceStatus
from morrow.core.preference_persistence_models import PreferenceEvidence, PreferenceReviewJob
from morrow.core.preference_review import PreferenceReviewOutput
from morrow.testing import FixedClock, FixedIdSource
from test_stage5_learning_store import _seed_subjects

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _journal(tmp_path):
    store = OperationalStore(tmp_path / "state", clock=FixedClock(NOW), maintenance_timeout=0)
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


def _source(*, entries=()):
    snapshot = PreferenceReviewSnapshot(
        entries=tuple(
            FrozenPreferenceSummary(
                preference_id=entry[0],
                statement=entry[1],
                status=entry[2],
                scope=entry[3],
                entry_revision=entry[4],
                document_revision=entry[5],
            )
            for entry in entries
        ),
        global_document_revision=0,
        workspace_document_revision=0,
    )
    encoded = snapshot.serialized_bytes
    job = PreferenceReviewJob(
        job_id="prjob_one",
        workspace_id="ws_1",
        session_id="ses_1",
        turn_id="turn_one",
        active_snapshot_json=encoded.decode(),
        active_snapshot_count=len(snapshot.entries),
        active_snapshot_bytes=len(encoded),
        active_snapshot_digest=hashlib.sha256(encoded).hexdigest(),
        created_at=NOW,
    )
    message = "请以后使用这条规则。"
    evidence = PreferenceEvidence(
        evidence_id="pev_one",
        workspace_id="ws_1",
        job_id=job.job_id,
        turn_id=job.turn_id,
        excerpt_redacted=message,
        excerpt_bytes=len(message.encode()),
        content_digest=sha256_digest(message),
        observed_at=NOW,
        created_at=NOW,
    )
    return job, evidence


def _operation(kind: str, *, statement=None, preference_id=None):
    return PreferenceOperation(
        operation=kind,
        scope=PreferenceScope.WORKSPACE,
        statement=statement,
        preference_id=preference_id,
        evidence_ids=("pev_one",),
    )


def test_pipeline_persists_independent_add_replace_remove_proposals(tmp_path):
    store, session, journal = _journal(tmp_path)
    try:
        job, evidence = _source(
            entries=(
                (
                    "pref_replace",
                    "旧规则",
                    PreferenceStatus.ACTIVE,
                    PreferenceScope.WORKSPACE,
                    2,
                    0,
                ),
                ("pref_remove", "待删除", PreferenceStatus.ACTIVE, PreferenceScope.WORKSPACE, 3, 0),
            )
        )
        journal.put_preference_job_with_evidence("ws_1", job, evidence)
        pipeline = PreferenceProposalPipeline(
            journal=journal,
            workspace_id="ws_1",
            id_source=FixedIdSource(),
            clock=FixedClock(NOW),
        )
        result = pipeline.persist(
            job,
            evidence,
            PreferenceReviewOutput(
                operations=(
                    _operation("add", statement="新的自然语言规则"),
                    _operation("replace", preference_id="pref_replace", statement="新规则"),
                    _operation("remove", preference_id="pref_remove"),
                )
            ),
        )
        assert len(result.proposals) == 3
        assert [item.expected_target_revision for item in result.proposals] == [None, 2, 3]
        assert (
            journal.list_preference_proposals("ws_1", job_id=job.job_id, limit=10)
            == result.proposals
        )
    finally:
        session.close()
        assert store.layout.database.exists()


def test_pipeline_rejects_exact_active_disabled_duplicates_but_not_deleted_tombstones(tmp_path):
    store, session, journal = _journal(tmp_path)
    try:
        job, evidence = _source(
            entries=(
                (
                    "pref_active",
                    "完全相同",
                    PreferenceStatus.ACTIVE,
                    PreferenceScope.WORKSPACE,
                    1,
                    0,
                ),
                (
                    "pref_disabled",
                    "已禁用",
                    PreferenceStatus.DISABLED,
                    PreferenceScope.WORKSPACE,
                    1,
                    0,
                ),
                (
                    "pref_deleted",
                    "可重新添加",
                    PreferenceStatus.DELETED,
                    PreferenceScope.WORKSPACE,
                    4,
                    0,
                ),
            )
        )
        journal.put_preference_job_with_evidence("ws_1", job, evidence)
        result = PreferenceProposalPipeline(
            journal=journal,
            workspace_id="ws_1",
            id_source=FixedIdSource(),
            clock=FixedClock(NOW),
        ).persist(
            job,
            evidence,
            (
                _operation("add", statement="完全相同"),
                _operation("add", statement="已禁用"),
                _operation("add", statement="可重新添加"),
            ),
        )
        assert len(result.proposals) == 1
        assert result.proposals[0].operation.statement == "可重新添加"
        assert {item.code for item in result.rejections} == {"duplicate", "disabled_duplicate"}
    finally:
        session.close()
        assert store.layout.database.exists()


def test_pipeline_is_idempotent_for_review_replay_and_rejects_bad_evidence(tmp_path):
    store, session, journal = _journal(tmp_path)
    try:
        job, evidence = _source()
        journal.put_preference_job_with_evidence("ws_1", job, evidence)
        pipeline = PreferenceProposalPipeline(
            journal=journal,
            workspace_id="ws_1",
            id_source=FixedIdSource(),
            clock=FixedClock(NOW),
        )
        operation = _operation("add", statement="一次规则")
        first = pipeline.persist(job, evidence, (operation,))
        replay = pipeline.persist(job, evidence, (operation,))
        assert replay.proposals == ()
        assert replay.duplicate_count == 1
        assert len(first.proposals) == 1
        with pytest.raises(PreferenceProposalPipelineError) as error:
            pipeline.persist(
                job,
                evidence.model_copy(update={"evidence_id": "pev_other"}),
                (operation,),
            )
        assert error.value.code == "missing_reference"
    finally:
        session.close()
        assert store.layout.database.exists()
