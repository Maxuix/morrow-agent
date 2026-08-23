"""Restore-time checks for v12 Memory references in a SQLite backup."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime

from morrow.adapters.state.preference_snapshot_compat import decode_agent_run_snapshot
from morrow.application.learning.memory_selector import memory_selection_digest
from morrow.core.domain import canonical_json_bytes
from morrow.core.learning import LearningSensitivity
from morrow.core.learning_memory import (
    ProjectKnowledgeCategory,
    ProjectKnowledgeHead,
    ProjectKnowledgeRevision,
    ProjectKnowledgeStatus,
)
from morrow.core.memory_rendering import project_knowledge_content_digest, render_project_knowledge
from morrow.core.memory_selection import MemorySelection, MemorySelectionItem

_MEMORY_TABLES = frozenset(
    {
        "memory_selections",
        "memory_selection_items",
        "memory_search_terms",
        "project_knowledge_heads",
        "project_knowledge_revisions",
        "agent_runs",
        "sessions",
    }
)


def verify_memory_references(connection: sqlite3.Connection) -> tuple[bool, tuple[str, ...]]:
    """Verify immutable selection, Knowledge, term, and AgentRun links in a backup DB."""

    tables = {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }
    if not _MEMORY_TABLES.issubset(tables):
        try:
            schema_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        except (TypeError, ValueError, sqlite3.Error):
            return False, ("memory_schema_version_invalid",)
        if schema_version < 12:
            # Backups made before v12 have no Memory selection authority to verify.
            return True, ()
        return False, ("memory_schema_tables_missing",)

    issues: list[str] = []
    heads = _read_heads(connection, issues)
    revisions = _read_revisions(connection, issues)
    selections: dict[str, MemorySelection] = {}
    for row in connection.execute(
        "SELECT selection_id, workspace_id, query_digest, source_memory_revision, "
        "item_count, omitted_count, rendered_chars, selection_digest, created_at_unix "
        "FROM memory_selections ORDER BY created_at_unix ASC, selection_id ASC"
    ).fetchall():
        selection_id = str(row[0])
        try:
            item_rows = connection.execute(
                "SELECT selection_id, workspace_id, ordinal, record_kind, record_id, "
                "record_revision_id, revision, reason_json, reason_bytes, estimated_chars, "
                "rendered_content_digest FROM memory_selection_items "
                "WHERE selection_id = ? ORDER BY ordinal ASC",
                (selection_id,),
            ).fetchall()
            items = tuple(_item_from_row(item_row) for item_row in item_rows)
            selection = MemorySelection(
                selection_id=selection_id,
                workspace_id=str(row[1]),
                query_digest=str(row[2]),
                source_memory_revision=int(row[3]),
                selected_items=items,
                item_count=int(row[4]),
                omitted_count=int(row[5]),
                rendered_chars=int(row[6]),
                selection_digest=str(row[7]),
                created_at=_timestamp(row[8]),
            )
            selections[selection_id] = selection
            if memory_selection_digest(selection) != selection.selection_digest:
                issues.append("memory_selection_digest")
            rendered_chars = 0
            for item in items:
                head = heads.get(item.record_id)
                revision = revisions.get(item.record_revision_id)
                if (
                    head is None
                    or revision is None
                    or head.workspace_id != selection.workspace_id
                    or revision.workspace_id != selection.workspace_id
                    or revision.knowledge_id != head.knowledge_id
                    or revision.revision != item.revision
                ):
                    issues.append("memory_selection_reference")
                    continue
                rendered_chars += len(render_project_knowledge(head, revision))
                if project_knowledge_content_digest(head, revision) != item.rendered_content_digest:
                    issues.append("memory_selection_content_digest")
            if rendered_chars != selection.rendered_chars:
                issues.append("memory_selection_rendered_chars")
        except (TypeError, ValueError, IndexError, OverflowError, json.JSONDecodeError):
            issues.append("memory_selection_invalid")

    for row in connection.execute(
        "SELECT workspace_id, knowledge_revision_id FROM memory_search_terms"
    ).fetchall():
        revision = revisions.get(str(row[1]))
        if revision is None or revision.workspace_id != str(row[0]):
            issues.append("memory_term_reference")

    for row in connection.execute(
        "SELECT r.agent_run_id, s.workspace_id, r.snapshot_json FROM agent_runs r "
        "JOIN sessions s ON s.session_id = r.session_id "
        "ORDER BY r.created_at_unix ASC, r.agent_run_id ASC"
    ).fetchall():
        try:
            snapshot = decode_agent_run_snapshot(json.loads(str(row[2])))
            selection_id = snapshot.memory_selection_id
            if selection_id is None:
                continue
            selection = selections.get(selection_id)
            if (
                selection is None
                or selection.workspace_id != str(row[1])
                or snapshot.memory_selection_digest != selection.selection_digest
                or snapshot.memory_snapshot_revision != selection.source_memory_revision
            ):
                issues.append("memory_agent_run_reference")
        except (TypeError, ValueError, json.JSONDecodeError):
            issues.append("memory_agent_run_snapshot")

    return not issues, tuple(dict.fromkeys(issues))


def _read_heads(
    connection: sqlite3.Connection, issues: list[str]
) -> dict[str, ProjectKnowledgeHead]:
    heads: dict[str, ProjectKnowledgeHead] = {}
    for row in connection.execute(
        "SELECT knowledge_id, workspace_id, semantic_key, category, status, "
        "current_revision_id, row_version, created_at_unix, updated_at_unix "
        "FROM project_knowledge_heads"
    ).fetchall():
        try:
            head = ProjectKnowledgeHead(
                knowledge_id=str(row[0]),
                workspace_id=str(row[1]),
                semantic_key=str(row[2]),
                category=ProjectKnowledgeCategory(str(row[3])),
                status=ProjectKnowledgeStatus(str(row[4])),
                current_revision_id=str(row[5]) if row[5] is not None else None,
                row_version=int(row[6]),
                created_at=_timestamp(row[7]),
                updated_at=_timestamp(row[8]),
            )
            heads[head.knowledge_id] = head
        except (TypeError, ValueError, IndexError, OverflowError):
            issues.append("memory_knowledge_head")
    return heads


def _read_revisions(
    connection: sqlite3.Connection, issues: list[str]
) -> dict[str, ProjectKnowledgeRevision]:
    revisions: dict[str, ProjectKnowledgeRevision] = {}
    for row in connection.execute(
        "SELECT knowledge_revision_id, knowledge_id, workspace_id, revision, statement, "
        "statement_digest, source_candidate_id, source_decision_id, supersedes_revision_id, "
        "sensitivity, valid_from_unix, valid_until_unix, created_at_unix, last_confirmed_at_unix "
        "FROM project_knowledge_revisions"
    ).fetchall():
        try:
            revision = ProjectKnowledgeRevision(
                knowledge_revision_id=str(row[0]),
                knowledge_id=str(row[1]),
                workspace_id=str(row[2]),
                revision=int(row[3]),
                statement=str(row[4]),
                statement_digest=str(row[5]),
                source_candidate_id=str(row[6]) if row[6] is not None else None,
                source_decision_id=str(row[7]) if row[7] is not None else None,
                supersedes_revision_id=str(row[8]) if row[8] is not None else None,
                sensitivity=LearningSensitivity(str(row[9])),
                valid_from=_optional_timestamp(row[10]),
                valid_until=_optional_timestamp(row[11]),
                created_at=_timestamp(row[12]),
                last_confirmed_at=_timestamp(row[13]),
            )
            revisions[revision.knowledge_revision_id] = revision
        except (TypeError, ValueError, IndexError, OverflowError):
            issues.append("memory_knowledge_revision")
    return revisions


def _item_from_row(row: tuple[object, ...]) -> MemorySelectionItem:
    reason_json = str(row[7])
    if int(row[8]) != len(reason_json.encode("utf-8")):
        raise ValueError("memory selection reason byte count is invalid")
    reasons = json.loads(reason_json)
    if not isinstance(reasons, list):
        raise ValueError("memory selection reasons are invalid")
    canonical_reasons = canonical_json_bytes(reasons).decode("utf-8")
    if canonical_reasons != reason_json:
        raise ValueError("memory selection reasons are not canonical")
    return MemorySelectionItem(
        selection_id=str(row[0]),
        workspace_id=str(row[1]),
        ordinal=int(row[2]),
        record_kind=str(row[3]),
        record_id=str(row[4]),
        record_revision_id=str(row[5]),
        revision=int(row[6]),
        reason_codes=tuple(reasons),
        estimated_chars=int(row[9]),
        rendered_content_digest=str(row[10]),
    )


def _timestamp(value: object) -> datetime:
    return datetime.fromtimestamp(int(value), UTC)


def _optional_timestamp(value: object) -> datetime | None:
    return None if value is None else _timestamp(value)


__all__ = ["verify_memory_references"]
