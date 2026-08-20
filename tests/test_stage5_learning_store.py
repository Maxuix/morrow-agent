"""v10 Learning persistence and transaction-boundary regressions."""

from datetime import UTC, datetime, timedelta

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
    V10_NAME,
    V11_NAME,
    MigrationRegistry,
    SchemaMigration,
)
from morrow.adapters.state.operational import OperationalStore
from morrow.core.domain import (
    DurableSession,
    DurableTaskOutcome,
    DurableTaskRun,
    DurableTaskRunTransition,
    TaskOutcomeTrigger,
    TaskRunStatus,
    canonical_json_bytes,
    sha256_digest,
)
from morrow.core.learning import (
    CandidateDraft,
    LearningCandidate,
    LearningCandidateOperation,
    LearningCandidateType,
    LearningConfidenceBand,
    LearningEvidence,
    LearningEvidenceActor,
    LearningEvidenceAuthority,
    LearningEvidenceExplicitness,
    LearningEvidencePolarity,
    LearningEvidenceSourceKind,
    LearningMode,
    LearningPolicy,
    LearningReview,
    LearningReviewStatus,
    LearningReviewTrigger,
    LearningScope,
    LearningSensitivity,
    LearningSuppression,
    LearningSuppressionStatus,
    PreferenceCandidatePayload,
)
from morrow.core.learning_memory import (
    LearningCandidateDecision,
    LearningCandidateDecisionKind,
    LearningConflictResolution,
    ProjectKnowledgeCategory,
    ProjectKnowledgeEvidenceLink,
    ProjectKnowledgeHead,
    ProjectKnowledgeRevision,
    ProjectKnowledgeStatus,
)
from morrow.core.store import StorageError, StorageErrorCode, StoreOpenMode
from morrow.testing import FixedClock

NOW = datetime(2026, 1, 1, tzinfo=UTC)
DIGEST = "a" * 64


def _v9_registry() -> MigrationRegistry:
    registry = MigrationRegistry(supported_version=9)
    for migration in (V1, V2, V3, V4, V5, V6, V7, V8, V9):
        registry.add(migration)
    return registry


def _v10_registry() -> MigrationRegistry:
    registry = MigrationRegistry(supported_version=10)
    for migration in (V1, V2, V3, V4, V5, V6, V7, V8, V9, V10):
        registry.add(migration)
    return registry


def _store(tmp_path, *, registry=None):
    return OperationalStore(
        tmp_path / "state",
        registry=registry,
        clock=FixedClock(NOW),
        maintenance_timeout=0,
    )


def _seed_subjects(journal: SqliteOperationalJournal) -> None:
    journal.create_session(
        DurableSession(session_id="ses_1", workspace_id="ws_1"),
        task=DurableTaskRun(task_run_id="task_1", session_id="ses_1", workspace_id="ws_1"),
    )
    ready = journal.transition_task_run(
        "ws_1",
        "task_1",
        target=TaskRunStatus.READY_FOR_ACCEPTANCE,
        transition=DurableTaskRunTransition(
            transition_id="ttr_1",
            workspace_id="ws_1",
            session_id="ses_1",
            task_run_id="task_1",
            from_status=TaskRunStatus.OPEN,
            to_status=TaskRunStatus.READY_FOR_ACCEPTANCE,
            reason="answer ready",
            created_at=NOW,
        ),
        expected_row_version=1,
    )
    assert ready.row_version == 2
    journal.transition_task_run(
        "ws_1",
        "task_1",
        target=TaskRunStatus.ACCEPTED,
        transition=DurableTaskRunTransition(
            transition_id="ttr_2",
            workspace_id="ws_1",
            session_id="ses_1",
            task_run_id="task_1",
            from_status=TaskRunStatus.READY_FOR_ACCEPTANCE,
            to_status=TaskRunStatus.ACCEPTED,
            reason="user accepted",
            created_at=NOW,
        ),
        expected_row_version=2,
    )
    journal.put_task_outcome(
        "ws_1",
        DurableTaskOutcome(
            outcome_id="out_1",
            workspace_id="ws_1",
            session_id="ses_1",
            task_run_id="task_1",
            version=1,
            trigger=TaskOutcomeTrigger.ACCEPTANCE,
            task_status=TaskRunStatus.ACCEPTED,
            summary="任务已接受。",
            created_at=NOW,
        ),
    )


def _review() -> LearningReview:
    snapshot = canonical_json_bytes({"mode": "review_only", "candidate_ttl_days": 30}).decode(
        "utf-8"
    )
    return LearningReview(
        review_id="lrv_1",
        workspace_id="ws_1",
        task_run_id="task_1",
        task_outcome_id="out_1",
        trigger=LearningReviewTrigger.TASK_ACCEPTED,
        policy_snapshot_json=snapshot,
        policy_digest=sha256_digest(snapshot),
        created_at=NOW,
    )


def _evidence(**overrides) -> LearningEvidence:
    values = {
        "evidence_id": "lev_1",
        "workspace_id": "ws_1",
        "origin_review_id": "lrv_1",
        "task_run_id": "task_1",
        "source_kind": LearningEvidenceSourceKind.USER_TURN,
        "source_id": "turn_1",
        "source_pointer": "user.content",
        "actor": LearningEvidenceActor.USER,
        "authority": LearningEvidenceAuthority.USER_EXPLICIT_PERSISTENT,
        "explicitness": LearningEvidenceExplicitness.EXPLICIT,
        "polarity": LearningEvidencePolarity.POSITIVE,
        "scope_hint": LearningScope.WORKSPACE,
        "excerpt_redacted": "以后默认使用中文。",
        "content_digest": DIGEST,
        "observed_at": NOW,
        "created_at": NOW,
    }
    values.update(overrides)
    return LearningEvidence(**values)


def _candidate() -> LearningCandidate:
    draft = CandidateDraft(
        candidate_type=LearningCandidateType.PREFERENCE,
        operation=LearningCandidateOperation.SET,
        semantic_key="communication.language",
        proposed_scope=LearningScope.WORKSPACE,
        proposed_payload=PreferenceCandidatePayload(path="language", value="中文"),
        evidence_ids=("lev_1",),
        temporary_or_durable="durable",
    )
    return LearningCandidate.from_draft(
        candidate_id="lcn_1",
        workspace_id="ws_1",
        origin_review_id="lrv_1",
        draft=draft,
        confidence_band=LearningConfidenceBand.HIGH,
        confidence_basis=("explicit_user_intent",),
        sensitivity=LearningSensitivity.NORMAL,
        expires_at=NOW + timedelta(days=30),
        now=NOW,
    )


def test_v9_store_upgrades_to_v11_without_rewriting_old_migrations(tmp_path):
    legacy = _store(tmp_path, registry=_v9_registry())
    legacy.initialize().close()
    upgraded = _store(tmp_path)
    report = upgraded.migrate()

    assert report.from_version == 9
    assert report.to_version == 11
    assert report.applied == (V10_NAME, V11_NAME)
    with upgraded.open(StoreOpenMode.READ_WRITE) as session:
        assert session.schema_version == 11
        rows = session.run_read(
            lambda executor: executor.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND ("
                "name LIKE 'learning_%' OR name LIKE 'project_%' OR name = 'memory_workspace_state')"
            )
        )
        assert {str(row[0]) for row in rows} == {
            "learning_policies",
            "learning_reviews",
            "learning_evidence",
            "learning_review_evidence",
            "learning_candidates",
            "learning_candidate_evidence",
            "learning_suppressions",
            "learning_candidate_decisions",
            "project_knowledge_heads",
            "project_knowledge_revisions",
            "project_knowledge_evidence",
            "memory_workspace_state",
        }


def test_v10_store_upgrades_to_v11_without_rewriting_v10(tmp_path):
    legacy = _store(tmp_path, registry=_v10_registry())
    legacy.initialize().close()
    upgraded = _store(tmp_path)

    report = upgraded.migrate()

    assert report.from_version == 10
    assert report.to_version == 11
    assert report.applied == (V11_NAME,)
    with upgraded.open(StoreOpenMode.READ_WRITE) as session:
        rows = session.run_read(
            lambda executor: executor.execute(
                "SELECT version, checksum FROM schema_migrations WHERE version IN (9, 10, 11) "
                "ORDER BY version"
            )
        )
        assert tuple(int(row[0]) for row in rows) == (9, 10, 11)
        assert str(rows[1][1]) == V10.checksum


def test_v11_migration_rolls_back_all_inbox_and_memory_ddl_on_failure(tmp_path):
    legacy = _store(tmp_path, registry=_v10_registry())
    legacy.initialize().close()
    broken = MigrationRegistry(supported_version=11)
    for migration in (V1, V2, V3, V4, V5, V6, V7, V8, V9, V10):
        broken.add(migration)
    broken.add(
        SchemaMigration(
            version=11,
            name="broken_inbox_memory",
            statements=(
                "CREATE TABLE v11_rollback_probe (id INTEGER PRIMARY KEY)",
                "THIS IS NOT SQL",
            ),
        )
    )
    failing = _store(tmp_path, registry=broken)

    with pytest.raises(StorageError) as error:
        failing.migrate()
    assert error.value.code is StorageErrorCode.UNAVAILABLE
    assert failing.classify().schema_version == 10
    with failing.open(StoreOpenMode.READ_WRITE) as session:
        names = session.run_read(
            lambda executor: executor.execute(
                "SELECT name FROM sqlite_master WHERE name IN ("
                "'v11_rollback_probe', 'learning_candidate_decisions', "
                "'project_knowledge_heads', 'promotion_operations')"
            )
        )
        assert names == ()


def test_v10_migration_rolls_back_all_learning_ddl_on_failure(tmp_path):
    legacy = _store(tmp_path, registry=_v9_registry())
    legacy.initialize().close()
    broken = MigrationRegistry(supported_version=10)
    for migration in (V1, V2, V3, V4, V5, V6, V7, V8, V9):
        broken.add(migration)
    broken.add(
        SchemaMigration(
            version=10,
            name="broken_learning_foundation",
            statements=(
                "CREATE TABLE learning_rollback_probe (id INTEGER PRIMARY KEY)",
                "THIS IS NOT SQL",
            ),
        )
    )
    failing = _store(tmp_path, registry=broken)

    with pytest.raises(StorageError) as error:
        failing.migrate()
    assert error.value.code is StorageErrorCode.UNAVAILABLE
    assert failing.classify().schema_version == 9
    with failing.open(StoreOpenMode.READ_WRITE) as session:
        names = session.run_read(
            lambda executor: executor.execute(
                "SELECT name FROM sqlite_master WHERE name = 'learning_rollback_probe'"
            )
        )
        assert names == ()


def test_learning_records_round_trip_and_default_policy_is_read_only(tmp_path):
    store = _store(tmp_path)
    session = store.initialize()
    journal = SqliteOperationalJournal(session)
    try:
        _seed_subjects(journal)
        assert journal.get_learning_policy("ws_1") is None
        assert journal.get_effective_learning_policy("ws_1").mode.value == "review_only"

        policy = LearningPolicy.default_for("ws_1", now=NOW)
        assert journal.save_learning_policy("ws_1", policy, expected_row_version=None) == policy
        review = journal.put_learning_review("ws_1", _review())
        evidence = journal.put_learning_evidence("ws_1", _evidence())
        journal.link_learning_review_evidence("ws_1", review.review_id, evidence.evidence_id)
        candidate = journal.put_learning_candidate("ws_1", _candidate())
        suppression = journal.put_learning_suppression(
            "ws_1",
            LearningSuppression(
                suppression_id="lsp_1",
                workspace_id="ws_1",
                candidate_type=LearningCandidateType.PREFERENCE,
                scope=LearningScope.WORKSPACE,
                semantic_key="communication.language",
                reason="用户拒绝该建议。",
                status=LearningSuppressionStatus.ACTIVE,
                created_at=NOW,
                updated_at=NOW,
            ),
        )

        assert journal.get_learning_review("ws_1", review.review_id) == review
        assert journal.get_learning_evidence("ws_1", evidence.evidence_id) == evidence
        assert journal.list_learning_review_evidence("ws_1", review.review_id) == (evidence,)
        assert journal.get_learning_candidate("ws_1", candidate.candidate_id) == candidate
        assert journal.list_learning_candidate_evidence("ws_1", candidate.candidate_id) == (
            evidence,
        )
        assert journal.get_learning_suppression("ws_1", suppression.suppression_id) == suppression
    finally:
        session.close()


def test_learning_evidence_allows_the_full_multibyte_character_budget(tmp_path):
    store = _store(tmp_path)
    session = store.initialize()
    journal = SqliteOperationalJournal(session)
    try:
        _seed_subjects(journal)
        journal.put_learning_review("ws_1", _review())
        evidence = journal.put_learning_evidence(
            "ws_1",
            _evidence(
                evidence_id="lev_cjk",
                source_id="turn_cjk",
                excerpt_redacted="中" * 512,
            ),
        )
        assert evidence.excerpt_redacted == "中" * 512
        assert journal.get_learning_evidence("ws_1", "lev_cjk") == evidence
    finally:
        session.close()


def test_learning_row_versions_leases_duplicates_and_workspace_isolation(tmp_path):
    store = _store(tmp_path)
    session = store.initialize()
    journal = SqliteOperationalJournal(session)
    try:
        _seed_subjects(journal)
        journal.put_learning_review("ws_1", _review())
        journal.put_learning_evidence("ws_1", _evidence())
        candidate = journal.put_learning_candidate("ws_1", _candidate())
        journal.put_learning_evidence("ws_1", _evidence(evidence_id="lev_2", source_id="turn_2"))
        duplicate = _candidate().model_copy(update={"candidate_id": "lcn_2"})
        with pytest.raises(StorageError):
            journal.put_learning_candidate("ws_1", duplicate)

        with pytest.raises(StorageError) as error:
            journal.get_learning_candidate("ws_2", candidate.candidate_id)
        assert error.value.code is StorageErrorCode.UNAVAILABLE

        policy = LearningPolicy.default_for("ws_1", now=NOW)
        journal.save_learning_policy("ws_1", policy, expected_row_version=None)
        next_policy = policy.model_copy(
            update={"mode": LearningMode.OFF, "row_version": 2, "updated_at": NOW}
        )
        journal.save_learning_policy("ws_1", next_policy, expected_row_version=1)
        with pytest.raises(StorageError):
            journal.save_learning_policy("ws_1", next_policy, expected_row_version=1)

        claimed = journal.claim_learning_review(
            "ws_1",
            "lrv_1",
            expected_row_version=1,
            lease_id="lease_1",
            lease_expires_at=NOW + timedelta(minutes=5),
            started_at=NOW,
        )
        assert claimed.status is LearningReviewStatus.RUNNING
        with pytest.raises(StorageError) as error:
            journal.claim_learning_review(
                "ws_1",
                "lrv_1",
                expected_row_version=claimed.row_version,
                lease_id="lease_2",
                lease_expires_at=NOW + timedelta(minutes=5),
                started_at=NOW,
            )
        assert error.value.code is StorageErrorCode.BUSY

        linked = journal.link_learning_candidate_evidence(
            "ws_1", candidate.candidate_id, "lev_2", expected_row_version=candidate.row_version
        )
        assert linked.row_version == candidate.row_version + 1
        with pytest.raises(StorageError) as error:
            journal.link_learning_candidate_evidence(
                "ws_1", candidate.candidate_id, "lev_2", expected_row_version=1
            )
        assert error.value.code is StorageErrorCode.UNAVAILABLE

        resolved = LearningCandidate.model_validate(
            {
                **linked.model_dump(),
                "status": "accepted",
                "row_version": linked.row_version + 1,
                "resolved_at": NOW,
                "resolved_by": "user",
            }
        )
        journal.save_learning_candidate("ws_1", resolved, expected_row_version=linked.row_version)
        with pytest.raises(StorageError):
            journal.link_learning_candidate_evidence(
                "ws_1", candidate.candidate_id, "lev_1", expected_row_version=resolved.row_version
            )

    finally:
        session.close()


def test_learning_corruption_is_classified_as_needs_repair_and_outer_writes_rollback(tmp_path):
    store = _store(tmp_path)
    session = store.initialize()
    journal = SqliteOperationalJournal(session)
    try:
        _seed_subjects(journal)
        journal.put_learning_review("ws_1", _review())
        journal.put_learning_evidence("ws_1", _evidence())
        journal.put_learning_candidate("ws_1", _candidate())
        session.run_write(
            lambda executor: executor.execute(
                "UPDATE learning_candidates SET proposed_payload_json = '{bad}' WHERE candidate_id = 'lcn_1'"
            )
        )
        with pytest.raises(StorageError) as error:
            journal.get_learning_candidate("ws_1", "lcn_1")
        assert error.value.code is StorageErrorCode.NEEDS_REPAIR

        session.run_write(
            lambda executor: executor.execute(
                "UPDATE learning_candidates SET proposed_payload_json = ? WHERE candidate_id = 'lcn_1'",
                (
                    canonical_json_bytes(
                        _candidate().proposed_payload.model_dump(mode="json")
                    ).decode(),
                ),
            )
        )
        with pytest.raises(RuntimeError):
            journal.transact(
                lambda current: (
                    current.save_learning_policy(
                        "ws_1",
                        LearningPolicy.default_for("ws_1", now=NOW),
                        expected_row_version=None,
                    ),
                    (_ for _ in ()).throw(RuntimeError("rollback")),
                )[-1]
            )
        assert journal.get_learning_policy("ws_1") is None
    finally:
        session.close()


def test_v11_decisions_knowledge_and_memory_state_round_trip(tmp_path):
    store = _store(tmp_path)
    session = store.initialize()
    journal = SqliteOperationalJournal(session)
    try:
        _seed_subjects(journal)
        journal.put_learning_review("ws_1", _review())
        journal.put_learning_evidence("ws_1", _evidence())
        candidate = journal.put_learning_candidate("ws_1", _candidate())

        decision = journal.put_learning_candidate_decision(
            "ws_1",
            LearningCandidateDecision(
                decision_id="lcd_1",
                workspace_id="ws_1",
                candidate_id=candidate.candidate_id,
                kind=LearningCandidateDecisionKind.ACCEPT,
                actor="user",
                original_proposal_digest=candidate.fingerprint,
                scope=LearningScope.WORKSPACE,
                conflict_resolution=LearningConflictResolution.NONE,
                command_id="cmd_learning_1",
                created_at=NOW,
            ),
        )
        assert decision.original_proposal_digest == candidate.fingerprint
        assert journal.list_learning_candidate_decisions("ws_1", candidate_id="lcn_1") == (
            decision,
        )

        head = journal.put_project_knowledge_head(
            "ws_1",
            ProjectKnowledgeHead(
                knowledge_id="knw_1",
                workspace_id="ws_1",
                semantic_key="architecture.persistence",
                category=ProjectKnowledgeCategory.ARCHITECTURE,
                status=ProjectKnowledgeStatus.ACTIVE,
                created_at=NOW,
                updated_at=NOW,
            ),
        )
        revision = journal.put_project_knowledge_revision(
            "ws_1",
            ProjectKnowledgeRevision(
                knowledge_revision_id="krv_1",
                knowledge_id=head.knowledge_id,
                workspace_id="ws_1",
                revision=1,
                statement="Operational state is persisted in SQLite.",
                statement_digest=ProjectKnowledgeRevision.digest_for(
                    "Operational state is persisted in SQLite."
                ),
                source_candidate_id=candidate.candidate_id,
                source_decision_id=decision.decision_id,
                sensitivity=LearningSensitivity.NORMAL,
                created_at=NOW,
                last_confirmed_at=NOW,
            ),
        )
        head = journal.save_project_knowledge_head(
            "ws_1",
            head.model_copy(
                update={"current_revision_id": revision.knowledge_revision_id, "row_version": 2}
            ),
            expected_row_version=1,
        )
        link = journal.put_project_knowledge_evidence(
            "ws_1",
            ProjectKnowledgeEvidenceLink(
                workspace_id="ws_1",
                knowledge_revision_id=revision.knowledge_revision_id,
                evidence_id="lev_1",
            ),
        )
        state = journal.ensure_memory_workspace_state("ws_1")
        assert state.memory_revision == 0
        state = journal.save_memory_workspace_state(
            "ws_1",
            state.model_copy(update={"memory_revision": 1, "row_version": 2}),
            expected_row_version=1,
        )

        assert journal.get_project_knowledge_head_by_key("ws_1", head.semantic_key) == head
        assert (
            journal.get_project_knowledge_revision("ws_1", revision.knowledge_revision_id)
            == revision
        )
        assert journal.list_project_knowledge_evidence("ws_1", revision.knowledge_revision_id) == (
            link,
        )
        assert journal.get_memory_workspace_state("ws_1") == state
    finally:
        session.close()


def test_v11_workspace_guards_immutable_revision_and_reserved_saga_constraints(tmp_path):
    store = _store(tmp_path)
    session = store.initialize()
    journal = SqliteOperationalJournal(session)
    try:
        _seed_subjects(journal)
        journal.put_learning_review("ws_1", _review())
        journal.put_learning_evidence("ws_1", _evidence())
        candidate = journal.put_learning_candidate("ws_1", _candidate())
        decision = journal.put_learning_candidate_decision(
            "ws_1",
            LearningCandidateDecision(
                decision_id="lcd_guard",
                workspace_id="ws_1",
                candidate_id=candidate.candidate_id,
                kind=LearningCandidateDecisionKind.ACCEPT,
                actor="user",
                original_proposal_digest=candidate.fingerprint,
                scope=LearningScope.WORKSPACE,
                command_id="cmd_guard",
                created_at=NOW,
            ),
        )
        head = journal.put_project_knowledge_head(
            "ws_1",
            ProjectKnowledgeHead(
                knowledge_id="knw_guard",
                workspace_id="ws_1",
                semantic_key="architecture.guard",
                category=ProjectKnowledgeCategory.ARCHITECTURE,
                created_at=NOW,
                updated_at=NOW,
            ),
        )
        revision = journal.put_project_knowledge_revision(
            "ws_1",
            ProjectKnowledgeRevision(
                knowledge_revision_id="krv_guard",
                knowledge_id=head.knowledge_id,
                workspace_id="ws_1",
                revision=1,
                statement="Guarded statement.",
                statement_digest=ProjectKnowledgeRevision.digest_for("Guarded statement."),
                source_candidate_id=candidate.candidate_id,
                source_decision_id=decision.decision_id,
                created_at=NOW,
                last_confirmed_at=NOW,
            ),
        )
        with pytest.raises(StorageError) as error:
            journal.get_project_knowledge_revision("ws_2", revision.knowledge_revision_id)
        assert error.value.code is StorageErrorCode.UNAVAILABLE

        with pytest.raises(StorageError):
            session.run_write(
                lambda executor: executor.execute(
                    "UPDATE project_knowledge_revisions SET statement = 'mutated' "
                    "WHERE knowledge_revision_id = 'krv_guard'"
                )
            )
        with pytest.raises(StorageError):
            session.run_write(
                lambda executor: executor.execute(
                    "INSERT INTO memory_workspace_state(workspace_id, memory_revision, row_version, updated_at_unix) "
                    "VALUES ('ws_1', -1, 1, ?)",
                    (int(NOW.timestamp()),),
                )
            )
        with pytest.raises(StorageError):
            session.run_write(
                lambda executor: executor.execute(
                    "INSERT INTO promotion_operations("
                    "operation_id, command_id, request_digest, workspace_id, candidate_id, "
                    "candidate_row_version, target, scope, path, prepared_change_json, "
                    "prepared_change_digest, state, row_version, created_at_unix, updated_at_unix) "
                    "VALUES ('pop_1', 'cmd_pop', ?, 'ws_1', 'lcn_1', 1, 'profile', 'workspace', "
                    "'summary', '{}', ?, 'invalid', 1, ?, ?)",
                    (DIGEST, DIGEST, int(NOW.timestamp()), int(NOW.timestamp())),
                )
            )
    finally:
        session.close()
