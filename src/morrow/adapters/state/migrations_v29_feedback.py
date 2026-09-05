"""Bounded feedback and review/evaluation evidence; no chat-history copies."""

V29_NAME = "workflow_feedback_evaluation"
V29_STATEMENTS = tuple(
    statement
    for name in ("feedback", "policy_candidates", "evaluations")
    for statement in (
        f"""CREATE TABLE workflow_{name} (
            record_id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL,
            body_json TEXT NOT NULL CHECK(length(body_json) <= 32768)
        )""",
        f"CREATE INDEX workflow_{name}_workspace ON workflow_{name}(workspace_id, record_id)",
    )
)
