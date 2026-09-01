"""Workflow execution: the frozen node request cap may narrow the Definition ceiling.

The v23 identity trigger required an exact ``max_agent_generation_requests``
match between the AgentRun snapshot and the Definition source. A Workflow leaf
instead freezes ``effective_node_generation_request_cap = min(declared node
maximum, Workflow remaining)``, which the Workflow journal separately proves
never exceeds the compiled declaration. The relaxed trigger keeps exact-match
enforcement for standalone runs (the application admission seam re-checks it)
while permitting a strictly narrower positive cap on the same exact Version.
"""

V25_NAME = "workflow_node_request_cap"
V25_STATEMENTS = (
    "DROP TRIGGER agent_runs_definition_identity_insert",
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
              AND (
                  json_extract(NEW.snapshot_json, '$.max_agent_generation_requests')
                      IS json_extract(v.body_json, '$.source.max_agent_generation_requests')
                  OR (
                      json_type(NEW.snapshot_json, '$.max_agent_generation_requests') = 'integer'
                      AND json_extract(NEW.snapshot_json, '$.max_agent_generation_requests') > 0
                      AND (
                          json_type(v.body_json, '$.source.max_agent_generation_requests') = 'null'
                          OR json_extract(NEW.snapshot_json, '$.max_agent_generation_requests')
                              <= json_extract(v.body_json, '$.source.max_agent_generation_requests')
                      )
                  )
              )
        ) THEN RAISE(ABORT, 'Agent definition run evidence mismatch') END;
    END""",
)
