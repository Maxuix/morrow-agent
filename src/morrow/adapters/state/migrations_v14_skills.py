"""Operational Store v14: Skill definitions, versions, operations, selections and contexts.

Global rows use the empty string as a non-null storage sentinel for
``scope_id``. SQLite treats NULL values as distinct in unique constraints and
composite foreign keys, so a nullable global key would not provide the
identity or referential guarantees these tables need. The AgentRun
selection/context tables are reserved fully in this migration; later
subplans add journal methods but never alter this DDL (the migration checksum
must stay fixed after release).
"""

V14_NAME = "skill_catalog_foundation"

GLOBAL_SCOPE_ID = ""

V14_STATEMENTS = (
    """
    CREATE TABLE skill_definitions (
        scope TEXT NOT NULL CHECK (scope IN ('global', 'workspace')),
        scope_id TEXT NOT NULL CHECK (
            (scope = 'global' AND scope_id = '') OR
            (scope = 'workspace' AND substr(scope_id, 1, 3) = 'ws_' AND length(scope_id) > 3)
        ),
        skill_id TEXT NOT NULL,
        name TEXT NOT NULL,
        source_kind TEXT NOT NULL
            CHECK (source_kind IN ('builtin', 'user_authored', 'generated', 'imported')),
        availability TEXT NOT NULL
            CHECK (availability IN ('available', 'invalid', 'conflicted', 'unavailable')),
        conflict_status TEXT NOT NULL
            CHECK (conflict_status IN ('none', 'identity_conflict', 'name_conflict')),
        effective_trust TEXT NOT NULL
            CHECK (effective_trust IN ('builtin', 'user', 'generated', 'imported', 'unknown')),
        updated_at_unix INTEGER NOT NULL,
        PRIMARY KEY (scope, scope_id, skill_id)
    )
    """,
    """
    CREATE TABLE skill_versions (
        version_id TEXT PRIMARY KEY,
        scope TEXT NOT NULL CHECK (scope IN ('global', 'workspace')),
        scope_id TEXT NOT NULL CHECK (
            (scope = 'global' AND scope_id = '') OR
            (scope = 'workspace' AND substr(scope_id, 1, 3) = 'ws_' AND length(scope_id) > 3)
        ),
        skill_id TEXT NOT NULL,
        display_version TEXT,
        tree_digest TEXT NOT NULL,
        file_count INTEGER NOT NULL CHECK (file_count >= 0),
        total_bytes INTEGER NOT NULL CHECK (total_bytes >= 0),
        source_kind TEXT NOT NULL
            CHECK (source_kind IN ('builtin', 'user_authored', 'generated', 'imported')),
        provenance TEXT NOT NULL,
        evidence_refs_json TEXT NOT NULL,
        effective_trust TEXT NOT NULL
            CHECK (effective_trust IN ('builtin', 'user', 'generated', 'imported', 'unknown')),
        installed_at_unix INTEGER NOT NULL,
        FOREIGN KEY (scope, scope_id, skill_id)
            REFERENCES skill_definitions (scope, scope_id, skill_id)
    )
    """,
    """
    CREATE INDEX skill_versions_identity ON skill_versions(scope, scope_id, skill_id)
    """,
    """
    CREATE TABLE skill_catalog_operations (
        operation_id TEXT PRIMARY KEY,
        scope TEXT NOT NULL CHECK (scope IN ('global', 'workspace')),
        scope_id TEXT NOT NULL CHECK (
            (scope = 'global' AND scope_id = '') OR
            (scope = 'workspace' AND substr(scope_id, 1, 3) = 'ws_' AND length(scope_id) > 3)
        ),
        skill_id TEXT NOT NULL,
        version_id TEXT,
        operation TEXT NOT NULL CHECK (
            operation IN (
                'import', 'enable', 'disable', 'pin', 'unpin', 'rollback',
                'remove', 'draft_create', 'draft_accept', 'usage_record'
            )
        ),
        disposition TEXT NOT NULL CHECK (
            disposition IN ('applied', 'rejected', 'failed')
        ),
        evidence_digest TEXT NOT NULL,
        reason TEXT,
        created_at_unix INTEGER NOT NULL
    )
    """,
    """
    CREATE INDEX skill_catalog_operations_identity
        ON skill_catalog_operations(scope, scope_id, skill_id)
    """,
    """
    CREATE TABLE agent_run_skill_selections (
        selection_id TEXT PRIMARY KEY,
        agent_run_id TEXT NOT NULL REFERENCES agent_runs(agent_run_id),
        scope TEXT NOT NULL CHECK (scope IN ('global', 'workspace')),
        scope_id TEXT NOT NULL CHECK (
            (scope = 'global' AND scope_id = '') OR
            (scope = 'workspace' AND substr(scope_id, 1, 3) = 'ws_' AND length(scope_id) > 3)
        ),
        skill_id TEXT NOT NULL,
        version_id TEXT NOT NULL REFERENCES skill_versions(version_id),
        activation_reason TEXT NOT NULL,
        tree_digest TEXT NOT NULL,
        created_at_unix INTEGER NOT NULL
    )
    """,
    """
    CREATE INDEX agent_run_skill_selections_run
        ON agent_run_skill_selections(agent_run_id)
    """,
    """
    CREATE TABLE agent_run_skill_contexts (
        context_id TEXT PRIMARY KEY,
        agent_run_id TEXT NOT NULL REFERENCES agent_runs(agent_run_id),
        scope TEXT NOT NULL CHECK (scope IN ('global', 'workspace')),
        scope_id TEXT NOT NULL CHECK (
            (scope = 'global' AND scope_id = '') OR
            (scope = 'workspace' AND substr(scope_id, 1, 3) = 'ws_' AND length(scope_id) > 3)
        ),
        skill_id TEXT NOT NULL,
        version_id TEXT NOT NULL REFERENCES skill_versions(version_id),
        context_digest TEXT NOT NULL,
        context_json TEXT NOT NULL,
        omitted_count INTEGER NOT NULL CHECK (omitted_count >= 0),
        created_at_unix INTEGER NOT NULL
    )
    """,
    """
    CREATE INDEX agent_run_skill_contexts_run
        ON agent_run_skill_contexts(agent_run_id)
    """,
)
