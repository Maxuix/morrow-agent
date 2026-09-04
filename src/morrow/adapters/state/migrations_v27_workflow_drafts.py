"""Durable OCC state for pre-freeze Workflow editing."""

V27_NAME = "workflow_editor_drafts"
V27_STATEMENTS = (
    """
    CREATE TABLE workflow_drafts (
        draft_id TEXT PRIMARY KEY,
        workspace_id TEXT NOT NULL,
        workflow_definition_id TEXT NOT NULL,
        status TEXT NOT NULL CHECK(status IN (
            'draft','validating','valid','invalid','rejected','frozen'
        )),
        row_version INTEGER NOT NULL CHECK(row_version >= 1),
        body_json TEXT NOT NULL CHECK(length(body_json) <= 524288),
        created_at_unix INTEGER NOT NULL,
        updated_at_unix INTEGER NOT NULL
    )
    """,
    """
    CREATE INDEX workflow_drafts_workspace_updated
        ON workflow_drafts(workspace_id, updated_at_unix, draft_id)
    """,
)
