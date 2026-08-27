"""Operational Store v21 resumable retry progress."""

from __future__ import annotations

V21_NAME = "agent_run_retry_progress"

V21_STATEMENTS = (
    """
    CREATE TABLE agent_run_retry_progress (
        agent_run_id TEXT PRIMARY KEY REFERENCES agent_runs(agent_run_id),
        workspace_id TEXT NOT NULL,
        consecutive_model_retries INTEGER NOT NULL DEFAULT 0
            CHECK (consecutive_model_retries >= 0),
        total_retry_count INTEGER NOT NULL DEFAULT 0
            CHECK (total_retry_count >= 0),
        summary_retry_count INTEGER NOT NULL DEFAULT 0
            CHECK (summary_retry_count >= 0),
        updated_at_unix INTEGER NOT NULL,
        CHECK (consecutive_model_retries <= total_retry_count),
        CHECK (summary_retry_count <= total_retry_count)
    )
    """,
    """
    CREATE INDEX agent_run_retry_progress_workspace
        ON agent_run_retry_progress(workspace_id, updated_at_unix, agent_run_id)
    """,
    """
    CREATE TRIGGER agent_run_retry_progress_workspace_guard_insert
    BEFORE INSERT ON agent_run_retry_progress
    BEGIN
        SELECT CASE WHEN NOT EXISTS (
            SELECT 1 FROM agent_runs r
            JOIN turns t ON t.turn_id = r.turn_id
            JOIN sessions s ON s.session_id = t.session_id
            WHERE r.agent_run_id = NEW.agent_run_id
              AND r.session_id = s.session_id
              AND s.workspace_id = NEW.workspace_id
        ) THEN RAISE(ABORT, 'AgentRun retry progress workspace mismatch') END;
    END
    """,
    """
    CREATE TRIGGER agent_run_retry_progress_workspace_guard_update
    BEFORE UPDATE OF workspace_id, agent_run_id ON agent_run_retry_progress
    BEGIN
        SELECT CASE WHEN NOT EXISTS (
            SELECT 1 FROM agent_runs r
            JOIN turns t ON t.turn_id = r.turn_id
            JOIN sessions s ON s.session_id = t.session_id
            WHERE r.agent_run_id = NEW.agent_run_id
              AND r.session_id = s.session_id
              AND s.workspace_id = NEW.workspace_id
        ) THEN RAISE(ABORT, 'AgentRun retry progress workspace mismatch') END;
    END
    """,
)

__all__ = ["V21_NAME", "V21_STATEMENTS"]
