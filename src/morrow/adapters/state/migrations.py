"""Ordered, checksummed Operational Store migrations.

Production currently owns schema v1–v3. Versions 4–9 stay reserved for later
subplans and must not be renumbered after they land.
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
