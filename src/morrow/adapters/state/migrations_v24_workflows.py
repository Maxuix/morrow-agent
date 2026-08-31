"""Workflow representation and ownership; no runtime or automatic publication."""

V24_NAME = "workflow_revision_artifact_contracts"
V24_STATEMENTS = (
    "ALTER TABLE task_runs ADD COLUMN purpose TEXT NOT NULL DEFAULT 'user' CHECK(purpose IN ('user','workflow_node'))",
    "ALTER TABLE artifacts ADD COLUMN text_safety_profile TEXT NOT NULL DEFAULT 'legacy_strict' CHECK(text_safety_profile IN ('legacy_strict','workflow_value_sensitive'))",
    "ALTER TABLE artifacts ADD COLUMN contract_json TEXT",
    "ALTER TABLE artifacts ADD COLUMN producer_node_run_id TEXT REFERENCES workflow_node_runs(node_run_id)",
    "ALTER TABLE artifacts ADD COLUMN output_slot TEXT",
    """CREATE UNIQUE INDEX artifact_node_output ON artifacts(producer_node_run_id, output_slot)
        WHERE producer_node_run_id IS NOT NULL""",
    """CREATE TABLE workflow_revisions (
        workflow_revision_id TEXT PRIMARY KEY,
        workspace_id TEXT NOT NULL,
        workflow_definition_id TEXT NOT NULL,
        revision INTEGER NOT NULL CHECK(revision >= 1),
        content_hash TEXT NOT NULL CHECK(length(content_hash)=64),
        body_json TEXT NOT NULL CHECK(length(body_json)<=262144),
        UNIQUE(workspace_id, workflow_definition_id, revision),
        UNIQUE(workspace_id, workflow_definition_id, workflow_revision_id)
    )""",
    """CREATE TABLE workflow_revision_nodes (
        workflow_revision_id TEXT NOT NULL REFERENCES workflow_revisions(workflow_revision_id),
        node_id TEXT NOT NULL,
        agent_definition_version_id TEXT NOT NULL REFERENCES agent_definition_versions(version_id),
        PRIMARY KEY(workflow_revision_id, node_id)
    )""",
    """CREATE TABLE workflow_definition_heads (
        workspace_id TEXT NOT NULL,
        workflow_definition_id TEXT NOT NULL,
        workflow_revision_id TEXT NOT NULL,
        body_json TEXT NOT NULL CHECK(length(body_json)<=2048),
        PRIMARY KEY(workspace_id, workflow_definition_id),
        FOREIGN KEY(workspace_id, workflow_definition_id, workflow_revision_id)
          REFERENCES workflow_revisions(workspace_id, workflow_definition_id, workflow_revision_id)
    )""",
    """CREATE TABLE workflow_revision_revocations (
        workflow_revision_id TEXT PRIMARY KEY REFERENCES workflow_revisions(workflow_revision_id),
        body_json TEXT NOT NULL CHECK(length(body_json)<=2048)
    )""",
    """CREATE TABLE workflow_publications (
        workspace_id TEXT NOT NULL, command_id TEXT NOT NULL, request_hash TEXT NOT NULL,
        workflow_revision_id TEXT NOT NULL REFERENCES workflow_revisions(workflow_revision_id),
        PRIMARY KEY(workspace_id, command_id)
    )""",
    """CREATE TABLE workflow_runs (
        workflow_run_id TEXT PRIMARY KEY,
        workspace_id TEXT NOT NULL,
        workflow_revision_id TEXT NOT NULL REFERENCES workflow_revisions(workflow_revision_id),
        root_task_run_id TEXT NOT NULL REFERENCES task_runs(task_run_id),
        status TEXT NOT NULL CHECK(status IN ('queued','running','completed','failed','cancelled','blocked')),
        body_json TEXT NOT NULL CHECK(length(body_json)<=16384)
    )""",
    """CREATE UNIQUE INDEX workflow_active_root ON workflow_runs(root_task_run_id)
        WHERE status IN ('queued','running','blocked')""",
    """CREATE TABLE workflow_node_runs (
        node_run_id TEXT PRIMARY KEY,
        workspace_id TEXT NOT NULL,
        workflow_run_id TEXT NOT NULL REFERENCES workflow_runs(workflow_run_id),
        node_id TEXT NOT NULL,
        attempt INTEGER NOT NULL CHECK(attempt=1),
        status TEXT NOT NULL CHECK(status IN ('queued','running','completed','failed','cancelled','blocked')),
        body_json TEXT NOT NULL CHECK(length(body_json)<=16384),
        UNIQUE(workflow_run_id, node_id, attempt)
    )""",
    """CREATE TABLE workflow_artifact_bindings (
        workflow_run_id TEXT NOT NULL REFERENCES workflow_runs(workflow_run_id),
        node_run_id TEXT NOT NULL DEFAULT '',
        direction TEXT NOT NULL CHECK(direction IN ('input','output')),
        name TEXT NOT NULL,
        artifact_id TEXT NOT NULL REFERENCES artifacts(artifact_id),
        body_json TEXT NOT NULL CHECK(length(body_json)<=2048),
        PRIMARY KEY(workflow_run_id, node_run_id, direction, name)
    )""",
    """CREATE TABLE workflow_agent_run_refs (
        agent_run_id TEXT PRIMARY KEY REFERENCES agent_runs(agent_run_id),
        node_run_id TEXT NOT NULL UNIQUE REFERENCES workflow_node_runs(node_run_id)
    )""",
    """CREATE TABLE workflow_leaf_ownership (
        node_run_id TEXT PRIMARY KEY REFERENCES workflow_node_runs(node_run_id),
        session_id TEXT NOT NULL UNIQUE REFERENCES sessions(session_id),
        task_run_id TEXT NOT NULL UNIQUE REFERENCES task_runs(task_run_id)
    )""",
    """CREATE TRIGGER task_purpose_immutable BEFORE UPDATE OF purpose ON task_runs
        BEGIN SELECT RAISE(ABORT, 'Task purpose is immutable'); END""",
    *tuple(
        f"""CREATE TRIGGER {table}_{operation.lower()}_immutable BEFORE {operation} ON {table}
        BEGIN SELECT RAISE(ABORT, 'Workflow evidence is immutable'); END"""
        for table in (
            "workflow_revisions",
            "workflow_revision_nodes",
            "workflow_revision_revocations",
            "workflow_publications",
            "workflow_artifact_bindings",
            "workflow_agent_run_refs",
            "workflow_leaf_ownership",
        )
        for operation in ("UPDATE", "DELETE")
    ),
    *tuple(
        f"""CREATE TRIGGER {table}_terminal_immutable BEFORE UPDATE ON {table}
        WHEN OLD.status IN ('completed','failed','cancelled')
        BEGIN SELECT RAISE(ABORT, 'Terminal Workflow evidence is immutable'); END"""
        for table in ("workflow_runs", "workflow_node_runs")
    ),
    *tuple(
        f"""CREATE TRIGGER {table}_delete_immutable BEFORE DELETE ON {table}
        BEGIN SELECT RAISE(ABORT, 'Workflow history cannot be deleted'); END"""
        for table in ("workflow_runs", "workflow_node_runs")
    ),
)
