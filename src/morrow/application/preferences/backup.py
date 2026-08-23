"""Restore-time integrity checks for Operational Store v13 Preference records."""

from __future__ import annotations

import hashlib
import json
import sqlite3

_PREFERENCE_SCHEMA_VERSION = 13
_PREFERENCE_TABLES = frozenset(
    {
        "preference_review_jobs",
        "preference_evidence",
        "preference_proposals",
        "preference_proposal_evidence",
        "preference_write_batches",
        "preference_write_batch_proposals",
    }
)


def verify_preference_references(connection: sqlite3.Connection) -> tuple[bool, tuple[str, ...]]:
    """Verify v13 links and bounded JSON metadata without consulting YAML or credentials."""

    try:
        schema_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
    except (TypeError, ValueError, sqlite3.Error):
        return False, ("preference_schema_unreadable",)
    if schema_version < _PREFERENCE_SCHEMA_VERSION:
        return True, ()
    if not _PREFERENCE_TABLES.issubset(tables):
        return False, ("preference_schema_tables_missing",)

    checks = (
        (
            "preference_job_subject",
            """
            SELECT COUNT(*) FROM preference_review_jobs j
            LEFT JOIN turns t ON t.turn_id = j.turn_id
            LEFT JOIN sessions s ON s.session_id = t.session_id
            WHERE t.turn_id IS NULL OR s.session_id IS NULL
               OR s.workspace_id != j.workspace_id
               OR (j.session_id IS NOT NULL AND j.session_id != t.session_id)
            """,
        ),
        (
            "preference_evidence_job",
            """
            SELECT COUNT(*) FROM preference_evidence e
            LEFT JOIN preference_review_jobs j ON j.job_id = e.job_id
            WHERE j.job_id IS NULL OR j.workspace_id != e.workspace_id
               OR j.turn_id != e.turn_id
            """,
        ),
        (
            "preference_job_evidence_cardinality",
            """
            SELECT COUNT(*) FROM preference_review_jobs j
            LEFT JOIN preference_evidence e ON e.job_id = j.job_id
            GROUP BY j.job_id HAVING COUNT(e.evidence_id) != 1
            """,
        ),
        (
            "preference_proposal_links",
            """
            SELECT COUNT(*) FROM preference_proposals p
            LEFT JOIN preference_review_jobs j ON j.job_id = p.job_id
            LEFT JOIN preference_evidence e ON e.evidence_id = p.evidence_id
            WHERE j.job_id IS NULL OR e.evidence_id IS NULL
               OR j.workspace_id != p.workspace_id OR e.workspace_id != p.workspace_id
               OR e.job_id != p.job_id
            """,
        ),
        (
            "preference_proposal_evidence_links",
            """
            SELECT COUNT(*) FROM preference_proposal_evidence l
            LEFT JOIN preference_proposals p ON p.proposal_id = l.proposal_id
            LEFT JOIN preference_evidence e ON e.evidence_id = l.evidence_id
            WHERE p.proposal_id IS NULL OR e.evidence_id IS NULL
               OR p.workspace_id != l.workspace_id OR e.workspace_id != l.workspace_id
               OR p.evidence_id != l.evidence_id
            """,
        ),
        (
            "preference_batch_proposal_links",
            """
            SELECT COUNT(*) FROM preference_write_batch_proposals l
            LEFT JOIN preference_write_batches b ON b.batch_id = l.batch_id
            LEFT JOIN preference_proposals p ON p.proposal_id = l.proposal_id
            WHERE b.batch_id IS NULL OR p.proposal_id IS NULL
               OR b.workspace_id != l.workspace_id OR p.workspace_id != l.workspace_id
               OR b.scope != p.scope
            """,
        ),
        (
            "preference_job_lease_state",
            """
            SELECT COUNT(*) FROM preference_review_jobs
            WHERE (status = 'running') != (lease_id IS NOT NULL AND lease_expires_at_unix IS NOT NULL)
               OR attempt_count NOT BETWEEN 0 AND 3
               OR (status IN ('pending', 'running') AND completed_at_unix IS NOT NULL)
               OR (status NOT IN ('pending', 'running') AND completed_at_unix IS NULL)
               OR (failure_code IS NOT NULL AND status NOT IN ('failed', 'exhausted'))
            """,
        ),
    )
    issues: list[str] = []
    for code, query in checks:
        try:
            rows = connection.execute(query).fetchall()
            count = sum(int(row[0]) for row in rows)
        except (TypeError, ValueError, sqlite3.Error):
            issues.append(f"{code}_unreadable")
            continue
        if count:
            issues.append(code)

    try:
        jobs = connection.execute(
            "SELECT active_snapshot_json, active_snapshot_count, active_snapshot_bytes, "
            "active_snapshot_digest FROM preference_review_jobs"
        ).fetchall()
        for raw, count, byte_count, digest in jobs:
            encoded = str(raw).encode("utf-8")
            payload = json.loads(str(raw))
            entries = payload.get("entries") if isinstance(payload, dict) else None
            if (
                not isinstance(entries, list)
                or len(entries) != int(count)
                or len(encoded) != int(byte_count)
                or hashlib.sha256(encoded).hexdigest() != str(digest)
            ):
                issues.append("preference_job_snapshot")
                break
    except (TypeError, ValueError, json.JSONDecodeError, sqlite3.Error):
        issues.append("preference_job_snapshot_unreadable")

    try:
        proposals = connection.execute(
            "SELECT operation_json, operation_bytes, operation_digest, final_operation_json, "
            "final_operation_bytes FROM preference_proposals"
        ).fetchall()
        for raw, byte_count, digest, final_raw, final_bytes in proposals:
            encoded = str(raw).encode("utf-8")
            json.loads(str(raw))
            final_valid = final_raw is None and final_bytes is None
            if final_raw is not None and final_bytes is not None:
                json.loads(str(final_raw))
                final_valid = len(str(final_raw).encode("utf-8")) == int(final_bytes)
            if (
                len(encoded) != int(byte_count)
                or hashlib.sha256(encoded).hexdigest() != str(digest)
                or not final_valid
            ):
                issues.append("preference_proposal_payload")
                break
    except (TypeError, ValueError, json.JSONDecodeError, sqlite3.Error):
        issues.append("preference_proposal_payload_unreadable")

    return not issues, tuple(dict.fromkeys(issues))


__all__ = ["verify_preference_references"]
