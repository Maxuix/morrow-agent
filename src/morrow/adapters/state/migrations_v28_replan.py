"""Closure signals and immutable proposal/audit snapshots; no new graph writer."""

V28_NAME = "workflow_global_replan"
V28_STATEMENTS = (
    """CREATE TABLE workflow_replan_signals (
        signal_id TEXT PRIMARY KEY,
        workspace_id TEXT NOT NULL,
        workflow_run_id TEXT NOT NULL REFERENCES workflow_runs(workflow_run_id),
        node_run_id TEXT NOT NULL UNIQUE REFERENCES workflow_node_runs(node_run_id),
        consumed_by TEXT,
        body_json TEXT NOT NULL CHECK(length(body_json) <= 32768)
    )""",
    """CREATE INDEX workflow_replan_pending
        ON workflow_replan_signals(workspace_id, workflow_run_id, consumed_by)""",
    """CREATE TABLE workflow_replan_proposals (
        proposal_id TEXT PRIMARY KEY,
        workspace_id TEXT NOT NULL,
        workflow_run_id TEXT NOT NULL REFERENCES workflow_runs(workflow_run_id),
        row_version INTEGER NOT NULL,
        body_json TEXT NOT NULL CHECK(length(body_json) <= 524288)
    )""",
    """CREATE INDEX workflow_replan_history
        ON workflow_replan_proposals(workspace_id, workflow_run_id)""",
)
