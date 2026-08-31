"""Ordered, checksummed Operational Store migrations.

Production currently owns schema v1–v19. Version 10 adds the governed Learning
foundation, version 11 adds immutable decisions plus Project Knowledge, and version
12 adds immutable MemorySelection records plus rebuildable lexical terms without
rewriting older evidence or creating a second configuration authority. Version 13
reserves the generic Preference Review, Evidence, Proposal, and Writer saga state.
Version 14 adds the immutable Skill catalog foundation (definitions, versions,
catalog operations, and the reserved AgentRun selection/context tables). Version
15 adds generated Skill Draft, validation and observational Usage records.
Version 16 adds MCP desired-state projections, Catalog revisions, and reserved
run snapshot/artifact-link tables. Version 17 adds bounded AgentRun model-request
observations and terminal metrics without mutating the immutable AgentRun snapshot.
Version 18 adds additive completion-truth observability columns with safe defaults
for older terminal rows. Version 19 adds per-request prompt projection evidence and
append-only structured completion-intent results. Version 20 adds bounded
long-horizon token-accounting, compaction, and overflow-recovery observations.
Version 21 adds mutable, bounded retry progress for safe AgentRun resume. Version 22 adds the
bounded durable steering and follow-up queue.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from morrow.adapters.state.migrations_v13_preferences import V13_NAME, V13_STATEMENTS
from morrow.adapters.state.migrations_v14_skills import V14_NAME, V14_STATEMENTS
from morrow.adapters.state.migrations_v15_skill_learning import V15_NAME, V15_STATEMENTS
from morrow.adapters.state.migrations_v16_mcp import V16_NAME, V16_STATEMENTS
from morrow.adapters.state.migrations_v17_observability import V17_NAME, V17_STATEMENTS
from morrow.adapters.state.migrations_v18_completion_truth import V18_NAME, V18_STATEMENTS
from morrow.adapters.state.migrations_v19_request_evidence import V19_NAME, V19_STATEMENTS
from morrow.adapters.state.migrations_v20_long_horizon_observability import (
    V20_NAME,
    V20_STATEMENTS,
)
from morrow.adapters.state.migrations_v21_retry_progress import V21_NAME, V21_STATEMENTS
from morrow.adapters.state.migrations_v22_runtime_control import V22_NAME, V22_STATEMENTS
from morrow.adapters.state.migrations_v23_agent_definitions import V23_NAME, V23_STATEMENTS
from morrow.adapters.state.migrations_v24_workflows import V24_NAME, V24_STATEMENTS
from morrow.core.store import (
    APPLICATION_NAME,
    RESERVED_SCHEMA_VERSIONS,
    SUPPORTED_SCHEMA_VERSION,
    StorageError,
    StorageErrorCode,
)

V1_NAME = "operational_store_identity"
V1_STATEMENTS = (
    """
    CREATE TABLE store_identity (
        singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
        application_name TEXT NOT NULL
            CHECK (application_name = 'morrow-operational-store'),
        schema_version INTEGER NOT NULL,
        created_at_unix INTEGER NOT NULL
    )
    """,
    """
    CREATE TABLE schema_migrations (
        version INTEGER PRIMARY KEY,
        name TEXT NOT NULL,
        checksum TEXT NOT NULL,
        applied_at_unix INTEGER NOT NULL
    )
    """,
)


@dataclass(frozen=True)
class SchemaMigration:
    version: int
    name: str
    statements: tuple[str, ...]

    @property
    def checksum(self) -> str:
        canonical = "\n".join(statement.strip() for statement in self.statements)
        payload = f"{self.version}\n{self.name}\n{canonical}".encode()
        return hashlib.sha256(payload).hexdigest()


V1 = SchemaMigration(version=1, name=V1_NAME, statements=V1_STATEMENTS)

V2_NAME = "durable_session_conversation"
V2_STATEMENTS = (
    """
    CREATE TABLE sessions (
        session_id TEXT PRIMARY KEY,
        workspace_id TEXT NOT NULL,
        lifecycle TEXT NOT NULL
            CHECK (lifecycle IN ('active', 'archived', 'deleted')),
        health TEXT NOT NULL
            CHECK (health IN ('ok', 'needs_recovery', 'quarantined', 'read_only')),
        current_task_run_id TEXT,
        conversation_position INTEGER NOT NULL CHECK (conversation_position >= 0),
        created_at_unix INTEGER NOT NULL,
        updated_at_unix INTEGER NOT NULL
    )
    """,
    """
    CREATE INDEX sessions_workspace_lifecycle
        ON sessions(workspace_id, lifecycle)
    """,
    """
    CREATE TABLE task_runs (
        task_run_id TEXT PRIMARY KEY,
        session_id TEXT NOT NULL REFERENCES sessions(session_id),
        workspace_id TEXT NOT NULL,
        status TEXT NOT NULL CHECK (status IN ('open')),
        created_at_unix INTEGER NOT NULL
    )
    """,
    """
    CREATE INDEX task_runs_session ON task_runs(session_id)
    """,
    """
    CREATE TABLE turns (
        turn_id TEXT PRIMARY KEY,
        session_id TEXT NOT NULL REFERENCES sessions(session_id),
        task_run_id TEXT NOT NULL REFERENCES task_runs(task_run_id),
        client_message_id TEXT NOT NULL,
        created_at_unix INTEGER NOT NULL,
        UNIQUE (session_id, client_message_id)
    )
    """,
    """
    CREATE INDEX turns_session ON turns(session_id)
    """,
    """
    CREATE TABLE agent_runs (
        agent_run_id TEXT PRIMARY KEY,
        turn_id TEXT NOT NULL REFERENCES turns(turn_id),
        session_id TEXT NOT NULL REFERENCES sessions(session_id),
        resume_of_agent_run_id TEXT REFERENCES agent_runs(agent_run_id),
        snapshot_json TEXT NOT NULL,
        created_at_unix INTEGER NOT NULL
    )
    """,
    """
    CREATE INDEX agent_runs_turn ON agent_runs(turn_id)
    """,
    """
    CREATE TABLE conversation_records (
        record_id TEXT PRIMARY KEY,
        session_id TEXT NOT NULL REFERENCES sessions(session_id),
        conversation_position INTEGER NOT NULL CHECK (conversation_position >= 1),
        kind TEXT NOT NULL CHECK (kind IN ('message', 'terminal')),
        payload_json TEXT NOT NULL,
        payload_bytes INTEGER NOT NULL CHECK (payload_bytes >= 0),
        UNIQUE (session_id, conversation_position)
    )
    """,
    """
    CREATE INDEX conversation_records_session
        ON conversation_records(session_id, conversation_position)
    """,
    """
    CREATE TABLE turn_submit_receipts (
        session_id TEXT NOT NULL REFERENCES sessions(session_id),
        client_message_id TEXT NOT NULL,
        request_digest TEXT NOT NULL,
        disposition TEXT NOT NULL
            CHECK (disposition IN (
                'accepted_open', 'accepted_closed', 'recovery', 'conflict'
            )),
        turn_id TEXT REFERENCES turns(turn_id),
        command_id TEXT,
        PRIMARY KEY (session_id, client_message_id)
    )
    """,
)

V2 = SchemaMigration(version=2, name=V2_NAME, statements=V2_STATEMENTS)

V3_NAME = "tool_execution_approval"
V3_STATEMENTS = (
    """
    CREATE TABLE tool_executions (
        tool_execution_id TEXT PRIMARY KEY,
        workspace_id TEXT NOT NULL,
        session_id TEXT NOT NULL REFERENCES sessions(session_id),
        task_run_id TEXT NOT NULL REFERENCES task_runs(task_run_id),
        turn_id TEXT NOT NULL REFERENCES turns(turn_id),
        agent_run_id TEXT NOT NULL REFERENCES agent_runs(agent_run_id),
        assistant_record_id TEXT REFERENCES conversation_records(record_id),
        call_id TEXT NOT NULL,
        ordinal INTEGER NOT NULL CHECK (ordinal >= 1),
        tool_name TEXT NOT NULL,
        state TEXT NOT NULL
            CHECK (state IN (
                'prepared', 'awaiting_approval', 'executing',
                'handler_completed', 'closed'
            )),
        disposition TEXT NOT NULL
            CHECK (disposition IN (
                'pending', 'denied', 'succeeded', 'failed',
                'cancelled', 'interrupted', 'unknown'
            )),
        row_version INTEGER NOT NULL CHECK (row_version >= 1),
        retry_of_execution_id TEXT REFERENCES tool_executions(tool_execution_id),
        approval_id TEXT,
        intent_json TEXT NOT NULL,
        intent_hash TEXT NOT NULL CHECK (length(intent_hash) = 64),
        schema_digest TEXT NOT NULL CHECK (length(schema_digest) = 64),
        permission_context_digest TEXT NOT NULL
            CHECK (length(permission_context_digest) = 64),
        result_envelope_json TEXT,
        facts_json TEXT,
        error_code TEXT,
        error_detail TEXT,
        created_at_unix INTEGER NOT NULL,
        executing_at_unix INTEGER,
        handler_completed_at_unix INTEGER,
        closed_at_unix INTEGER,
        UNIQUE (assistant_record_id, ordinal),
        CHECK (
            state != 'closed'
            OR disposition IN (
                'denied', 'succeeded', 'failed', 'cancelled',
                'interrupted', 'unknown'
            )
        ),
        CHECK (state != 'handler_completed' OR disposition != 'pending')
    )
    """,
    """
    CREATE INDEX tool_executions_session
        ON tool_executions(workspace_id, session_id)
    """,
    """
    CREATE INDEX tool_executions_turn_ordinal
        ON tool_executions(turn_id, ordinal)
    """,
    """
    CREATE INDEX tool_executions_call
        ON tool_executions(agent_run_id, call_id)
    """,
    """
    CREATE TABLE approvals (
        approval_id TEXT PRIMARY KEY,
        tool_execution_id TEXT NOT NULL UNIQUE
            REFERENCES tool_executions(tool_execution_id),
        intent_hash TEXT NOT NULL CHECK (length(intent_hash) = 64),
        tool_schema_digest TEXT NOT NULL CHECK (length(tool_schema_digest) = 64),
        permission_context_digest TEXT NOT NULL
            CHECK (length(permission_context_digest) = 64),
        requested_scope TEXT NOT NULL,
        granted_scope TEXT,
        preview_json TEXT NOT NULL,
        preview_digest TEXT NOT NULL CHECK (length(preview_digest) = 64),
        row_version INTEGER NOT NULL CHECK (row_version >= 1),
        created_at_unix INTEGER NOT NULL,
        expires_at_unix INTEGER NOT NULL,
        resolution TEXT NOT NULL
            CHECK (resolution IN ('pending', 'approved', 'denied', 'expired')),
        resolved_at_unix INTEGER,
        consumed_at_unix INTEGER,
        command_id TEXT,
        CHECK (expires_at_unix > created_at_unix),
        CHECK (consumed_at_unix IS NULL OR resolution = 'approved')
    )
    """,
)

V3 = SchemaMigration(version=3, name=V3_NAME, statements=V3_STATEMENTS)

V4_NAME = "recovery_reports"
V4_STATEMENTS = (
    """
    CREATE TABLE recovery_reports (
        report_id TEXT PRIMARY KEY,
        workspace_id TEXT NOT NULL,
        session_id TEXT NOT NULL REFERENCES sessions(session_id),
        turn_id TEXT,
        agent_run_id TEXT,
        status TEXT NOT NULL
            CHECK (status IN ('open', 'resolved', 'quarantined')),
        payload_json TEXT NOT NULL,
        payload_bytes INTEGER NOT NULL CHECK (payload_bytes >= 0),
        created_at_unix INTEGER NOT NULL,
        resolved_at_unix INTEGER
    )
    """,
    """
    CREATE UNIQUE INDEX recovery_reports_open_session
        ON recovery_reports(session_id) WHERE status = 'open'
    """,
    """
    CREATE INDEX recovery_reports_workspace
        ON recovery_reports(workspace_id, session_id)
    """,
    """
    CREATE TABLE recovery_receipts (
        session_id TEXT NOT NULL REFERENCES sessions(session_id),
        command_id TEXT NOT NULL,
        request_digest TEXT NOT NULL,
        report_id TEXT NOT NULL REFERENCES recovery_reports(report_id),
        item_id TEXT,
        resolution TEXT NOT NULL,
        PRIMARY KEY (session_id, command_id)
    )
    """,
)

V4 = SchemaMigration(version=4, name=V4_NAME, statements=V4_STATEMENTS)

V5_NAME = "task_run_lifecycle_and_outcomes"
V5_STATEMENTS = (
    """
    PRAGMA legacy_alter_table = ON
    """,
    """
    ALTER TABLE task_runs RENAME TO task_runs_v4
    """,
    """
    CREATE TABLE task_runs (
        task_run_id TEXT PRIMARY KEY,
        session_id TEXT NOT NULL REFERENCES sessions(session_id),
        workspace_id TEXT NOT NULL,
        status TEXT NOT NULL CHECK (
            status IN (
                'open', 'ready_for_acceptance', 'accepted', 'cancelled',
                'failed', 'abandoned'
            )
        ),
        row_version INTEGER NOT NULL CHECK (row_version >= 1),
        attempt INTEGER NOT NULL CHECK (attempt >= 1),
        created_at_unix INTEGER NOT NULL,
        updated_at_unix INTEGER NOT NULL,
        accepted_at_unix INTEGER,
        closed_at_unix INTEGER
    )
    """,
    """
    INSERT INTO task_runs(
        task_run_id, session_id, workspace_id, status, row_version, attempt,
        created_at_unix, updated_at_unix, accepted_at_unix, closed_at_unix
    )
    SELECT task_run_id, session_id, workspace_id, 'open', 1, 1,
           created_at_unix, created_at_unix, NULL, NULL
    FROM task_runs_v4
    """,
    """
    DROP TABLE task_runs_v4
    """,
    """
    CREATE INDEX task_runs_session ON task_runs(session_id)
    """,
    """
    CREATE INDEX task_runs_workspace_status
        ON task_runs(workspace_id, status)
    """,
    """
    CREATE TABLE task_run_transitions (
        transition_id TEXT PRIMARY KEY,
        workspace_id TEXT NOT NULL,
        session_id TEXT NOT NULL REFERENCES sessions(session_id),
        task_run_id TEXT NOT NULL REFERENCES task_runs(task_run_id),
        from_status TEXT CHECK (
            from_status IS NULL OR from_status IN (
                'open', 'ready_for_acceptance', 'accepted', 'cancelled',
                'failed', 'abandoned'
            )
        ),
        to_status TEXT NOT NULL CHECK (
            to_status IN (
                'open', 'ready_for_acceptance', 'accepted', 'cancelled',
                'failed', 'abandoned'
            )
        ),
        reason TEXT NOT NULL,
        turn_id TEXT REFERENCES turns(turn_id),
        command_id TEXT,
        attempt INTEGER NOT NULL CHECK (attempt >= 1),
        created_at_unix INTEGER NOT NULL
    )
    """,
    """
    CREATE INDEX task_run_transitions_task
        ON task_run_transitions(workspace_id, task_run_id, created_at_unix, transition_id)
    """,
    """
    CREATE TABLE task_outcomes (
        outcome_id TEXT PRIMARY KEY,
        workspace_id TEXT NOT NULL,
        session_id TEXT NOT NULL REFERENCES sessions(session_id),
        task_run_id TEXT NOT NULL REFERENCES task_runs(task_run_id),
        version INTEGER NOT NULL CHECK (version >= 1),
        trigger TEXT NOT NULL CHECK (
            trigger IN ('acceptance', 'snapshot', 'terminal_close')
        ),
        task_status TEXT NOT NULL CHECK (
            task_status IN (
                'open', 'ready_for_acceptance', 'accepted', 'cancelled',
                'failed', 'abandoned'
            )
        ),
        payload_json TEXT NOT NULL,
        payload_bytes INTEGER NOT NULL CHECK (payload_bytes >= 0),
        created_at_unix INTEGER NOT NULL,
        UNIQUE (task_run_id, version)
    )
    """,
    """
    CREATE INDEX task_outcomes_workspace_task
        ON task_outcomes(workspace_id, task_run_id, version)
    """,
    """
    CREATE TABLE task_command_receipts (
        command_id TEXT PRIMARY KEY,
        workspace_id TEXT NOT NULL,
        session_id TEXT NOT NULL REFERENCES sessions(session_id),
        task_run_id TEXT REFERENCES task_runs(task_run_id),
        operation TEXT NOT NULL,
        request_digest TEXT NOT NULL CHECK (length(request_digest) = 64),
        disposition TEXT NOT NULL CHECK (disposition IN ('accepted', 'replay', 'conflict')),
        result_task_run_id TEXT REFERENCES task_runs(task_run_id),
        outcome_id TEXT REFERENCES task_outcomes(outcome_id),
        task_status TEXT,
        row_version INTEGER,
        created_at_unix INTEGER NOT NULL
    )
    """,
    """
    CREATE INDEX task_command_receipts_session
        ON task_command_receipts(workspace_id, session_id, created_at_unix)
    """,
)

V5 = SchemaMigration(version=5, name=V5_NAME, statements=V5_STATEMENTS)

V6_NAME = "artifact_store_and_references"
V6_STATEMENTS = (
    """
    ALTER TABLE tool_executions
        ADD COLUMN artifact_refs_json TEXT NOT NULL DEFAULT '[]'
    """,
    """
    ALTER TABLE task_outcomes
        ADD COLUMN artifact_refs_json TEXT NOT NULL DEFAULT '[]'
    """,
    """
    CREATE TABLE artifacts (
        artifact_id TEXT PRIMARY KEY,
        workspace_id TEXT NOT NULL,
        session_id TEXT REFERENCES sessions(session_id),
        task_run_id TEXT REFERENCES task_runs(task_run_id),
        kind TEXT NOT NULL CHECK (
            kind IN (
                'command_output', 'patch', 'diff', 'test_report',
                'diagnostic_report', 'task_summary', 'context_summary'
            )
        ),
        sensitivity TEXT NOT NULL CHECK (sensitivity IN ('non_sensitive', 'redacted')),
        state TEXT NOT NULL CHECK (state IN ('staging', 'available', 'missing', 'corrupt')),
        retention TEXT NOT NULL CHECK (retention IN ('standard', 'pinned')),
        sha256 TEXT NOT NULL CHECK (length(sha256) = 64),
        byte_size INTEGER NOT NULL CHECK (byte_size >= 0 AND byte_size <= 67108864),
        excerpt TEXT NOT NULL DEFAULT '',
        provenance_json TEXT NOT NULL,
        row_version INTEGER NOT NULL CHECK (row_version >= 1),
        created_at_unix INTEGER NOT NULL,
        updated_at_unix INTEGER NOT NULL,
        CHECK (task_run_id IS NULL OR session_id IS NOT NULL)
    )
    """,
    """
    CREATE INDEX artifacts_workspace_scope
        ON artifacts(workspace_id, session_id, task_run_id, state)
    """,
    """
    CREATE TABLE artifact_references (
        artifact_id TEXT NOT NULL REFERENCES artifacts(artifact_id),
        workspace_id TEXT NOT NULL,
        owner_kind TEXT NOT NULL CHECK (owner_kind IN ('tool_execution', 'task_outcome')),
        owner_id TEXT NOT NULL,
        role TEXT NOT NULL CHECK (length(role) BETWEEN 1 AND 64),
        created_at_unix INTEGER NOT NULL,
        PRIMARY KEY (artifact_id, owner_kind, owner_id, role)
    )
    """,
    """
    CREATE INDEX artifact_references_owner
        ON artifact_references(workspace_id, owner_kind, owner_id)
    """,
)

V6 = SchemaMigration(version=6, name=V6_NAME, statements=V6_STATEMENTS)

V7_NAME = "context_checkpoints_and_session_lineage"
V7_STATEMENTS = (
    """
    CREATE TABLE context_checkpoints (
        checkpoint_id TEXT PRIMARY KEY,
        workspace_id TEXT NOT NULL,
        session_id TEXT NOT NULL REFERENCES sessions(session_id),
        task_run_id TEXT REFERENCES task_runs(task_run_id),
        source_agent_run_id TEXT REFERENCES agent_runs(agent_run_id),
        codec TEXT NOT NULL,
        method_version TEXT NOT NULL,
        source_start_record_id TEXT,
        source_start_position INTEGER NOT NULL CHECK (source_start_position >= 0),
        source_end_record_id TEXT NOT NULL,
        source_end_position INTEGER NOT NULL CHECK (source_end_position > source_start_position),
        retained_record_ids_json TEXT NOT NULL,
        sections_json TEXT NOT NULL,
        omitted_sections_json TEXT NOT NULL,
        artifact_refs_json TEXT NOT NULL,
        input_bytes INTEGER NOT NULL CHECK (input_bytes >= 0),
        output_bytes INTEGER NOT NULL CHECK (output_bytes >= 0),
        request_estimate_chars INTEGER NOT NULL CHECK (request_estimate_chars >= 0),
        created_at_unix INTEGER NOT NULL,
        CHECK (task_run_id IS NULL OR session_id IS NOT NULL)
    )
    """,
    """
    CREATE INDEX context_checkpoints_scope
        ON context_checkpoints(workspace_id, session_id, task_run_id, source_end_position)
    """,
    """
    ALTER TABLE sessions ADD COLUMN parent_session_id TEXT REFERENCES sessions(session_id)
    """,
    """
    ALTER TABLE sessions ADD COLUMN parent_cut_record_id TEXT
    """,
    """
    ALTER TABLE sessions ADD COLUMN parent_cut_position INTEGER
        CHECK (parent_cut_position IS NULL OR parent_cut_position >= 1)
    """,
    """
    ALTER TABLE sessions ADD COLUMN parent_checkpoint_id TEXT
        REFERENCES context_checkpoints(checkpoint_id)
    """,
    """
    ALTER TABLE sessions ADD COLUMN fork_reason TEXT
    """,
    """
    CREATE TABLE checkpoint_artifact_references (
        artifact_id TEXT NOT NULL REFERENCES artifacts(artifact_id),
        workspace_id TEXT NOT NULL,
        checkpoint_id TEXT NOT NULL REFERENCES context_checkpoints(checkpoint_id),
        role TEXT NOT NULL CHECK (length(role) BETWEEN 1 AND 64),
        created_at_unix INTEGER NOT NULL,
        PRIMARY KEY (artifact_id, checkpoint_id, role)
    )
    """,
    """
    CREATE INDEX checkpoint_artifact_references_workspace
        ON checkpoint_artifact_references(workspace_id, checkpoint_id)
    """,
)

V7 = SchemaMigration(version=7, name=V7_NAME, statements=V7_STATEMENTS)

V8_NAME = "application_events_and_command_receipts"
V8_STATEMENTS = (
    """
    CREATE TABLE application_events (
        event_id TEXT PRIMARY KEY,
        workspace_id TEXT NOT NULL,
        cursor INTEGER NOT NULL CHECK (cursor >= 1),
        schema_version INTEGER NOT NULL CHECK (schema_version >= 1),
        event_type TEXT NOT NULL CHECK (length(event_type) BETWEEN 1 AND 128),
        aggregate_kind TEXT NOT NULL CHECK (length(aggregate_kind) BETWEEN 1 AND 64),
        aggregate_id TEXT NOT NULL CHECK (length(aggregate_id) BETWEEN 1 AND 128),
        payload_json TEXT NOT NULL,
        payload_bytes INTEGER NOT NULL CHECK (payload_bytes >= 0 AND payload_bytes <= 32768),
        created_at_unix INTEGER NOT NULL,
        UNIQUE (workspace_id, cursor)
    )
    """,
    """
    CREATE INDEX application_events_workspace_cursor
        ON application_events(workspace_id, cursor)
    """,
    """
    CREATE TABLE application_command_receipts (
        command_id TEXT PRIMARY KEY,
        workspace_id TEXT NOT NULL,
        session_id TEXT,
        operation TEXT NOT NULL CHECK (length(operation) BETWEEN 1 AND 128),
        request_digest TEXT NOT NULL CHECK (length(request_digest) = 64),
        disposition TEXT NOT NULL CHECK (disposition IN ('accepted', 'replay', 'conflict')),
        result_kind TEXT,
        result_id TEXT,
        event_cursor INTEGER,
        row_version INTEGER CHECK (row_version IS NULL OR row_version >= 1),
        created_at_unix INTEGER NOT NULL
    )
    """,
    """
    CREATE INDEX application_command_receipts_workspace
        ON application_command_receipts(workspace_id, created_at_unix, command_id)
    """,
)

V8 = SchemaMigration(version=8, name=V8_NAME, statements=V8_STATEMENTS)

V9_NAME = "capability_grants_and_permission_snapshots"
V9_STATEMENTS = (
    """
    CREATE TABLE capability_grants (
        grant_id TEXT PRIMARY KEY,
        workspace_id TEXT NOT NULL,
        task_run_id TEXT NOT NULL REFERENCES task_runs(task_run_id),
        agent_run_id TEXT NOT NULL REFERENCES agent_runs(agent_run_id),
        capabilities_json TEXT NOT NULL,
        granted_by TEXT NOT NULL CHECK (granted_by = 'local_interface_command'),
        command_id TEXT NOT NULL,
        reason TEXT NOT NULL,
        preview_digest TEXT NOT NULL CHECK (length(preview_digest) = 64),
        policy_version TEXT NOT NULL,
        schema_version INTEGER NOT NULL CHECK (schema_version = 9),
        created_at_unix INTEGER NOT NULL,
        expires_at_unix INTEGER NOT NULL,
        revoked_at_unix INTEGER,
        revocation_reason TEXT,
        row_version INTEGER NOT NULL CHECK (row_version >= 1),
        CHECK (expires_at_unix > created_at_unix),
        CHECK (revoked_at_unix IS NULL OR revoked_at_unix >= created_at_unix),
        CHECK (revoked_at_unix IS NOT NULL OR revocation_reason IS NULL),
        CHECK (revoked_at_unix IS NULL OR revocation_reason IS NOT NULL)
    )
    """,
    """
    CREATE INDEX capability_grants_workspace_run
        ON capability_grants(workspace_id, agent_run_id, task_run_id, expires_at_unix)
    """,
    """
    CREATE TABLE permission_snapshots (
        permission_snapshot_id TEXT PRIMARY KEY,
        workspace_id TEXT NOT NULL,
        session_id TEXT NOT NULL REFERENCES sessions(session_id),
        task_run_id TEXT NOT NULL REFERENCES task_runs(task_run_id),
        turn_id TEXT NOT NULL REFERENCES turns(turn_id),
        agent_run_id TEXT NOT NULL UNIQUE REFERENCES agent_runs(agent_run_id),
        access_scope TEXT NOT NULL CHECK (access_scope IN ('workspace', 'full_access')),
        approval_mode TEXT NOT NULL CHECK (approval_mode IN ('manual', 'auto_safe', 'auto')),
        process_isolation TEXT NOT NULL CHECK (
            process_isolation IN ('host', 'native_sandbox')
        ),
        workspace_root_digest TEXT NOT NULL CHECK (length(workspace_root_digest) = 64),
        workspace_read_only INTEGER NOT NULL CHECK (workspace_read_only IN (0, 1)),
        tool_schema_digest TEXT NOT NULL CHECK (length(tool_schema_digest) = 64),
        run_policy_digest TEXT NOT NULL CHECK (length(run_policy_digest) = 64),
        permission_profile_digest TEXT NOT NULL CHECK (length(permission_profile_digest) = 64),
        policy_version TEXT NOT NULL,
        schema_version INTEGER NOT NULL CHECK (schema_version = 9),
        source_revisions_json TEXT NOT NULL,
        grant_id TEXT REFERENCES capability_grants(grant_id),
        grant_digest TEXT CHECK (grant_digest IS NULL OR length(grant_digest) = 64),
        granted_capabilities_json TEXT NOT NULL,
        capability_isolations_json TEXT NOT NULL,
        created_at_unix INTEGER NOT NULL
    )
    """,
    """
    CREATE INDEX permission_snapshots_workspace_run
        ON permission_snapshots(workspace_id, agent_run_id, created_at_unix)
    """,
    """
    ALTER TABLE agent_runs
        ADD COLUMN permission_snapshot_id TEXT
        REFERENCES permission_snapshots(permission_snapshot_id)
    """,
    """
    CREATE UNIQUE INDEX agent_runs_permission_snapshot
        ON agent_runs(permission_snapshot_id)
        WHERE permission_snapshot_id IS NOT NULL
    """,
    """
    ALTER TABLE tool_executions ADD COLUMN permission_snapshot_id TEXT
        REFERENCES permission_snapshots(permission_snapshot_id)
    """,
    """
    ALTER TABLE tool_executions ADD COLUMN grant_id TEXT
        REFERENCES capability_grants(grant_id)
    """,
    """
    ALTER TABLE tool_executions ADD COLUMN isolation TEXT CHECK (
        isolation IS NULL OR isolation IN ('workspace', 'native_sandbox', 'unconfined_host')
    )
    """,
    """
    ALTER TABLE approvals ADD COLUMN permission_snapshot_id TEXT
        REFERENCES permission_snapshots(permission_snapshot_id)
    """,
    """
    ALTER TABLE approvals ADD COLUMN grant_id TEXT
        REFERENCES capability_grants(grant_id)
    """,
    """
    ALTER TABLE approvals ADD COLUMN isolation TEXT CHECK (
        isolation IS NULL OR isolation IN ('workspace', 'native_sandbox', 'unconfined_host')
    )
    """,
    """
    ALTER TABLE approvals ADD COLUMN revoked_at_unix INTEGER
    """,
    """
    ALTER TABLE approvals ADD COLUMN revocation_reason TEXT
    """,
    """
    ALTER TABLE tool_executions ADD COLUMN cancel_requested_at_unix INTEGER
    """,
    """
    ALTER TABLE tool_executions ADD COLUMN cancel_request_reason TEXT
    """,
    """
    CREATE INDEX tool_executions_permission_snapshot
        ON tool_executions(workspace_id, permission_snapshot_id, grant_id)
    """,
    """
    CREATE INDEX approvals_permission_snapshot
        ON approvals(permission_snapshot_id, grant_id)
    """,
)

V9 = SchemaMigration(version=9, name=V9_NAME, statements=V9_STATEMENTS)

V10_NAME = "learning_foundation"
V10_STATEMENTS = (
    """
    CREATE TABLE learning_policies (
        workspace_id TEXT PRIMARY KEY,
        mode TEXT NOT NULL CHECK (mode IN ('off', 'review_only')),
        candidate_ttl_days INTEGER NOT NULL CHECK (candidate_ttl_days BETWEEN 1 AND 365),
        max_candidates_per_review INTEGER NOT NULL CHECK (
            max_candidates_per_review BETWEEN 1 AND 3
        ),
        max_evidence_per_review INTEGER NOT NULL CHECK (
            max_evidence_per_review BETWEEN 1 AND 32
        ),
        row_version INTEGER NOT NULL CHECK (row_version >= 1),
        created_at_unix INTEGER NOT NULL,
        updated_at_unix INTEGER NOT NULL,
        CHECK (updated_at_unix >= created_at_unix)
    )
    """,
    """
    CREATE TABLE learning_reviews (
        review_id TEXT PRIMARY KEY,
        workspace_id TEXT NOT NULL,
        task_run_id TEXT NOT NULL REFERENCES task_runs(task_run_id),
        task_outcome_id TEXT NOT NULL REFERENCES task_outcomes(outcome_id),
        review_version INTEGER NOT NULL CHECK (review_version >= 1),
        trigger TEXT NOT NULL CHECK (trigger IN ('task_accepted', 'explicit_request')),
        status TEXT NOT NULL CHECK (
            status IN ('pending', 'running', 'completed', 'failed', 'superseded')
        ),
        policy_snapshot_json TEXT NOT NULL,
        policy_snapshot_bytes INTEGER NOT NULL CHECK (
            policy_snapshot_bytes BETWEEN 1 AND 8192
        ),
        policy_digest TEXT NOT NULL CHECK (length(policy_digest) = 64),
        reviewer_provider_id TEXT,
        reviewer_model_id TEXT,
        reviewer_prompt_version TEXT NOT NULL,
        reviewer_schema_version TEXT NOT NULL,
        supersedes_review_id TEXT REFERENCES learning_reviews(review_id),
        lease_id TEXT UNIQUE,
        lease_expires_at_unix INTEGER,
        attempt_count INTEGER NOT NULL CHECK (attempt_count BETWEEN 0 AND 32),
        row_version INTEGER NOT NULL CHECK (row_version >= 1),
        created_at_unix INTEGER NOT NULL,
        started_at_unix INTEGER,
        completed_at_unix INTEGER,
        failure_code TEXT CHECK (
            failure_code IS NULL OR failure_code IN (
                'provider_unavailable', 'timeout', 'invalid_output',
                'safety_rejected', 'lease_lost', 'cancelled', 'internal'
            )
        ),
        UNIQUE (workspace_id, task_outcome_id, review_version),
        CHECK (status != 'running' OR (lease_id IS NOT NULL AND lease_expires_at_unix IS NOT NULL)),
        CHECK (status != 'failed' OR failure_code IS NOT NULL),
        CHECK (status = 'failed' OR failure_code IS NULL),
        CHECK (completed_at_unix IS NULL OR completed_at_unix >= created_at_unix)
    )
    """,
    """
    CREATE INDEX learning_reviews_workspace_status
        ON learning_reviews(workspace_id, status, created_at_unix, review_id)
    """,
    """
    CREATE INDEX learning_reviews_outcome
        ON learning_reviews(workspace_id, task_outcome_id, review_version)
    """,
    """
    CREATE INDEX learning_reviews_lease
        ON learning_reviews(workspace_id, status, lease_expires_at_unix)
    """,
    """
    CREATE TABLE learning_evidence (
        evidence_id TEXT PRIMARY KEY,
        workspace_id TEXT NOT NULL,
        origin_review_id TEXT NOT NULL REFERENCES learning_reviews(review_id),
        task_run_id TEXT NOT NULL REFERENCES task_runs(task_run_id),
        source_kind TEXT NOT NULL CHECK (source_kind IN (
            'user_turn', 'task_transition', 'task_outcome', 'artifact',
            'tool_execution', 'configuration', 'candidate_decision', 'system'
        )),
        source_id TEXT NOT NULL,
        source_pointer TEXT NOT NULL DEFAULT '',
        actor TEXT NOT NULL CHECK (actor IN ('user', 'assistant', 'tool', 'system')),
        authority TEXT NOT NULL CHECK (authority IN (
            'user_explicit_persistent', 'user_correction', 'user_acceptance',
            'configuration_change', 'deterministic_task_fact',
            'deterministic_artifact_fact', 'behavioral_signal',
            'untrusted_external_content'
        )),
        explicitness TEXT NOT NULL CHECK (
            explicitness IN ('explicit', 'behavioral', 'inferred')
        ),
        polarity TEXT NOT NULL CHECK (polarity IN ('positive', 'negative', 'neutral')),
        scope_hint TEXT CHECK (
            scope_hint IS NULL OR scope_hint IN ('global', 'workspace', 'session', 'task')
        ),
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
        UNIQUE (workspace_id, source_kind, source_id, source_pointer),
        CHECK ((safety_rejection_code IS NULL) OR excerpt_redacted IS NULL),
        CHECK (excerpt_redacted IS NULL OR length(excerpt_redacted) <= 512)
    )
    """,
    """
    CREATE INDEX learning_evidence_workspace_source
        ON learning_evidence(workspace_id, source_kind, source_id, created_at_unix)
    """,
    """
    CREATE INDEX learning_evidence_workspace_authority
        ON learning_evidence(workspace_id, authority, created_at_unix)
    """,
    """
    CREATE TABLE learning_review_evidence (
        workspace_id TEXT NOT NULL,
        review_id TEXT NOT NULL REFERENCES learning_reviews(review_id),
        evidence_id TEXT NOT NULL REFERENCES learning_evidence(evidence_id),
        PRIMARY KEY (review_id, evidence_id)
    )
    """,
    """
    CREATE INDEX learning_review_evidence_workspace
        ON learning_review_evidence(workspace_id, review_id, evidence_id)
    """,
    """
    CREATE TABLE learning_candidates (
        candidate_id TEXT PRIMARY KEY,
        workspace_id TEXT NOT NULL,
        origin_review_id TEXT NOT NULL REFERENCES learning_reviews(review_id),
        candidate_type TEXT NOT NULL CHECK (candidate_type IN (
            'preference', 'profile', 'project_knowledge', 'skill_candidate',
            'workflow_feedback', 'orchestration_policy_candidate'
        )),
        operation TEXT NOT NULL CHECK (operation IN ('set', 'append', 'replace', 'remove')),
        semantic_key TEXT NOT NULL,
        proposed_scope TEXT NOT NULL CHECK (
            proposed_scope IN ('global', 'workspace', 'session', 'task')
        ),
        proposed_payload_json TEXT NOT NULL,
        proposed_payload_bytes INTEGER NOT NULL CHECK (
            proposed_payload_bytes BETWEEN 1 AND 8192
        ),
        fingerprint TEXT NOT NULL CHECK (length(fingerprint) = 64),
        status TEXT NOT NULL CHECK (status IN (
            'proposed', 'promoting', 'accepted', 'edited_and_accepted',
            'rejected', 'expired', 'superseded'
        )),
        evidence_ids_json TEXT NOT NULL,
        confidence_band TEXT NOT NULL CHECK (confidence_band IN ('low', 'medium', 'high')),
        confidence_basis_json TEXT NOT NULL,
        sensitivity TEXT NOT NULL CHECK (
            sensitivity IN ('normal', 'personal', 'sensitive', 'prohibited')
        ),
        expected_target_revision INTEGER CHECK (expected_target_revision IS NULL OR expected_target_revision >= 1),
        duplicate_of_id TEXT REFERENCES learning_candidates(candidate_id),
        supersedes_id TEXT REFERENCES learning_candidates(candidate_id),
        conflict_refs_json TEXT NOT NULL,
        expires_at_unix INTEGER NOT NULL,
        row_version INTEGER NOT NULL CHECK (row_version >= 1),
        created_at_unix INTEGER NOT NULL,
        resolved_at_unix INTEGER,
        resolved_by TEXT CHECK (resolved_by IS NULL OR resolved_by IN ('user', 'policy')),
        rejection_reason TEXT,
        CHECK (resolved_at_unix IS NULL OR resolved_at_unix >= created_at_unix),
        CHECK (status IN ('proposed', 'promoting') OR resolved_at_unix IS NOT NULL),
        CHECK (status IN ('proposed', 'promoting') OR resolved_by IS NOT NULL)
    )
    """,
    """
    CREATE INDEX learning_candidates_workspace_status
        ON learning_candidates(workspace_id, status, expires_at_unix, candidate_id)
    """,
    """
    CREATE INDEX learning_candidates_workspace_review
        ON learning_candidates(workspace_id, origin_review_id, created_at_unix, candidate_id)
    """,
    """
    CREATE UNIQUE INDEX learning_candidates_active_fingerprint
        ON learning_candidates(workspace_id, fingerprint)
        WHERE status IN ('proposed', 'promoting')
    """,
    """
    CREATE TABLE learning_candidate_evidence (
        workspace_id TEXT NOT NULL,
        candidate_id TEXT NOT NULL REFERENCES learning_candidates(candidate_id),
        evidence_id TEXT NOT NULL REFERENCES learning_evidence(evidence_id),
        PRIMARY KEY (candidate_id, evidence_id)
    )
    """,
    """
    CREATE INDEX learning_candidate_evidence_workspace
        ON learning_candidate_evidence(workspace_id, candidate_id, evidence_id)
    """,
    """
    CREATE TABLE learning_suppressions (
        suppression_id TEXT PRIMARY KEY,
        workspace_id TEXT NOT NULL,
        candidate_type TEXT NOT NULL CHECK (candidate_type IN (
            'preference', 'profile', 'project_knowledge', 'skill_candidate',
            'workflow_feedback', 'orchestration_policy_candidate'
        )),
        scope TEXT NOT NULL CHECK (scope IN ('global', 'workspace', 'session', 'task')),
        semantic_key TEXT,
        fingerprint TEXT CHECK (fingerprint IS NULL OR length(fingerprint) = 64),
        source_candidate_id TEXT REFERENCES learning_candidates(candidate_id),
        reason TEXT NOT NULL CHECK (length(reason) BETWEEN 1 AND 256),
        status TEXT NOT NULL CHECK (status IN ('active', 'disabled', 'expired')),
        expires_at_unix INTEGER,
        row_version INTEGER NOT NULL CHECK (row_version >= 1),
        created_at_unix INTEGER NOT NULL,
        updated_at_unix INTEGER NOT NULL,
        CHECK (semantic_key IS NOT NULL OR fingerprint IS NOT NULL),
        CHECK (updated_at_unix >= created_at_unix)
    )
    """,
    """
    CREATE INDEX learning_suppressions_lookup
        ON learning_suppressions(
            workspace_id, candidate_type, scope, semantic_key, fingerprint, status
        )
    """,
    """
    CREATE UNIQUE INDEX learning_suppressions_active_identity
        ON learning_suppressions(
            workspace_id, candidate_type, scope,
            COALESCE(semantic_key, ''), COALESCE(fingerprint, '')
        )
        WHERE status = 'active'
    """,
    """
    CREATE TRIGGER learning_reviews_workspace_guard_insert
    BEFORE INSERT ON learning_reviews
    BEGIN
        SELECT CASE WHEN EXISTS (
            SELECT 1 FROM task_runs t
            WHERE t.task_run_id = NEW.task_run_id AND t.workspace_id != NEW.workspace_id
        ) THEN RAISE(ABORT, 'learning workspace mismatch') END;
        SELECT CASE WHEN EXISTS (
            SELECT 1 FROM task_outcomes o
            WHERE o.outcome_id = NEW.task_outcome_id AND o.workspace_id != NEW.workspace_id
        ) THEN RAISE(ABORT, 'learning workspace mismatch') END;
    END
    """,
    """
    CREATE TRIGGER learning_reviews_workspace_guard_update
    BEFORE UPDATE OF workspace_id, task_run_id, task_outcome_id ON learning_reviews
    BEGIN
        SELECT CASE WHEN EXISTS (
            SELECT 1 FROM task_runs t
            WHERE t.task_run_id = NEW.task_run_id AND t.workspace_id != NEW.workspace_id
        ) THEN RAISE(ABORT, 'learning workspace mismatch') END;
        SELECT CASE WHEN EXISTS (
            SELECT 1 FROM task_outcomes o
            WHERE o.outcome_id = NEW.task_outcome_id AND o.workspace_id != NEW.workspace_id
        ) THEN RAISE(ABORT, 'learning workspace mismatch') END;
    END
    """,
    """
    CREATE TRIGGER learning_evidence_workspace_guard_insert
    BEFORE INSERT ON learning_evidence
    BEGIN
        SELECT CASE WHEN EXISTS (
            SELECT 1 FROM learning_reviews r
            WHERE r.review_id = NEW.origin_review_id AND r.workspace_id != NEW.workspace_id
        ) THEN RAISE(ABORT, 'learning workspace mismatch') END;
        SELECT CASE WHEN EXISTS (
            SELECT 1 FROM task_runs t
            WHERE t.task_run_id = NEW.task_run_id AND t.workspace_id != NEW.workspace_id
        ) THEN RAISE(ABORT, 'learning workspace mismatch') END;
    END
    """,
    """
    CREATE TRIGGER learning_review_evidence_workspace_guard_insert
    BEFORE INSERT ON learning_review_evidence
    BEGIN
        SELECT CASE WHEN EXISTS (
            SELECT 1 FROM learning_reviews r
            JOIN learning_evidence e ON e.evidence_id = NEW.evidence_id
            WHERE r.review_id = NEW.review_id
              AND (r.workspace_id != NEW.workspace_id OR e.workspace_id != NEW.workspace_id)
        ) THEN RAISE(ABORT, 'learning workspace mismatch') END;
    END
    """,
    """
    CREATE TRIGGER learning_candidate_workspace_guard_insert
    BEFORE INSERT ON learning_candidates
    BEGIN
        SELECT CASE WHEN EXISTS (
            SELECT 1 FROM learning_reviews r
            WHERE r.review_id = NEW.origin_review_id AND r.workspace_id != NEW.workspace_id
        ) THEN RAISE(ABORT, 'learning workspace mismatch') END;
    END
    """,
    """
    CREATE TRIGGER learning_candidate_evidence_workspace_guard_insert
    BEFORE INSERT ON learning_candidate_evidence
    BEGIN
        SELECT CASE WHEN EXISTS (
            SELECT 1 FROM learning_candidates c
            JOIN learning_evidence e ON e.evidence_id = NEW.evidence_id
            WHERE c.candidate_id = NEW.candidate_id
              AND (c.workspace_id != NEW.workspace_id OR e.workspace_id != NEW.workspace_id)
        ) THEN RAISE(ABORT, 'learning workspace mismatch') END;
    END
    """,
)

V10 = SchemaMigration(version=10, name=V10_NAME, statements=V10_STATEMENTS)

V11_NAME = "learning_inbox_project_knowledge"
V11_STATEMENTS = (
    """
    CREATE TABLE learning_candidate_decisions (
        decision_id TEXT PRIMARY KEY,
        workspace_id TEXT NOT NULL,
        candidate_id TEXT NOT NULL REFERENCES learning_candidates(candidate_id),
        kind TEXT NOT NULL CHECK (kind IN (
            'accept', 'edit_and_accept', 'reject', 'reject_and_suppress',
            'expire', 'supersede'
        )),
        actor TEXT NOT NULL CHECK (actor IN ('user', 'policy')),
        original_proposal_digest TEXT NOT NULL CHECK (length(original_proposal_digest) = 64),
        final_proposal_json TEXT,
        final_proposal_bytes INTEGER CHECK (
            final_proposal_bytes IS NULL OR final_proposal_bytes BETWEEN 1 AND 8192
        ),
        scope TEXT NOT NULL CHECK (scope IN ('global', 'workspace', 'session', 'task')),
        conflict_resolution TEXT NOT NULL CHECK (conflict_resolution IN (
            'none', 'confirm', 'replace', 'merge', 're_enable', 'resolve_dispute'
        )),
        command_id TEXT NOT NULL,
        created_at_unix INTEGER NOT NULL,
        UNIQUE (workspace_id, command_id),
        CHECK ((final_proposal_json IS NULL) = (final_proposal_bytes IS NULL)),
        CHECK ((kind = 'edit_and_accept') = (final_proposal_json IS NOT NULL)),
        CHECK (final_proposal_json IS NULL OR length(final_proposal_json) BETWEEN 2 AND 8192)
    )
    """,
    """
    CREATE INDEX learning_candidate_decisions_candidate
        ON learning_candidate_decisions(workspace_id, candidate_id, created_at_unix, decision_id)
    """,
    """
    CREATE TABLE project_knowledge_heads (
        knowledge_id TEXT PRIMARY KEY,
        workspace_id TEXT NOT NULL,
        semantic_key TEXT NOT NULL CHECK (length(semantic_key) BETWEEN 1 AND 128),
        category TEXT NOT NULL CHECK (category IN (
            'architecture', 'convention', 'decision', 'environment', 'domain', 'other'
        )),
        status TEXT NOT NULL CHECK (
            status IN ('active', 'disabled', 'disputed', 'deleted')
        ),
        current_revision_id TEXT REFERENCES project_knowledge_revisions(knowledge_revision_id),
        row_version INTEGER NOT NULL CHECK (row_version >= 1),
        created_at_unix INTEGER NOT NULL,
        updated_at_unix INTEGER NOT NULL,
        UNIQUE (workspace_id, semantic_key),
        CHECK (updated_at_unix >= created_at_unix)
    )
    """,
    """
    CREATE INDEX project_knowledge_heads_workspace_status
        ON project_knowledge_heads(workspace_id, status, category, semantic_key, knowledge_id)
    """,
    """
    CREATE TABLE project_knowledge_revisions (
        knowledge_revision_id TEXT PRIMARY KEY,
        knowledge_id TEXT NOT NULL REFERENCES project_knowledge_heads(knowledge_id),
        workspace_id TEXT NOT NULL,
        revision INTEGER NOT NULL CHECK (revision >= 1),
        statement TEXT NOT NULL CHECK (length(statement) BETWEEN 1 AND 4096),
        statement_digest TEXT NOT NULL CHECK (length(statement_digest) = 64),
        source_candidate_id TEXT REFERENCES learning_candidates(candidate_id),
        source_decision_id TEXT REFERENCES learning_candidate_decisions(decision_id),
        supersedes_revision_id TEXT REFERENCES project_knowledge_revisions(knowledge_revision_id),
        sensitivity TEXT NOT NULL CHECK (
            sensitivity IN ('normal', 'personal', 'sensitive', 'prohibited')
        ),
        valid_from_unix INTEGER,
        valid_until_unix INTEGER,
        created_at_unix INTEGER NOT NULL,
        last_confirmed_at_unix INTEGER NOT NULL,
        UNIQUE (knowledge_id, revision),
        CHECK (source_candidate_id IS NOT NULL OR source_decision_id IS NOT NULL),
        CHECK (
            valid_until_unix IS NULL OR valid_from_unix IS NULL
            OR valid_until_unix > valid_from_unix
        ),
        CHECK (last_confirmed_at_unix >= created_at_unix)
    )
    """,
    """
    CREATE INDEX project_knowledge_revisions_history
        ON project_knowledge_revisions(workspace_id, knowledge_id, revision, created_at_unix)
    """,
    """
    CREATE INDEX project_knowledge_revisions_source
        ON project_knowledge_revisions(workspace_id, source_candidate_id, source_decision_id)
    """,
    """
    CREATE TABLE project_knowledge_evidence (
        workspace_id TEXT NOT NULL,
        knowledge_revision_id TEXT NOT NULL
            REFERENCES project_knowledge_revisions(knowledge_revision_id),
        evidence_id TEXT NOT NULL REFERENCES learning_evidence(evidence_id),
        PRIMARY KEY (knowledge_revision_id, evidence_id)
    )
    """,
    """
    CREATE INDEX project_knowledge_evidence_workspace
        ON project_knowledge_evidence(workspace_id, knowledge_revision_id, evidence_id)
    """,
    """
    CREATE TABLE memory_workspace_state (
        workspace_id TEXT PRIMARY KEY,
        memory_revision INTEGER NOT NULL DEFAULT 0 CHECK (memory_revision >= 0),
        row_version INTEGER NOT NULL DEFAULT 1 CHECK (row_version >= 1),
        updated_at_unix INTEGER NOT NULL
    )
    """,
    """
    CREATE TABLE promotion_operations (
        operation_id TEXT PRIMARY KEY,
        command_id TEXT NOT NULL,
        request_digest TEXT NOT NULL CHECK (length(request_digest) = 64),
        workspace_id TEXT NOT NULL,
        candidate_id TEXT NOT NULL REFERENCES learning_candidates(candidate_id),
        candidate_row_version INTEGER NOT NULL CHECK (candidate_row_version >= 1),
        target TEXT NOT NULL CHECK (length(target) BETWEEN 1 AND 64),
        scope TEXT NOT NULL CHECK (scope IN ('global', 'workspace', 'session', 'task')),
        path TEXT NOT NULL CHECK (length(path) BETWEEN 1 AND 256),
        prepared_change_json TEXT NOT NULL CHECK (length(prepared_change_json) BETWEEN 2 AND 8192),
        prepared_change_digest TEXT NOT NULL CHECK (length(prepared_change_digest) = 64),
        state TEXT NOT NULL CHECK (
            state IN ('prepared', 'finalized', 'aborted', 'needs_resolution')
        ),
        before_revision INTEGER CHECK (before_revision IS NULL OR before_revision >= 0),
        before_digest TEXT CHECK (before_digest IS NULL OR length(before_digest) = 64),
        after_digest TEXT CHECK (after_digest IS NULL OR length(after_digest) = 64),
        applied_revision INTEGER CHECK (applied_revision IS NULL OR applied_revision >= 1),
        row_version INTEGER NOT NULL CHECK (row_version >= 1),
        failure_code TEXT CHECK (
            failure_code IS NULL OR failure_code IN (
                'stale', 'conflict', 'validation', 'filesystem', 'yaml', 'needs_recovery',
                'internal'
            )
        ),
        created_at_unix INTEGER NOT NULL,
        updated_at_unix INTEGER NOT NULL,
        prepared_at_unix INTEGER,
        finalized_at_unix INTEGER,
        UNIQUE (workspace_id, command_id),
        CHECK (updated_at_unix >= created_at_unix),
        CHECK (state != 'finalized' OR applied_revision IS NOT NULL),
        CHECK (state = 'finalized' OR finalized_at_unix IS NULL)
    )
    """,
    """
    CREATE TABLE configuration_activations (
        activation_id TEXT PRIMARY KEY,
        workspace_id TEXT NOT NULL,
        candidate_id TEXT NOT NULL REFERENCES learning_candidates(candidate_id),
        decision_id TEXT NOT NULL REFERENCES learning_candidate_decisions(decision_id),
        operation_id TEXT NOT NULL REFERENCES promotion_operations(operation_id),
        target TEXT NOT NULL CHECK (length(target) BETWEEN 1 AND 64),
        scope TEXT NOT NULL CHECK (scope IN ('global', 'workspace', 'session', 'task')),
        path TEXT NOT NULL CHECK (length(path) BETWEEN 1 AND 256),
        operation TEXT NOT NULL CHECK (operation IN ('set', 'append', 'replace', 'remove')),
        applied_revision INTEGER NOT NULL CHECK (applied_revision >= 1),
        before_digest TEXT CHECK (before_digest IS NULL OR length(before_digest) = 64),
        after_digest TEXT NOT NULL CHECK (length(after_digest) = 64),
        value_digest TEXT NOT NULL CHECK (length(value_digest) = 64),
        inverse_command_json TEXT NOT NULL CHECK (
            length(inverse_command_json) BETWEEN 2 AND 8192
        ),
        inverse_command_digest TEXT NOT NULL CHECK (length(inverse_command_digest) = 64),
        supersedes_activation_id TEXT REFERENCES configuration_activations(activation_id),
        reverses_activation_id TEXT REFERENCES configuration_activations(activation_id),
        status TEXT NOT NULL CHECK (status IN ('active', 'reversed', 'superseded')),
        created_at_unix INTEGER NOT NULL,
        updated_at_unix INTEGER NOT NULL,
        CHECK (updated_at_unix >= created_at_unix)
    )
    """,
    """
    CREATE INDEX promotion_operations_workspace_state
        ON promotion_operations(workspace_id, state, updated_at_unix, operation_id)
    """,
    """
    CREATE INDEX configuration_activations_workspace_target
        ON configuration_activations(workspace_id, target, path, status, applied_revision)
    """,
    """
    CREATE TRIGGER learning_candidate_decisions_workspace_guard_insert
    BEFORE INSERT ON learning_candidate_decisions
    BEGIN
        SELECT CASE WHEN EXISTS (
            SELECT 1 FROM learning_candidates c
            WHERE c.candidate_id = NEW.candidate_id AND c.workspace_id != NEW.workspace_id
        ) THEN RAISE(ABORT, 'learning workspace mismatch') END;
    END
    """,
    """
    CREATE TRIGGER project_knowledge_heads_workspace_guard_insert
    BEFORE INSERT ON project_knowledge_heads
    BEGIN
        SELECT CASE WHEN EXISTS (
            SELECT 1 FROM project_knowledge_revisions r
            WHERE r.knowledge_revision_id = NEW.current_revision_id
              AND (r.workspace_id != NEW.workspace_id OR r.knowledge_id != NEW.knowledge_id)
        ) THEN RAISE(ABORT, 'memory workspace mismatch') END;
    END
    """,
    """
    CREATE TRIGGER project_knowledge_heads_workspace_guard_update
    BEFORE UPDATE OF workspace_id, knowledge_id, current_revision_id ON project_knowledge_heads
    BEGIN
        SELECT CASE WHEN EXISTS (
            SELECT 1 FROM project_knowledge_revisions r
            WHERE r.knowledge_revision_id = NEW.current_revision_id
              AND (r.workspace_id != NEW.workspace_id OR r.knowledge_id != NEW.knowledge_id)
        ) THEN RAISE(ABORT, 'memory workspace mismatch') END;
    END
    """,
    """
    CREATE TRIGGER project_knowledge_revisions_workspace_guard_insert
    BEFORE INSERT ON project_knowledge_revisions
    BEGIN
        SELECT CASE WHEN EXISTS (
            SELECT 1 FROM project_knowledge_heads h
            WHERE h.knowledge_id = NEW.knowledge_id
              AND h.workspace_id != NEW.workspace_id
        ) THEN RAISE(ABORT, 'memory workspace mismatch') END;
        SELECT CASE WHEN EXISTS (
            SELECT 1 FROM learning_candidates c
            WHERE c.candidate_id = NEW.source_candidate_id
              AND c.workspace_id != NEW.workspace_id
        ) THEN RAISE(ABORT, 'memory workspace mismatch') END;
        SELECT CASE WHEN EXISTS (
            SELECT 1 FROM learning_candidate_decisions d
            WHERE d.decision_id = NEW.source_decision_id
              AND d.workspace_id != NEW.workspace_id
        ) THEN RAISE(ABORT, 'memory workspace mismatch') END;
        SELECT CASE WHEN EXISTS (
            SELECT 1 FROM learning_candidate_decisions d
            WHERE d.decision_id = NEW.source_decision_id
              AND NEW.source_candidate_id IS NOT NULL
              AND d.candidate_id != NEW.source_candidate_id
        ) THEN RAISE(ABORT, 'memory provenance mismatch') END;
        SELECT CASE WHEN EXISTS (
            SELECT 1 FROM project_knowledge_revisions r
            WHERE r.knowledge_revision_id = NEW.supersedes_revision_id
              AND (r.workspace_id != NEW.workspace_id OR r.knowledge_id != NEW.knowledge_id)
        ) THEN RAISE(ABORT, 'memory revision mismatch') END;
    END
    """,
    """
    CREATE TRIGGER project_knowledge_evidence_workspace_guard_insert
    BEFORE INSERT ON project_knowledge_evidence
    BEGIN
        SELECT CASE WHEN EXISTS (
            SELECT 1
            FROM project_knowledge_revisions r
            JOIN learning_evidence e ON e.evidence_id = NEW.evidence_id
            WHERE r.knowledge_revision_id = NEW.knowledge_revision_id
              AND (r.workspace_id != NEW.workspace_id OR e.workspace_id != NEW.workspace_id)
        ) THEN RAISE(ABORT, 'memory workspace mismatch') END;
    END
    """,
    """
    CREATE TRIGGER project_knowledge_revisions_immutable_update
    BEFORE UPDATE ON project_knowledge_revisions
    WHEN NEW.knowledge_revision_id IS NOT OLD.knowledge_revision_id
      OR NEW.knowledge_id IS NOT OLD.knowledge_id
      OR NEW.workspace_id IS NOT OLD.workspace_id
      OR NEW.revision IS NOT OLD.revision
      OR NEW.statement IS NOT OLD.statement
      OR NEW.statement_digest IS NOT OLD.statement_digest
      OR NEW.source_candidate_id IS NOT OLD.source_candidate_id
      OR NEW.source_decision_id IS NOT OLD.source_decision_id
      OR NEW.supersedes_revision_id IS NOT OLD.supersedes_revision_id
      OR NEW.sensitivity IS NOT OLD.sensitivity
      OR NEW.valid_from_unix IS NOT OLD.valid_from_unix
      OR NEW.valid_until_unix IS NOT OLD.valid_until_unix
      OR NEW.created_at_unix IS NOT OLD.created_at_unix
      OR NEW.last_confirmed_at_unix < OLD.last_confirmed_at_unix
    BEGIN
        SELECT RAISE(ABORT, 'project knowledge revision is immutable');
    END
    """,
)

V11 = SchemaMigration(version=11, name=V11_NAME, statements=V11_STATEMENTS)

V12_NAME = "memory_selection_and_terms"
V12_STATEMENTS = (
    """
    CREATE TABLE memory_selections (
        selection_id TEXT PRIMARY KEY,
        workspace_id TEXT NOT NULL,
        query_digest TEXT NOT NULL CHECK (length(query_digest) = 64),
        source_memory_revision INTEGER NOT NULL CHECK (source_memory_revision >= 0),
        item_count INTEGER NOT NULL CHECK (item_count BETWEEN 0 AND 12),
        omitted_count INTEGER NOT NULL CHECK (omitted_count >= 0),
        rendered_chars INTEGER NOT NULL CHECK (rendered_chars BETWEEN 0 AND 6144),
        selection_digest TEXT NOT NULL CHECK (length(selection_digest) = 64),
        created_at_unix INTEGER NOT NULL,
        UNIQUE (workspace_id, selection_id)
    )
    """,
    """
    CREATE INDEX memory_selections_workspace_created
        ON memory_selections(workspace_id, created_at_unix, selection_id)
    """,
    """
    CREATE TABLE memory_selection_items (
        selection_id TEXT NOT NULL REFERENCES memory_selections(selection_id),
        workspace_id TEXT NOT NULL,
        ordinal INTEGER NOT NULL CHECK (ordinal >= 1),
        record_kind TEXT NOT NULL CHECK (record_kind = 'project_knowledge'),
        record_id TEXT NOT NULL REFERENCES project_knowledge_heads(knowledge_id),
        record_revision_id TEXT NOT NULL
            REFERENCES project_knowledge_revisions(knowledge_revision_id),
        revision INTEGER NOT NULL CHECK (revision >= 1),
        reason_json TEXT NOT NULL CHECK (length(reason_json) BETWEEN 2 AND 2048),
        reason_bytes INTEGER NOT NULL CHECK (reason_bytes BETWEEN 2 AND 2048),
        estimated_chars INTEGER NOT NULL CHECK (estimated_chars >= 0),
        rendered_content_digest TEXT NOT NULL CHECK (length(rendered_content_digest) = 64),
        PRIMARY KEY (selection_id, ordinal),
        UNIQUE (selection_id, record_revision_id),
        CHECK (reason_bytes = length(reason_json))
    )
    """,
    """
    CREATE INDEX memory_selection_items_workspace_revision
        ON memory_selection_items(workspace_id, record_revision_id, selection_id, ordinal)
    """,
    """
    CREATE TABLE memory_search_terms (
        workspace_id TEXT NOT NULL,
        knowledge_revision_id TEXT NOT NULL
            REFERENCES project_knowledge_revisions(knowledge_revision_id),
        token_kind TEXT NOT NULL CHECK (
            token_kind IN ('word', 'identifier', 'cjk_bigram', 'number', 'path')
        ),
        token TEXT NOT NULL CHECK (length(token) BETWEEN 1 AND 128),
        weight_band TEXT NOT NULL CHECK (weight_band IN ('high', 'medium', 'low')),
        PRIMARY KEY (workspace_id, knowledge_revision_id, token_kind, token)
    )
    """,
    """
    CREATE INDEX memory_search_terms_workspace_token
        ON memory_search_terms(workspace_id, token, token_kind, knowledge_revision_id)
    """,
    """
    CREATE TRIGGER memory_selection_items_workspace_guard_insert
    BEFORE INSERT ON memory_selection_items
    BEGIN
        SELECT CASE WHEN EXISTS (
            SELECT 1
            FROM memory_selections s
            JOIN project_knowledge_heads h ON h.knowledge_id = NEW.record_id
            JOIN project_knowledge_revisions r ON r.knowledge_revision_id = NEW.record_revision_id
            WHERE s.selection_id = NEW.selection_id
              AND (s.workspace_id != NEW.workspace_id
                   OR h.workspace_id != NEW.workspace_id
                   OR r.workspace_id != NEW.workspace_id
                   OR r.knowledge_id != NEW.record_id)
        ) THEN RAISE(ABORT, 'memory selection workspace mismatch') END;
    END
    """,
    """
    CREATE TRIGGER memory_search_terms_workspace_guard_insert
    BEFORE INSERT ON memory_search_terms
    BEGIN
        SELECT CASE WHEN EXISTS (
            SELECT 1 FROM project_knowledge_revisions r
            WHERE r.knowledge_revision_id = NEW.knowledge_revision_id
              AND r.workspace_id != NEW.workspace_id
        ) THEN RAISE(ABORT, 'memory search term workspace mismatch') END;
    END
    """,
)

V12 = SchemaMigration(version=12, name=V12_NAME, statements=V12_STATEMENTS)

V13 = SchemaMigration(version=13, name=V13_NAME, statements=V13_STATEMENTS)

V14 = SchemaMigration(version=14, name=V14_NAME, statements=V14_STATEMENTS)
V15 = SchemaMigration(version=15, name=V15_NAME, statements=V15_STATEMENTS)
V16 = SchemaMigration(version=16, name=V16_NAME, statements=V16_STATEMENTS)
V17 = SchemaMigration(version=17, name=V17_NAME, statements=V17_STATEMENTS)
V18 = SchemaMigration(version=18, name=V18_NAME, statements=V18_STATEMENTS)
V19 = SchemaMigration(version=19, name=V19_NAME, statements=V19_STATEMENTS)
V20 = SchemaMigration(version=20, name=V20_NAME, statements=V20_STATEMENTS)
V21 = SchemaMigration(version=21, name=V21_NAME, statements=V21_STATEMENTS)
V22 = SchemaMigration(version=22, name=V22_NAME, statements=V22_STATEMENTS)
V23 = SchemaMigration(version=23, name=V23_NAME, statements=V23_STATEMENTS)
V24 = SchemaMigration(version=24, name=V24_NAME, statements=V24_STATEMENTS)


class MigrationRegistry:
    def __init__(self, *, supported_version: int = SUPPORTED_SCHEMA_VERSION) -> None:
        if supported_version not in RESERVED_SCHEMA_VERSIONS:
            raise StorageError(
                StorageErrorCode.UNAVAILABLE,
                "operational store schema version is outside the reserved range",
            )
        self.supported_version = supported_version
        self._migrations: dict[int, SchemaMigration] = {}

    def add(self, migration: SchemaMigration) -> None:
        if migration.version not in RESERVED_SCHEMA_VERSIONS:
            raise StorageError(
                StorageErrorCode.UNAVAILABLE,
                "operational store schema version is outside the reserved range",
            )
        if migration.version in self._migrations:
            raise StorageError(
                StorageErrorCode.UNAVAILABLE,
                "operational store migration version is already registered",
            )
        if migration.version > self.supported_version:
            raise StorageError(
                StorageErrorCode.UNAVAILABLE,
                "operational store migration exceeds the supported schema version",
            )
        self._migrations[migration.version] = migration

    def get(self, version: int) -> SchemaMigration:
        try:
            return self._migrations[version]
        except KeyError as exc:
            raise StorageError(
                StorageErrorCode.UNAVAILABLE,
                "operational store migration is not registered",
            ) from exc

    def pending(self, current_version: int) -> tuple[SchemaMigration, ...]:
        return tuple(
            self._migrations[version]
            for version in range(current_version + 1, self.supported_version + 1)
            if version in self._migrations
        )

    def checksum_for(self, version: int) -> str | None:
        migration = self._migrations.get(version)
        if migration is None:
            return None
        return migration.checksum


def production_registry() -> MigrationRegistry:
    registry = MigrationRegistry(supported_version=SUPPORTED_SCHEMA_VERSION)
    registry.add(V1)
    registry.add(V2)
    registry.add(V3)
    registry.add(V4)
    registry.add(V5)
    registry.add(V6)
    registry.add(V7)
    registry.add(V8)
    registry.add(V9)
    registry.add(V10)
    registry.add(V11)
    registry.add(V12)
    registry.add(V13)
    registry.add(V14)
    registry.add(V15)
    registry.add(V16)
    registry.add(V17)
    registry.add(V18)
    registry.add(V19)
    registry.add(V20)
    registry.add(V21)
    registry.add(V22)
    registry.add(V23)
    registry.add(V24)
    return registry


def identity_insert_sql() -> str:
    return """
        INSERT INTO store_identity(
            singleton, application_name, schema_version, created_at_unix
        )
        VALUES (1, ?, ?, ?)
        """


def migration_insert_sql() -> str:
    return """
        INSERT INTO schema_migrations(version, name, checksum, applied_at_unix)
        VALUES (?, ?, ?, ?)
        """


def identity_version_sql() -> str:
    return "UPDATE store_identity SET schema_version = ? WHERE singleton = 1"


# Keep the production application name in this module so checksums stay stable.
assert APPLICATION_NAME == "morrow-operational-store"
