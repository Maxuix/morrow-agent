from __future__ import annotations

from datetime import UTC, datetime

from morrow.adapters.state.journal import SqliteOperationalJournal
from morrow.adapters.state.operational import OperationalStore
from morrow.application.learning.memory_selector import MemorySelector
from morrow.application.learning.memory_terms import refresh_project_knowledge_terms
from morrow.core.learning import LearningSensitivity
from morrow.core.learning_memory import (
    ProjectKnowledgeCategory,
    ProjectKnowledgeHead,
    ProjectKnowledgeRevision,
    ProjectKnowledgeStatus,
)
from morrow.core.memory_selection import (
    MemoryQuery,
    MemorySelectionReasonCode,
)
from morrow.testing import FixedClock, FixedIdSource
from test_stage5_memory_selection import _knowledge_subject

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _add_knowledge(
    journal: SqliteOperationalJournal,
    *,
    knowledge_id: str,
    revision_id: str,
    semantic_key: str,
    category: ProjectKnowledgeCategory,
    statement: str,
    sensitivity: LearningSensitivity = LearningSensitivity.NORMAL,
    valid_until: datetime | None = None,
) -> ProjectKnowledgeHead:
    head = ProjectKnowledgeHead(
        knowledge_id=knowledge_id,
        workspace_id="ws_1",
        semantic_key=semantic_key,
        category=category,
        status=ProjectKnowledgeStatus.ACTIVE,
        created_at=NOW,
        updated_at=NOW,
    )
    journal.put_project_knowledge_head("ws_1", head)
    revision = ProjectKnowledgeRevision(
        knowledge_revision_id=revision_id,
        knowledge_id=knowledge_id,
        workspace_id="ws_1",
        revision=1,
        statement=statement,
        statement_digest=ProjectKnowledgeRevision.digest_for(statement),
        source_candidate_id="lcn_1",
        source_decision_id="lcd_1",
        sensitivity=sensitivity,
        valid_until=valid_until,
        created_at=NOW,
        last_confirmed_at=NOW,
    )
    journal.put_project_knowledge_revision("ws_1", revision)
    saved = journal.save_project_knowledge_head(
        "ws_1",
        head.model_copy(update={"current_revision_id": revision_id, "row_version": 2}),
        expected_row_version=1,
    )
    return saved


def _seed_selector_subject(journal: SqliteOperationalJournal):
    base_head, _base_revision = _knowledge_subject(journal)
    current_base = journal.get_project_knowledge_head("ws_1", base_head.knowledge_id)
    assert current_base is not None
    convention = _add_knowledge(
        journal,
        knowledge_id="knw_2",
        revision_id="krv_2",
        semantic_key="convention.testing",
        category=ProjectKnowledgeCategory.CONVENTION,
        statement="Pytest covers the command path.",
    )
    domain = _add_knowledge(
        journal,
        knowledge_id="knw_3",
        revision_id="krv_3",
        semantic_key="domain.storage",
        category=ProjectKnowledgeCategory.DOMAIN,
        statement="SQLite database stores durable configuration.",
    )
    expired = _add_knowledge(
        journal,
        knowledge_id="knw_4",
        revision_id="krv_4",
        semantic_key="architecture.expired",
        category=ProjectKnowledgeCategory.ARCHITECTURE,
        statement="Expired memory should not be selected.",
        valid_until=NOW,
    )
    prohibited = _add_knowledge(
        journal,
        knowledge_id="knw_5",
        revision_id="krv_5",
        semantic_key="architecture.prohibited",
        category=ProjectKnowledgeCategory.ARCHITECTURE,
        statement="Prohibited memory should not be selected.",
        sensitivity=LearningSensitivity.PROHIBITED,
    )

    def rebuild(txn):
        for head in (current_base, convention, domain, expired, prohibited):
            refresh_project_knowledge_terms(txn, "ws_1", head)

    journal.transact(rebuild)
    return current_base, convention, domain, expired, prohibited


def _select(journal, query: MemoryQuery):
    selector = MemorySelector(id_source=FixedIdSource(), clock=FixedClock(NOW).now)
    return journal.transact(lambda txn: selector.select(txn, query))


def test_selector_is_deterministic_explainable_and_diverse(tmp_path):
    store = OperationalStore(tmp_path / "state", clock=FixedClock(NOW), maintenance_timeout=0)
    session = store.initialize()
    journal = SqliteOperationalJournal(session)
    try:
        base, convention, domain, _expired, _prohibited = _seed_selector_subject(journal)
        query = MemoryQuery(
            workspace_id="ws_1",
            task_goal="SQLite pytest command",
            requested_categories=(ProjectKnowledgeCategory.CONVENTION,),
            explicit_semantic_keys=("architecture.persistence",),
            max_items=2,
        )
        first = _select(journal, query)
        second = _select(journal, query)
        assert first == second
        assert [item.record_id for item in first.selected_items] == [
            base.knowledge_id,
            convention.knowledge_id,
        ]
        assert first.source_memory_revision == 0
        assert first.selected_items[0].reason_codes[0] is MemorySelectionReasonCode.EXPLICIT_KEY
        assert MemorySelectionReasonCode.CATEGORY in first.selected_items[1].reason_codes
        assert MemorySelectionReasonCode.CATEGORY_DIVERSITY in first.selected_items[1].reason_codes
        assert first.selected_items[0].estimated_chars > 0
        assert first.rendered_chars == sum(item.estimated_chars for item in first.selected_items)
        assert domain.knowledge_id not in {item.record_id for item in first.selected_items}
    finally:
        session.close()


def test_selector_applies_item_and_character_budgets_and_zero_item_selection(tmp_path):
    store = OperationalStore(tmp_path / "state", clock=FixedClock(NOW), maintenance_timeout=0)
    session = store.initialize()
    journal = SqliteOperationalJournal(session)
    try:
        base, _convention, _domain, _expired, _prohibited = _seed_selector_subject(journal)
        broad = _select(
            journal,
            MemoryQuery(
                workspace_id="ws_1",
                task_goal="SQLite pytest",
                explicit_semantic_keys=("architecture.persistence",),
                max_items=12,
            ),
        )
        exact = _select(
            journal,
            MemoryQuery(
                workspace_id="ws_1",
                task_goal="SQLite pytest",
                explicit_semantic_keys=("architecture.persistence",),
                max_items=1,
                max_rendered_chars=broad.selected_items[0].estimated_chars,
            ),
        )
        assert exact.item_count == 1
        assert exact.selected_items[0].record_id == base.knowledge_id
        assert exact.omitted_count == broad.item_count + broad.omitted_count - 1

        empty = _select(
            journal,
            MemoryQuery(
                workspace_id="ws_1",
                task_goal="SQLite pytest",
                max_items=0,
                max_rendered_chars=0,
            ),
        )
        assert empty.selected_items == ()
        assert empty.item_count == 0
        assert empty.omitted_count == broad.item_count + broad.omitted_count
        assert empty.rendered_chars == 0
    finally:
        session.close()


def test_selector_excludes_expired_and_prohibited_current_revisions(tmp_path):
    store = OperationalStore(tmp_path / "state", clock=FixedClock(NOW), maintenance_timeout=0)
    session = store.initialize()
    journal = SqliteOperationalJournal(session)
    try:
        _base, _convention, _domain, expired, prohibited = _seed_selector_subject(journal)
        selection = _select(
            journal,
            MemoryQuery(
                workspace_id="ws_1",
                task_goal="Expired prohibited",
                explicit_semantic_keys=(expired.semantic_key, prohibited.semantic_key),
            ),
        )
        assert selection.selected_items == ()
        assert selection.omitted_count == 0
    finally:
        session.close()
