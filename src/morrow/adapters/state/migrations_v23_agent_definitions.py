"""Immutable Agent definitions and OCC publication heads."""

V23_NAME = "agent_definition_foundation"
V23_STATEMENTS = (
    """CREATE TABLE agent_definition_versions (
        version_id TEXT PRIMARY KEY,
        workspace_id TEXT NOT NULL,
        definition_id TEXT NOT NULL,
        version INTEGER NOT NULL CHECK(version >= 1),
        content_hash TEXT NOT NULL CHECK(length(content_hash) = 64),
        body_json TEXT NOT NULL CHECK(length(body_json) <= 65536),
        UNIQUE(workspace_id, definition_id, version),
        UNIQUE(workspace_id, definition_id, version_id)
    )""",
    """CREATE TABLE agent_definition_heads (
        workspace_id TEXT NOT NULL,
        definition_id TEXT NOT NULL,
        version_id TEXT NOT NULL,
        source_revision INTEGER NOT NULL CHECK(source_revision >= 0),
        source_hash TEXT NOT NULL CHECK(length(source_hash) = 64),
        enabled INTEGER NOT NULL CHECK(enabled IN (0,1)),
        row_version INTEGER NOT NULL CHECK(row_version >= 1),
        PRIMARY KEY(workspace_id, definition_id),
        FOREIGN KEY(workspace_id, definition_id, version_id)
            REFERENCES agent_definition_versions(workspace_id, definition_id, version_id)
    )""",
    """CREATE TABLE agent_definition_revocations (
        version_id TEXT PRIMARY KEY REFERENCES agent_definition_versions(version_id),
        body_json TEXT NOT NULL CHECK(length(body_json) <= 2048)
    )""",
    """CREATE TABLE agent_definition_publications (
        workspace_id TEXT NOT NULL,
        command_id TEXT NOT NULL,
        request_hash TEXT NOT NULL CHECK(length(request_hash) = 64),
        version_id TEXT NOT NULL REFERENCES agent_definition_versions(version_id),
        PRIMARY KEY(workspace_id, command_id)
    )""",
    """CREATE TABLE agent_definition_skills (
        version_id TEXT NOT NULL REFERENCES agent_definition_versions(version_id),
        skill_version_id TEXT NOT NULL REFERENCES skill_versions(version_id),
        PRIMARY KEY(version_id, skill_version_id)
    )""",
    """CREATE TRIGGER agent_runs_definition_identity_insert
    BEFORE INSERT ON agent_runs
    WHEN json_extract(NEW.snapshot_json, '$.definition_ref') IS NOT NULL
    BEGIN
        SELECT CASE WHEN NOT EXISTS (
            SELECT 1 FROM agent_definition_versions v JOIN sessions s ON s.session_id=NEW.session_id
            WHERE v.workspace_id=s.workspace_id
              AND v.version_id=json_extract(NEW.snapshot_json, '$.definition_ref.version_id')
              AND v.definition_id=json_extract(NEW.snapshot_json, '$.definition_ref.definition_id')
              AND v.content_hash=json_extract(NEW.snapshot_json, '$.definition_ref.content_hash')
              AND NEW.session_id=json_extract(NEW.snapshot_json, '$.conversation_session_id')
              AND json_extract(NEW.snapshot_json, '$.max_agent_generation_requests')
                  IS json_extract(v.body_json, '$.source.max_agent_generation_requests')
        ) THEN RAISE(ABORT, 'Agent definition run evidence mismatch') END;
    END""",
    """CREATE TRIGGER agent_runs_definition_snapshot_immutable
    BEFORE UPDATE OF snapshot_json, session_id ON agent_runs
    WHEN json_extract(OLD.snapshot_json, '$.definition_ref') IS NOT NULL
      OR json_extract(NEW.snapshot_json, '$.definition_ref') IS NOT NULL
    BEGIN SELECT RAISE(ABORT, 'Agent definition run evidence is immutable'); END""",
    *tuple(
        f"""CREATE TRIGGER {table}_{operation.lower()}_immutable BEFORE {operation} ON {table}
        BEGIN SELECT RAISE(ABORT, 'Agent definition evidence is immutable'); END"""
        for table in (
            "agent_definition_versions",
            "agent_definition_revocations",
            "agent_definition_publications",
            "agent_definition_skills",
        )
        for operation in ("UPDATE", "DELETE")
    ),
)
