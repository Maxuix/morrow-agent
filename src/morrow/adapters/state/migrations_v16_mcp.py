"""Operational Store v16 DDL for MCP definitions, Catalogs and run evidence."""

from __future__ import annotations

V16_NAME = "mcp_control_catalog_and_snapshots"

V16_STATEMENTS = (
    """
    CREATE TABLE mcp_servers (
        server_id TEXT NOT NULL,
        scope TEXT NOT NULL CHECK (scope IN ('global', 'workspace')),
        scope_id TEXT NOT NULL,
        display_name TEXT NOT NULL CHECK (length(display_name) BETWEEN 1 AND 256),
        transport TEXT NOT NULL CHECK (transport = 'stdio'),
        executable TEXT NOT NULL CHECK (length(executable) BETWEEN 1 AND 4096),
        argv_json TEXT NOT NULL CHECK (length(argv_json) BETWEEN 2 AND 262144),
        argv_digest TEXT NOT NULL CHECK (length(argv_digest) = 64),
        executable_kind TEXT NOT NULL CHECK (executable_kind IN ('absolute', 'managed')),
        cwd_policy TEXT NOT NULL CHECK (cwd_policy IN ('workspace', 'absolute', 'managed')),
        cwd TEXT,
        timeout_ms INTEGER NOT NULL CHECK (timeout_ms BETWEEN 100 AND 300000),
        credential_refs_json TEXT NOT NULL CHECK (length(credential_refs_json) BETWEEN 2 AND 8192),
        workspace_visibility TEXT NOT NULL
            CHECK (workspace_visibility IN ('none', 'read_only', 'read_write')),
        launch_risks_json TEXT NOT NULL CHECK (length(launch_risks_json) BETWEEN 2 AND 4096),
        enabled INTEGER NOT NULL CHECK (enabled IN (0, 1)),
        tool_policy_json TEXT NOT NULL CHECK (length(tool_policy_json) BETWEEN 2 AND 32768),
        config_revision INTEGER NOT NULL CHECK (config_revision >= 1),
        config_digest TEXT NOT NULL CHECK (length(config_digest) = 64),
        catalog_revision INTEGER,
        catalog_digest TEXT CHECK (catalog_digest IS NULL OR length(catalog_digest) = 64),
        catalog_status TEXT NOT NULL
            CHECK (catalog_status IN ('empty', 'ready', 'degraded', 'unavailable')),
        degraded_reason TEXT,
        created_at_unix INTEGER NOT NULL,
        updated_at_unix INTEGER NOT NULL,
        PRIMARY KEY (scope, scope_id, server_id),
        CHECK ((scope = 'global' AND scope_id = '')
               OR (scope = 'workspace' AND scope_id LIKE 'ws_%'))
    )
    """,
    """
    CREATE INDEX mcp_servers_scope_enabled
        ON mcp_servers(scope, scope_id, enabled, server_id)
    """,
    """
    CREATE TABLE mcp_catalog_revisions (
        catalog_revision_id TEXT PRIMARY KEY,
        server_id TEXT NOT NULL,
        scope TEXT NOT NULL CHECK (scope IN ('global', 'workspace')),
        scope_id TEXT NOT NULL,
        revision INTEGER NOT NULL CHECK (revision >= 1),
        status TEXT NOT NULL CHECK (status IN ('empty', 'ready', 'degraded', 'unavailable')),
        catalog_digest TEXT NOT NULL CHECK (length(catalog_digest) = 64),
        handshake_json TEXT,
        degraded_reason TEXT,
        created_at_unix INTEGER NOT NULL,
        UNIQUE (scope, scope_id, server_id, revision),
        FOREIGN KEY (scope, scope_id, server_id)
            REFERENCES mcp_servers(scope, scope_id, server_id)
            ON DELETE CASCADE
    )
    """,
    """
    CREATE INDEX mcp_catalog_revisions_server
        ON mcp_catalog_revisions(scope, scope_id, server_id, revision)
    """,
    """
    CREATE TABLE mcp_catalog_tools (
        catalog_revision_id TEXT NOT NULL REFERENCES mcp_catalog_revisions(catalog_revision_id)
            ON DELETE CASCADE,
        remote_name TEXT NOT NULL CHECK (length(remote_name) BETWEEN 1 AND 128),
        local_name TEXT NOT NULL CHECK (length(local_name) BETWEEN 7 AND 64),
        description TEXT NOT NULL DEFAULT '',
        input_schema_json TEXT,
        output_schema_json TEXT,
        schema_dialect TEXT,
        input_schema_digest TEXT CHECK (
            input_schema_digest IS NULL OR length(input_schema_digest) = 64
        ),
        output_schema_digest TEXT CHECK (
            output_schema_digest IS NULL OR length(output_schema_digest) = 64
        ),
        annotations_json TEXT NOT NULL CHECK (length(annotations_json) BETWEEN 2 AND 8192),
        status TEXT NOT NULL
            CHECK (status IN ('ready', 'invalid_schema', 'unmapped', 'disallowed')),
        invalid_reason TEXT,
        effect TEXT CHECK (
            effect IS NULL OR effect IN ('none', 'session_write', 'persistent_write')
        ),
        approval TEXT CHECK (
            approval IS NULL OR approval IN ('deny', 'require_approval', 'allow')
        ),
        mapping_enabled INTEGER CHECK (mapping_enabled IS NULL OR mapping_enabled IN (0, 1)),
        PRIMARY KEY (catalog_revision_id, remote_name),
        UNIQUE (catalog_revision_id, local_name),
        CHECK (input_schema_json IS NULL OR length(input_schema_json) <= 16384),
        CHECK (output_schema_json IS NULL OR length(output_schema_json) <= 16384)
    )
    """,
    """
    CREATE TABLE mcp_run_launch_snapshots (
        launch_snapshot_id TEXT PRIMARY KEY,
        workspace_id TEXT NOT NULL,
        agent_run_id TEXT NOT NULL REFERENCES agent_runs(agent_run_id),
        server_id TEXT NOT NULL,
        config_revision INTEGER NOT NULL CHECK (config_revision >= 1),
        config_digest TEXT NOT NULL CHECK (length(config_digest) = 64),
        catalog_revision INTEGER,
        catalog_digest TEXT CHECK (catalog_digest IS NULL OR length(catalog_digest) = 64),
        transport TEXT NOT NULL CHECK (transport = 'stdio'),
        argv_digest TEXT NOT NULL CHECK (length(argv_digest) = 64),
        executable_digest TEXT NOT NULL CHECK (length(executable_digest) = 64),
        cwd_policy TEXT NOT NULL CHECK (cwd_policy IN ('workspace', 'absolute', 'managed')),
        workspace_visibility TEXT NOT NULL
            CHECK (workspace_visibility IN ('none', 'read_only', 'read_write')),
        network_risk INTEGER NOT NULL CHECK (network_risk IN (0, 1)),
        credential_risk INTEGER NOT NULL CHECK (credential_risk IN (0, 1)),
        outside_workspace_risk INTEGER NOT NULL CHECK (outside_workspace_risk IN (0, 1)),
        allowlisted_remote_tools_json TEXT NOT NULL,
        created_at_unix INTEGER NOT NULL
    )
    """,
    """
    CREATE INDEX mcp_run_launch_snapshots_run
        ON mcp_run_launch_snapshots(workspace_id, agent_run_id, server_id)
    """,
    """
    CREATE TABLE mcp_run_tool_snapshots (
        tool_snapshot_id TEXT PRIMARY KEY,
        workspace_id TEXT NOT NULL,
        launch_snapshot_id TEXT NOT NULL REFERENCES mcp_run_launch_snapshots(launch_snapshot_id),
        agent_run_id TEXT NOT NULL REFERENCES agent_runs(agent_run_id),
        server_id TEXT NOT NULL,
        remote_name TEXT NOT NULL,
        local_name TEXT NOT NULL,
        schema_dialect TEXT NOT NULL,
        input_schema_digest TEXT NOT NULL CHECK (length(input_schema_digest) = 64),
        output_schema_digest TEXT CHECK (
            output_schema_digest IS NULL OR length(output_schema_digest) = 64
        ),
        effect TEXT NOT NULL CHECK (effect IN ('none', 'session_write', 'persistent_write')),
        approval TEXT NOT NULL CHECK (approval IN ('deny', 'require_approval', 'allow')),
        catalog_revision INTEGER NOT NULL CHECK (catalog_revision >= 1),
        recovery_declaration_json TEXT NOT NULL CHECK (length(recovery_declaration_json) <= 16384),
        created_at_unix INTEGER NOT NULL
    )
    """,
    """
    CREATE INDEX mcp_run_tool_snapshots_run
        ON mcp_run_tool_snapshots(workspace_id, agent_run_id, local_name)
    """,
    """
    CREATE TABLE mcp_result_artifact_links (
        link_id TEXT PRIMARY KEY,
        workspace_id TEXT NOT NULL,
        tool_execution_id TEXT NOT NULL REFERENCES tool_executions(tool_execution_id),
        artifact_id TEXT NOT NULL REFERENCES artifacts(artifact_id),
        role TEXT NOT NULL CHECK (length(role) BETWEEN 1 AND 64),
        created_at_unix INTEGER NOT NULL,
        UNIQUE (tool_execution_id, artifact_id, role)
    )
    """,
    """
    CREATE INDEX mcp_result_artifact_links_execution
        ON mcp_result_artifact_links(workspace_id, tool_execution_id, role)
    """,
    """
    ALTER TABLE permission_snapshots
        ADD COLUMN mcp_review_evidence_json TEXT NOT NULL DEFAULT '[]'
    """,
    """
    ALTER TABLE mcp_run_launch_snapshots
        ADD COLUMN toolset_digest TEXT
    """,
)

__all__ = ["V16_NAME", "V16_STATEMENTS"]
