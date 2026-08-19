"""Ordered, checksummed Operational Store migrations.

Production currently owns schema v1–v8. Version 9 stays reserved for later
subplans and must not be renumbered after it lands.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

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
