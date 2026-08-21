"""Operational Store v13 DDL for the Preference v2 foundation.

Keep every statement in this module immutable after S56.  Later Preference
subplans add adapters and services, not columns or constraints to v13.
"""

from __future__ import annotations

V13_NAME = "preference_v2_foundation"

V13_STATEMENTS = (
    """
    CREATE TABLE preference_review_jobs (
        job_id TEXT PRIMARY KEY,
        workspace_id TEXT NOT NULL,
        session_id TEXT REFERENCES sessions(session_id),
        turn_id TEXT NOT NULL REFERENCES turns(turn_id),
        review_version INTEGER NOT NULL CHECK (review_version >= 1),
        status TEXT NOT NULL CHECK (
            status IN ('pending', 'running', 'completed', 'failed', 'exhausted',
                       'cancelled', 'superseded')
        ),
        source_global_revision INTEGER NOT NULL CHECK (source_global_revision >= 0),
        source_workspace_revision INTEGER NOT NULL CHECK (source_workspace_revision >= 0),
        active_snapshot_json TEXT NOT NULL CHECK (length(active_snapshot_json) >= 2),
        active_snapshot_count INTEGER NOT NULL CHECK (
            active_snapshot_count BETWEEN 0 AND 256
        ),
        active_snapshot_bytes INTEGER NOT NULL CHECK (
            active_snapshot_bytes BETWEEN 2 AND 196608
        ),
        active_snapshot_digest TEXT NOT NULL CHECK (length(active_snapshot_digest) = 64),
        reviewer_provider_id TEXT,
        reviewer_model_id TEXT,
        reviewer_prompt_version TEXT NOT NULL CHECK (
            length(reviewer_prompt_version) BETWEEN 1 AND 64
        ),
        reviewer_schema_version TEXT NOT NULL CHECK (
            length(reviewer_schema_version) BETWEEN 1 AND 64
        ),
        lease_id TEXT UNIQUE,
        lease_expires_at_unix INTEGER,
        attempt_count INTEGER NOT NULL CHECK (attempt_count BETWEEN 0 AND 3),
        row_version INTEGER NOT NULL CHECK (row_version >= 1),
        created_at_unix INTEGER NOT NULL,
        started_at_unix INTEGER,
        completed_at_unix INTEGER,
        failure_code TEXT CHECK (
            failure_code IS NULL OR failure_code IN (
                'context_budget', 'request_budget', 'safety_rejected',
                'snapshot_invalid', 'provider_unavailable', 'timeout',
                'malformed_output', 'lease_lost', 'cancelled', 'persistence'
            )
        ),
        UNIQUE (workspace_id, turn_id, review_version),
        CHECK (status != 'running' OR (
            lease_id IS NOT NULL AND lease_expires_at_unix IS NOT NULL
        )),
        CHECK (status IN ('failed', 'exhausted') OR failure_code IS NULL),
        CHECK (status IN ('pending', 'running') OR completed_at_unix IS NOT NULL),
        CHECK (status NOT IN ('pending', 'running') OR completed_at_unix IS NULL),
        CHECK (completed_at_unix IS NULL OR completed_at_unix >= created_at_unix)
    )
    """,
    """
    CREATE INDEX preference_review_jobs_pending
        ON preference_review_jobs(workspace_id, status, created_at_unix, job_id)
    """,
    """
    CREATE INDEX preference_review_jobs_lease
        ON preference_review_jobs(workspace_id, status, lease_expires_at_unix, job_id)
    """,
    """
    CREATE TABLE preference_evidence (
        evidence_id TEXT PRIMARY KEY,
        workspace_id TEXT NOT NULL,
        job_id TEXT NOT NULL UNIQUE REFERENCES preference_review_jobs(job_id),
        turn_id TEXT NOT NULL REFERENCES turns(turn_id),
        source_kind TEXT NOT NULL CHECK (source_kind = 'user_turn'),
        actor TEXT NOT NULL CHECK (actor = 'user'),
        excerpt_redacted TEXT,
        excerpt_bytes INTEGER NOT NULL CHECK (excerpt_bytes BETWEEN 0 AND 2048),
        content_digest TEXT NOT NULL CHECK (length(content_digest) = 64),
        safety_rejection_code TEXT CHECK (
            safety_rejection_code IS NULL OR safety_rejection_code IN (
                'secret_material', 'prohibited_personal_data',
                'hidden_unicode_control', 'capability_authorization',
                'prompt_injection'
            )
        ),
        observed_at_unix INTEGER NOT NULL,
        created_at_unix INTEGER NOT NULL,
        CHECK (excerpt_redacted IS NULL OR length(excerpt_redacted) BETWEEN 1 AND 512),
        CHECK ((excerpt_redacted IS NULL AND excerpt_bytes = 0)
            OR (excerpt_redacted IS NOT NULL AND excerpt_bytes > 0)),
        CHECK (safety_rejection_code IS NULL OR excerpt_redacted IS NULL)
    )
    """,
    """
    CREATE INDEX preference_evidence_workspace_job
        ON preference_evidence(workspace_id, job_id, evidence_id)
    """,
    """
    CREATE TABLE preference_proposals (
        proposal_id TEXT PRIMARY KEY,
        workspace_id TEXT NOT NULL,
        job_id TEXT NOT NULL REFERENCES preference_review_jobs(job_id),
        evidence_id TEXT NOT NULL REFERENCES preference_evidence(evidence_id),
        operation TEXT NOT NULL CHECK (operation IN ('add', 'replace', 'remove')),
        scope TEXT NOT NULL CHECK (scope IN ('global', 'workspace')),
        preference_id TEXT,
        operation_json TEXT NOT NULL CHECK (length(operation_json) BETWEEN 2 AND 8192),
        operation_bytes INTEGER NOT NULL CHECK (operation_bytes BETWEEN 2 AND 8192),
        operation_digest TEXT NOT NULL CHECK (length(operation_digest) = 64),
        expected_target_revision INTEGER CHECK (
            expected_target_revision IS NULL OR expected_target_revision >= 1
        ),
        expected_document_revision INTEGER NOT NULL CHECK (expected_document_revision >= 0),
        status TEXT NOT NULL CHECK (
            status IN ('proposed', 'accepted', 'edited_and_accepted', 'rejected',
                       'suppressed', 'stale', 'conflict', 'expired')
        ),
        final_operation_json TEXT,
        final_operation_bytes INTEGER CHECK (
            final_operation_bytes IS NULL OR final_operation_bytes BETWEEN 2 AND 8192
        ),
        decision_command_id TEXT,
        decision_reason TEXT,
        row_version INTEGER NOT NULL CHECK (row_version >= 1),
        created_at_unix INTEGER NOT NULL,
        resolved_at_unix INTEGER,
        UNIQUE (workspace_id, job_id, operation_digest),
        CHECK ((final_operation_json IS NULL) = (final_operation_bytes IS NULL)),
        CHECK (resolved_at_unix IS NULL OR resolved_at_unix >= created_at_unix)
    )
    """,
    """
    CREATE INDEX preference_proposals_inbox
        ON preference_proposals(workspace_id, status, created_at_unix, proposal_id)
    """,
    """
    CREATE INDEX preference_proposals_job
        ON preference_proposals(workspace_id, job_id, created_at_unix, proposal_id)
    """,
    """
    CREATE TABLE preference_proposal_evidence (
        workspace_id TEXT NOT NULL,
        proposal_id TEXT NOT NULL REFERENCES preference_proposals(proposal_id),
        evidence_id TEXT NOT NULL REFERENCES preference_evidence(evidence_id),
        PRIMARY KEY (proposal_id, evidence_id)
    )
    """,
    """
    CREATE INDEX preference_proposal_evidence_workspace
        ON preference_proposal_evidence(workspace_id, proposal_id, evidence_id)
    """,
    """
    CREATE TABLE preference_write_batches (
        batch_id TEXT PRIMARY KEY,
        workspace_id TEXT NOT NULL,
        scope TEXT NOT NULL CHECK (scope IN ('global', 'workspace')),
        command_id TEXT NOT NULL,
        operations_json TEXT NOT NULL CHECK (length(operations_json) BETWEEN 2 AND 196608),
        operations_bytes INTEGER NOT NULL CHECK (
            operations_bytes BETWEEN 2 AND 196608
        ),
        allocated_add_ids_json TEXT NOT NULL CHECK (length(allocated_add_ids_json) BETWEEN 2 AND 4096),
        proposal_ids_json TEXT NOT NULL CHECK (length(proposal_ids_json) BETWEEN 2 AND 8192),
        expected_document_revision INTEGER NOT NULL CHECK (expected_document_revision >= 0),
        before_document_revision INTEGER NOT NULL CHECK (before_document_revision >= 0),
        before_document_digest TEXT NOT NULL CHECK (length(before_document_digest) = 64),
        after_document_revision INTEGER NOT NULL CHECK (after_document_revision >= 0),
        after_document_digest TEXT NOT NULL CHECK (length(after_document_digest) = 64),
        before_document_json TEXT,
        after_document_json TEXT,
        status TEXT NOT NULL CHECK (
            status IN ('prepared', 'yaml_applied', 'finalized', 'needs_resolution', 'failed')
        ),
        recovery_code TEXT,
        row_version INTEGER NOT NULL CHECK (row_version >= 1),
        created_at_unix INTEGER NOT NULL,
        prepared_at_unix INTEGER,
        applied_at_unix INTEGER,
        finalized_at_unix INTEGER,
        UNIQUE (workspace_id, command_id),
        CHECK (before_document_json IS NULL OR length(before_document_json) BETWEEN 2 AND 196608),
        CHECK (after_document_json IS NULL OR length(after_document_json) BETWEEN 2 AND 196608),
        CHECK (finalized_at_unix IS NULL OR finalized_at_unix >= created_at_unix)
    )
    """,
    """
    CREATE INDEX preference_write_batches_recovery
        ON preference_write_batches(workspace_id, status, created_at_unix, batch_id)
    """,
    """
    CREATE TABLE preference_write_batch_proposals (
        workspace_id TEXT NOT NULL,
        batch_id TEXT NOT NULL REFERENCES preference_write_batches(batch_id),
        proposal_id TEXT NOT NULL REFERENCES preference_proposals(proposal_id),
        PRIMARY KEY (batch_id, proposal_id)
    )
    """,
    """
    CREATE INDEX preference_write_batch_proposals_workspace
        ON preference_write_batch_proposals(workspace_id, batch_id, proposal_id)
    """,
    """
    CREATE TRIGGER preference_review_jobs_workspace_guard_insert
    BEFORE INSERT ON preference_review_jobs
    BEGIN
        SELECT CASE WHEN EXISTS (
            SELECT 1 FROM turns t
            WHERE t.turn_id = NEW.turn_id AND t.session_id IS NULL
        ) THEN RAISE(ABORT, 'preference turn is invalid') END;
        SELECT CASE WHEN EXISTS (
            SELECT 1 FROM sessions s
            WHERE s.session_id = NEW.session_id AND s.workspace_id != NEW.workspace_id
        ) THEN RAISE(ABORT, 'preference job workspace mismatch') END;
        SELECT CASE WHEN EXISTS (
            SELECT 1 FROM turns t
            WHERE t.turn_id = NEW.turn_id
              AND NEW.session_id IS NOT NULL
              AND t.session_id != NEW.session_id
        ) THEN RAISE(ABORT, 'preference job session mismatch') END;
        SELECT CASE WHEN EXISTS (
            SELECT 1 FROM turns t
            WHERE t.turn_id = NEW.turn_id AND t.session_id IN (
                SELECT s.session_id FROM sessions s WHERE s.workspace_id != NEW.workspace_id
            )
        ) THEN RAISE(ABORT, 'preference job workspace mismatch') END;
    END
    """,
    """
    CREATE TRIGGER preference_evidence_workspace_guard_insert
    BEFORE INSERT ON preference_evidence
    BEGIN
        SELECT CASE WHEN EXISTS (
            SELECT 1 FROM preference_review_jobs j
            WHERE j.job_id = NEW.job_id
              AND (j.workspace_id != NEW.workspace_id OR j.turn_id != NEW.turn_id)
        ) THEN RAISE(ABORT, 'preference evidence workspace mismatch') END;
        SELECT CASE WHEN EXISTS (
            SELECT 1 FROM turns t
            JOIN sessions s ON s.session_id = t.session_id
            WHERE t.turn_id = NEW.turn_id AND s.workspace_id != NEW.workspace_id
        ) THEN RAISE(ABORT, 'preference evidence workspace mismatch') END;
    END
    """,
    """
    CREATE TRIGGER preference_proposals_workspace_guard_insert
    BEFORE INSERT ON preference_proposals
    BEGIN
        SELECT CASE WHEN EXISTS (
            SELECT 1 FROM preference_review_jobs j
            WHERE j.job_id = NEW.job_id AND j.workspace_id != NEW.workspace_id
        ) THEN RAISE(ABORT, 'preference proposal workspace mismatch') END;
        SELECT CASE WHEN EXISTS (
            SELECT 1 FROM preference_evidence e
            WHERE e.evidence_id = NEW.evidence_id AND e.workspace_id != NEW.workspace_id
        ) THEN RAISE(ABORT, 'preference proposal workspace mismatch') END;
    END
    """,
    """
    CREATE TRIGGER preference_proposal_evidence_workspace_guard_insert
    BEFORE INSERT ON preference_proposal_evidence
    BEGIN
        SELECT CASE WHEN EXISTS (
            SELECT 1 FROM preference_proposals p
            JOIN preference_evidence e ON e.evidence_id = NEW.evidence_id
            WHERE p.proposal_id = NEW.proposal_id
              AND (p.workspace_id != NEW.workspace_id OR e.workspace_id != NEW.workspace_id)
        ) THEN RAISE(ABORT, 'preference proposal evidence workspace mismatch') END;
    END
    """,
    """
    CREATE TRIGGER preference_write_batch_proposals_workspace_guard_insert
    BEFORE INSERT ON preference_write_batch_proposals
    BEGIN
        SELECT CASE WHEN EXISTS (
            SELECT 1 FROM preference_write_batches b
            JOIN preference_proposals p ON p.proposal_id = NEW.proposal_id
            WHERE b.batch_id = NEW.batch_id
              AND (b.workspace_id != NEW.workspace_id OR p.workspace_id != NEW.workspace_id)
        ) THEN RAISE(ABORT, 'preference write batch workspace mismatch') END;
    END
    """,
)
