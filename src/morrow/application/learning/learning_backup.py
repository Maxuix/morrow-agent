"""Restore-time checks for v10/v11 Learning links in a SQLite backup."""

from __future__ import annotations

import sqlite3

_LEARNING_SCHEMA_VERSION = 10
_LEARNING_TABLES = frozenset(
    {
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
        "promotion_operations",
        "configuration_activations",
    }
)


def verify_learning_references(connection: sqlite3.Connection) -> tuple[bool, tuple[str, ...]]:
    """Verify bounded Learning references without reading YAML or credentials."""

    try:
        schema_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
    except (TypeError, ValueError, sqlite3.Error):
        return False, ("learning_schema_unreadable",)
    if schema_version < _LEARNING_SCHEMA_VERSION:
        return True, ()
    if not _LEARNING_TABLES.issubset(tables):
        return False, ("learning_schema_tables_missing",)

    checks = (
        (
            "learning_review_subject",
            """
            SELECT COUNT(*) FROM learning_reviews r
            LEFT JOIN task_runs t ON t.task_run_id = r.task_run_id
            LEFT JOIN task_outcomes o ON o.outcome_id = r.task_outcome_id
            WHERE t.task_run_id IS NULL OR o.outcome_id IS NULL
               OR t.workspace_id != r.workspace_id OR o.workspace_id != r.workspace_id
               OR t.task_run_id != o.task_run_id
            """,
        ),
        (
            "learning_evidence_owner",
            """
            SELECT COUNT(*) FROM learning_evidence e
            LEFT JOIN learning_reviews r ON r.review_id = e.origin_review_id
            LEFT JOIN task_runs t ON t.task_run_id = e.task_run_id
            WHERE r.review_id IS NULL OR t.task_run_id IS NULL
               OR r.workspace_id != e.workspace_id OR t.workspace_id != e.workspace_id
               OR r.task_run_id != e.task_run_id
            """,
        ),
        (
            "learning_review_evidence_link",
            """
            SELECT COUNT(*) FROM learning_review_evidence l
            LEFT JOIN learning_reviews r ON r.review_id = l.review_id
            LEFT JOIN learning_evidence e ON e.evidence_id = l.evidence_id
            WHERE r.review_id IS NULL OR e.evidence_id IS NULL
               OR r.workspace_id != l.workspace_id OR e.workspace_id != l.workspace_id
               OR e.origin_review_id != l.review_id
            """,
        ),
        (
            "learning_candidate_review",
            """
            SELECT COUNT(*) FROM learning_candidates c
            LEFT JOIN learning_reviews r ON r.review_id = c.origin_review_id
            WHERE r.review_id IS NULL OR r.workspace_id != c.workspace_id
            """,
        ),
        (
            "learning_candidate_evidence_link",
            """
            SELECT COUNT(*) FROM learning_candidate_evidence l
            LEFT JOIN learning_candidates c ON c.candidate_id = l.candidate_id
            LEFT JOIN learning_evidence e ON e.evidence_id = l.evidence_id
            WHERE c.candidate_id IS NULL OR e.evidence_id IS NULL
               OR c.workspace_id != l.workspace_id OR e.workspace_id != l.workspace_id
            """,
        ),
        (
            "learning_decision_candidate",
            """
            SELECT COUNT(*) FROM learning_candidate_decisions d
            LEFT JOIN learning_candidates c ON c.candidate_id = d.candidate_id
            WHERE c.candidate_id IS NULL OR c.workspace_id != d.workspace_id
               OR d.original_proposal_digest != c.fingerprint
            """,
        ),
        (
            "learning_promotion_candidate",
            """
            SELECT COUNT(*) FROM promotion_operations p
            LEFT JOIN learning_candidates c ON c.candidate_id = p.candidate_id
            WHERE c.candidate_id IS NULL OR c.workspace_id != p.workspace_id
            """,
        ),
        (
            "learning_activation_links",
            """
            SELECT COUNT(*) FROM configuration_activations a
            LEFT JOIN learning_candidates c ON c.candidate_id = a.candidate_id
            LEFT JOIN learning_candidate_decisions d ON d.decision_id = a.decision_id
            LEFT JOIN promotion_operations p ON p.operation_id = a.operation_id
            WHERE c.candidate_id IS NULL OR d.decision_id IS NULL OR p.operation_id IS NULL
               OR c.workspace_id != a.workspace_id OR d.workspace_id != a.workspace_id
               OR p.workspace_id != a.workspace_id
               OR d.candidate_id != a.candidate_id OR p.candidate_id != a.candidate_id
            """,
        ),
        (
            "learning_knowledge_revision",
            """
            SELECT COUNT(*) FROM project_knowledge_revisions r
            LEFT JOIN project_knowledge_heads h ON h.knowledge_id = r.knowledge_id
            LEFT JOIN learning_candidates c ON c.candidate_id = r.source_candidate_id
            LEFT JOIN learning_candidate_decisions d ON d.decision_id = r.source_decision_id
            WHERE h.knowledge_id IS NULL OR h.workspace_id != r.workspace_id
               OR (r.source_candidate_id IS NOT NULL AND c.candidate_id IS NULL)
               OR (r.source_decision_id IS NOT NULL AND d.decision_id IS NULL)
            """,
        ),
        (
            "learning_knowledge_evidence_link",
            """
            SELECT COUNT(*) FROM project_knowledge_evidence l
            LEFT JOIN project_knowledge_revisions r
                ON r.knowledge_revision_id = l.knowledge_revision_id
            LEFT JOIN learning_evidence e ON e.evidence_id = l.evidence_id
            WHERE r.knowledge_revision_id IS NULL OR e.evidence_id IS NULL
               OR r.workspace_id != l.workspace_id OR e.workspace_id != l.workspace_id
            """,
        ),
    )
    issues: list[str] = []
    for code, query in checks:
        try:
            count = int(connection.execute(query).fetchone()[0])
        except sqlite3.Error:
            issues.append(f"{code}_unreadable")
            continue
        if count:
            issues.append(code)
    return not issues, tuple(dict.fromkeys(issues))


__all__ = ["verify_learning_references"]
