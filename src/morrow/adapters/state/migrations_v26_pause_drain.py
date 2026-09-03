"""Stage 8 Pause/Drain runtime schema: one merged rebuild of ``workflow_runs``.

Per the frozen runtime contracts (docs/decisions/stage-8-runtime-contracts.md,
C1/C5) a single rebuild covers every Stage 8 runtime change to the core table:
the ``draining``/``paused``/``superseded`` statuses, the orthogonal
``pause_requested`` fact, the lineage columns (``run_relation``,
``lineage_budget_root_run_id``, ``parent_run_id``), the widened
``workflow_active_root`` partial unique index, and the immutable execution-set
and artifact-import tables. The continuation code paths land in later subplans;
the lineage tables stay inert until then. Existing rows backfill to
``pause_requested=0``, ``run_relation='initial'`` and a self budget root, so
upgraded running/blocked runs recover without NULL-induced stalls.
"""

V26_NAME = "workflow_pause_drain_lineage"
V26_STATEMENTS = (
    """
    ALTER TABLE workflow_runs RENAME TO workflow_runs_v25
    """,
    """
    CREATE TABLE workflow_runs (
        workflow_run_id TEXT PRIMARY KEY,
        workspace_id TEXT NOT NULL,
        workflow_revision_id TEXT NOT NULL REFERENCES workflow_revisions(workflow_revision_id),
        root_task_run_id TEXT NOT NULL REFERENCES task_runs(task_run_id),
        status TEXT NOT NULL CHECK(status IN (
            'queued','running','completed','failed','cancelled','blocked',
            'draining','paused','superseded'
        )),
        pause_requested INTEGER NOT NULL DEFAULT 0 CHECK(pause_requested IN (0,1)),
        run_relation TEXT NOT NULL DEFAULT 'initial'
            CHECK(run_relation IN ('initial','continuation','rerun')),
        lineage_budget_root_run_id TEXT NOT NULL REFERENCES workflow_runs(workflow_run_id),
        parent_run_id TEXT REFERENCES workflow_runs(workflow_run_id),
        body_json TEXT NOT NULL CHECK(length(body_json)<=16384)
    )
    """,
    """
    INSERT INTO workflow_runs(
        workflow_run_id, workspace_id, workflow_revision_id, root_task_run_id, status,
        pause_requested, run_relation, lineage_budget_root_run_id, parent_run_id, body_json
    )
    SELECT workflow_run_id, workspace_id, workflow_revision_id, root_task_run_id, status,
           0, 'initial', workflow_run_id, NULL, body_json
    FROM workflow_runs_v25
    """,
    """
    DROP TABLE workflow_runs_v25
    """,
    """
    CREATE UNIQUE INDEX workflow_active_root ON workflow_runs(root_task_run_id)
        WHERE status IN ('queued','running','blocked','draining','paused')
    """,
    """
    CREATE TABLE workflow_run_execution_nodes (
        workflow_run_id TEXT NOT NULL REFERENCES workflow_runs(workflow_run_id),
        node_id TEXT NOT NULL,
        topology_ordinal INTEGER NOT NULL CHECK(topology_ordinal >= 0),
        inclusion_reason TEXT NOT NULL
            CHECK(inclusion_reason IN ('initial','retained_future','failed_retry')),
        PRIMARY KEY(workflow_run_id, node_id)
    )
    """,
    """
    CREATE TABLE workflow_run_artifact_imports (
        workflow_run_id TEXT NOT NULL REFERENCES workflow_runs(workflow_run_id),
        source_workflow_run_id TEXT NOT NULL REFERENCES workflow_runs(workflow_run_id),
        source_node_run_id TEXT NOT NULL REFERENCES workflow_node_runs(node_run_id),
        source_node_id TEXT NOT NULL,
        output_slot TEXT NOT NULL,
        artifact_id TEXT NOT NULL REFERENCES artifacts(artifact_id),
        contract_json TEXT NOT NULL CHECK(length(contract_json)<=2048),
        inherited_at_unix INTEGER NOT NULL,
        PRIMARY KEY(workflow_run_id, source_node_id, output_slot)
    )
    """,
    """
    CREATE TRIGGER workflow_runs_terminal_immutable BEFORE UPDATE ON workflow_runs
    WHEN OLD.status IN ('completed','failed','cancelled','superseded')
    BEGIN SELECT RAISE(ABORT, 'Terminal Workflow evidence is immutable'); END
    """,
    """
    CREATE TRIGGER workflow_runs_delete_immutable BEFORE DELETE ON workflow_runs
    BEGIN SELECT RAISE(ABORT, 'Workflow history cannot be deleted'); END
    """,
    *tuple(
        f"""CREATE TRIGGER {table}_{operation.lower()}_immutable BEFORE {operation} ON {table}
        BEGIN SELECT RAISE(ABORT, 'Workflow evidence is immutable'); END"""
        for table in (
            "workflow_run_execution_nodes",
            "workflow_run_artifact_imports",
        )
        for operation in ("UPDATE", "DELETE")
    ),
)
