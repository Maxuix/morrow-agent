from __future__ import annotations

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
    V12_NAME,
    V13_NAME,
    V14_NAME,
    V15_NAME,
    V16_NAME,
    V17_NAME,
    MigrationRegistry,
    SchemaMigration,
)
from morrow.adapters.state.operational import OperationalStore
from morrow.core.learning import ProjectKnowledgeCategory
from morrow.core.memory_selection import (
    MemoryQuery,
    MemorySearchTerm,
    MemorySelection,
    MemorySelectionItem,
    MemorySelectionReasonCode,
)
from morrow.core.store import StorageError, StorageErrorCode, StoreOpenMode
from morrow.testing import FixedClock
from test_stage5_learning_store import (
    _candidate,
    _evidence,
    _review,
    _seed_subjects,
    _store,
    _v11_registry,
)

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _knowledge_subject(journal: SqliteOperationalJournal):
    _seed_subjects(journal)
    journal.put_learning_review("ws_1", _review())
    journal.put_learning_evidence("ws_1", _evidence())
    candidate = journal.put_learning_candidate("ws_1", _candidate())
    from morrow.core.learning_memory import (
        LearningCandidateDecision,
        LearningCandidateDecisionKind,
        LearningConflictResolution,
        ProjectKnowledgeHead,
        ProjectKnowledgeRevision,
        ProjectKnowledgeStatus,
    )

    decision = journal.put_learning_candidate_decision(
        "ws_1",
        LearningCandidateDecision(
            decision_id="lcd_1",
            workspace_id="ws_1",
            candidate_id=candidate.candidate_id,
            kind=LearningCandidateDecisionKind.ACCEPT,
            actor="user",
            original_proposal_digest=candidate.fingerprint,
            scope="workspace",
            conflict_resolution=LearningConflictResolution.NONE,
            command_id="cmd_memory_selection",
            created_at=NOW,
        ),
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
            created_at=NOW,
            last_confirmed_at=NOW,
        ),
    )
    journal.save_project_knowledge_head(
        "ws_1",
        head.model_copy(
            update={"current_revision_id": revision.knowledge_revision_id, "row_version": 2}
        ),
        expected_row_version=1,
    )
    journal.ensure_memory_workspace_state("ws_1")
    return head, revision


def _selection() -> MemorySelection:
    item = MemorySelectionItem(
        selection_id="msel_1",
        workspace_id="ws_1",
        ordinal=1,
        record_id="knw_1",
        record_revision_id="krv_1",
        revision=1,
        reason_codes=(MemorySelectionReasonCode.EXPLICIT_KEY,),
        estimated_chars=42,
        rendered_content_digest="c" * 64,
    )
    return MemorySelection(
        selection_id="msel_1",
        workspace_id="ws_1",
        query_digest="a" * 64,
        source_memory_revision=0,
        selected_items=(item,),
        item_count=1,
        omitted_count=0,
        rendered_chars=42,
        selection_digest="b" * 64,
        created_at=NOW,
    )


def test_memory_selection_models_are_bounded_and_deterministic():
    query = MemoryQuery(
        workspace_id="ws_1",
        task_run_id="task_1",
        task_goal="Review persisted operational state",
        requested_categories=(ProjectKnowledgeCategory.ARCHITECTURE,),
        explicit_semantic_keys=("architecture.persistence",),
    )
    assert query.agent_role == "foreground"
    assert query.max_items == 12
    assert query.model_dump(mode="json")["requested_categories"] == ["architecture"]

    selection = _selection()
    assert selection.item_count == len(selection.selected_items) == 1
    assert selection.selected_items[0].ordinal == 1

    with pytest.raises(ValueError):
        MemorySelectionItem(
            selection_id="msel_1",
            workspace_id="ws_1",
            ordinal=1,
            record_id="knw_1",
            record_revision_id="krv_1",
            revision=1,
            reason_codes=(MemorySelectionReasonCode.EXPLICIT_KEY,) * 2,
            estimated_chars=1,
            rendered_content_digest="c" * 64,
        )


def test_v11_store_upgrades_to_v13_without_rewriting_v11(tmp_path):
    legacy = _store(tmp_path, registry=_v11_registry())
    legacy.initialize().close()
    upgraded = _store(tmp_path)

    report = upgraded.migrate()

    assert report.from_version == 11
    assert report.to_version == 22
    assert report.applied == (
        V12_NAME,
        V13_NAME,
        V14_NAME,
        V15_NAME,
        V16_NAME,
        V17_NAME,
        "agent_run_completion_truth",
        "agent_run_request_evidence",
        "agent_run_long_horizon_observability",
        "agent_run_retry_progress",
        "durable_runtime_control_queue",
    )
    with upgraded.open(StoreOpenMode.READ_WRITE) as session:
        assert session.schema_version == 22
        tables = session.run_read(
            lambda executor: executor.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name IN ("
                "'memory_selections', 'memory_selection_items', 'memory_search_terms') "
                "ORDER BY name"
            )
        )
        assert tables == (
            ("memory_search_terms",),
            ("memory_selection_items",),
            ("memory_selections",),
        )


def test_v12_migration_rolls_back_selection_ddl_on_failure(tmp_path):
    legacy = _store(tmp_path, registry=_v11_registry())
    legacy.initialize().close()
    broken = MigrationRegistry(supported_version=12)
    for migration in (V1, V2, V3, V4, V5, V6, V7, V8, V9, V10, V11):
        broken.add(migration)
    broken.add(
        SchemaMigration(
            version=12,
            name="broken_memory_selection",
            statements=(
                "CREATE TABLE v12_rollback_probe (id INTEGER PRIMARY KEY)",
                "THIS IS NOT SQL",
            ),
        )
    )
    failing = _store(tmp_path, registry=broken)

    with pytest.raises(StorageError) as error:
        failing.migrate()
    assert error.value.code is StorageErrorCode.UNAVAILABLE
    assert failing.classify().schema_version == 11
    with failing.open(StoreOpenMode.READ_WRITE) as session:
        names = session.run_read(
            lambda executor: executor.execute(
                "SELECT name FROM sqlite_master WHERE name IN ("
                "'v12_rollback_probe', 'memory_selections', 'memory_search_terms')"
            )
        )
        assert names == ()


def test_memory_selection_and_terms_round_trip_with_workspace_guards(tmp_path):
    store = OperationalStore(tmp_path / "state", clock=FixedClock(NOW), maintenance_timeout=0)
    session = store.initialize()
    journal = SqliteOperationalJournal(session)
    try:
        _knowledge_subject(journal)
        selection = journal.put_memory_selection("ws_1", _selection())
        assert journal.get_memory_selection("ws_1", "msel_1") == selection
        assert journal.list_memory_selections("ws_1") == (selection,)

        terms = journal.replace_memory_search_terms(
            "ws_1",
            "krv_1",
            (
                MemorySearchTerm(
                    workspace_id="ws_1",
                    knowledge_revision_id="krv_1",
                    token_kind="word",
                    token="SQLite",
                    weight_band="high",
                ),
            ),
        )
        assert terms[0].token == "sqlite"
        assert journal.list_memory_search_terms("ws_1", token="SQLITE") == terms

        with pytest.raises(StorageError) as error:
            journal.get_memory_selection("ws_2", "msel_1")
        assert error.value.code is StorageErrorCode.UNAVAILABLE
    finally:
        session.close()


def test_memory_selection_write_rejects_revision_number_mismatch(tmp_path):
    store = OperationalStore(tmp_path / "state", clock=FixedClock(NOW), maintenance_timeout=0)
    session = store.initialize()
    journal = SqliteOperationalJournal(session)
    try:
        _knowledge_subject(journal)
        item = _selection().selected_items[0].model_copy(update={"revision": 2})
        invalid = _selection().model_copy(update={"selected_items": (item,)})
        with pytest.raises(StorageError) as error:
            journal.put_memory_selection("ws_1", invalid)
        assert error.value.code is StorageErrorCode.UNAVAILABLE
    finally:
        session.close()


def test_corrupt_selection_item_is_reported_as_needs_repair(tmp_path):
    store = OperationalStore(tmp_path / "state", clock=FixedClock(NOW), maintenance_timeout=0)
    session = store.initialize()
    journal = SqliteOperationalJournal(session)
    try:
        _knowledge_subject(journal)
        journal.put_memory_selection("ws_1", _selection())
        session.run_write(
            lambda executor: executor.execute(
                "UPDATE memory_selection_items SET reason_json = ?, reason_bytes = ? "
                "WHERE selection_id = ? AND ordinal = ?",
                ("not-json", 8, "msel_1", 1),
            )
        )
        with pytest.raises(StorageError) as error:
            journal.get_memory_selection("ws_1", "msel_1")
        assert error.value.code is StorageErrorCode.NEEDS_REPAIR
    finally:
        session.close()
