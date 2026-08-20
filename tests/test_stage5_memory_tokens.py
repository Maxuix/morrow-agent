from __future__ import annotations

from datetime import UTC, datetime

import pytest

from morrow.adapters.state.journal import SqliteOperationalJournal
from morrow.adapters.state.operational import OperationalStore
from morrow.application.api import OperationalApplicationService
from morrow.application.learning.memory_terms import (
    rebuild_project_knowledge_terms,
    refresh_project_knowledge_terms,
    retrieve_memory_term_candidates,
)
from morrow.core.learning_commands import (
    DisableProjectKnowledgeCommand,
    EnableProjectKnowledgeCommand,
)
from morrow.core.memory_selection import MemorySearchTokenKind, MemorySearchWeightBand
from morrow.core.memory_tokens import (
    tokenize_memory_query,
    tokenize_memory_record,
    tokenize_memory_text,
)
from morrow.testing import FixedClock, FixedIdSource
from test_stage5_memory_selection import _knowledge_subject

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def test_memory_tokenizer_is_bounded_and_mixes_code_paths_and_cjk():
    tokens = tokenize_memory_record(
        semantic_key="architecture.persistence",
        category="architecture",
        statement="Use gitStatus in /src/Morrow/app.py with SQLite 3.12 数据库",
    )
    assert tokens == tokenize_memory_record(
        semantic_key="architecture.persistence",
        category="architecture",
        statement="Use gitStatus in /src/Morrow/app.py with SQLite 3.12 数据库",
    )
    values = {(item.token_kind, item.token) for item in tokens}
    assert (MemorySearchTokenKind.IDENTIFIER, "architecture.persistence") in values
    assert (MemorySearchTokenKind.IDENTIFIER, "gitstatus") in values
    assert (MemorySearchTokenKind.WORD, "git") in values
    assert (MemorySearchTokenKind.WORD, "status") in values
    assert (MemorySearchTokenKind.PATH, "src/morrow/app.py") in values
    assert (MemorySearchTokenKind.NUMBER, "3.12") in values
    assert (MemorySearchTokenKind.CJK_BIGRAM, "数据") in values
    assert (MemorySearchTokenKind.CJK_BIGRAM, "据库") in values
    assert (MemorySearchTokenKind.WORD, "the") not in values


def test_memory_tokenizer_rejects_oversized_input_and_keeps_query_budget():
    with pytest.raises(ValueError):
        tokenize_memory_text("x" * 8_193)
    query = tokenize_memory_query("Operational state is persisted in SQLite", limit=3)
    assert len(query) == 3
    assert all(item.weight_band is MemorySearchWeightBand.MEDIUM for item in query)


def test_long_cjk_query_preserves_code_and_identifier_tokens():
    query = tokenize_memory_query(
        "这是一个需要检查持久化状态的中文问题" * 8 + " SQLite src/morrow/app.py"
    )
    values = {(item.token_kind, item.token) for item in query}
    assert len(query) <= 64
    assert (MemorySearchTokenKind.PATH, "src/morrow/app.py") in values
    assert (MemorySearchTokenKind.IDENTIFIER, "sqlite") in values


def test_memory_terms_rebuild_retrieve_and_follow_knowledge_lifecycle(tmp_path):
    store = OperationalStore(tmp_path / "state", clock=FixedClock(NOW), maintenance_timeout=0)
    session = store.initialize()
    journal = SqliteOperationalJournal(session)
    try:
        head, revision = _knowledge_subject(journal)
        current_head = journal.get_project_knowledge_head("ws_1", head.knowledge_id)
        assert current_head is not None
        built = journal.transact(
            lambda txn: refresh_project_knowledge_terms(txn, "ws_1", current_head)
        )
        assert built
        assert journal.list_memory_search_terms("ws_1", knowledge_revision_id="krv_1") == built

        candidates = journal.transact(
            lambda txn: retrieve_memory_term_candidates(txn, "ws_1", "operational SQLite")
        )
        assert candidates[0].knowledge_revision_id == revision.knowledge_revision_id
        assert {term.token for term in candidates[0].matched_terms} >= {"operational", "sqlite"}

        rebuilt = journal.transact(lambda txn: rebuild_project_knowledge_terms(txn, "ws_1"))
        assert rebuilt == 1
        api = OperationalApplicationService(
            journal=journal,
            workspace_id="ws_1",
            id_source=FixedIdSource(),
            clock=lambda: NOW,
        )
        disabled = api.disable_project_knowledge(
            DisableProjectKnowledgeCommand(
                workspace_id="ws_1",
                knowledge_id=current_head.knowledge_id,
                expected_row_version=2,
                command_id="cmd_terms_disable",
            )
        )
        assert disabled.value.operation == "disabled"
        assert journal.list_memory_search_terms("ws_1", knowledge_revision_id="krv_1") == ()

        enabled = api.enable_project_knowledge(
            EnableProjectKnowledgeCommand(
                workspace_id="ws_1",
                knowledge_id=current_head.knowledge_id,
                expected_row_version=3,
                command_id="cmd_terms_enable",
            )
        )
        assert enabled.value.operation == "enabled"
        assert journal.list_memory_search_terms("ws_1", knowledge_revision_id="krv_1")
    finally:
        session.close()
