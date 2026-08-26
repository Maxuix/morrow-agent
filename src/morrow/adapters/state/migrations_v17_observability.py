"""Operational Store v17 DDL for safe AgentRun observations."""

from __future__ import annotations

V17_NAME = "agent_run_observability"

V17_STATEMENTS = (
    """
    CREATE TABLE agent_run_model_requests (
        model_request_id TEXT PRIMARY KEY,
        workspace_id TEXT NOT NULL,
        agent_run_id TEXT NOT NULL REFERENCES agent_runs(agent_run_id),
        attempt_ordinal INTEGER NOT NULL CHECK (attempt_ordinal >= 1),
        state TEXT NOT NULL CHECK (state IN ('admitted', 'completed', 'failed', 'cancelled')),
        admitted_at_unix INTEGER NOT NULL,
        settled_at_unix INTEGER,
        estimated_request_chars INTEGER NOT NULL CHECK (estimated_request_chars >= 0),
        request_char_budget INTEGER NOT NULL CHECK (request_char_budget > 0),
        cleared_cycle_count INTEGER NOT NULL CHECK (cleared_cycle_count >= 0),
        dropped_turn_count INTEGER NOT NULL CHECK (dropped_turn_count >= 0),
        dropped_cycle_count INTEGER NOT NULL CHECK (dropped_cycle_count >= 0),
        dropped_record_count INTEGER NOT NULL CHECK (dropped_record_count >= 0),
        tool_rounds INTEGER NOT NULL CHECK (tool_rounds >= 0),
        tool_calls INTEGER NOT NULL CHECK (tool_calls >= 0),
        finish_reason TEXT CHECK (
            finish_reason IS NULL OR finish_reason IN ('stop', 'tool_calls', 'length', 'content_filter')
        ),
        error_code TEXT CHECK (
            error_code IS NULL OR error_code IN (
                'auth', 'network', 'rate_limit', 'timeout', 'invalid_response', 'internal'
            )
        ),
        usage_availability TEXT NOT NULL CHECK (usage_availability IN ('available', 'unavailable')),
        input_tokens INTEGER CHECK (input_tokens IS NULL OR input_tokens >= 0),
        output_tokens INTEGER CHECK (output_tokens IS NULL OR output_tokens >= 0),
        total_tokens INTEGER CHECK (total_tokens IS NULL OR total_tokens >= 0),
        cost_availability TEXT NOT NULL CHECK (cost_availability IN ('available', 'unavailable')),
        cost_amount_minor INTEGER CHECK (cost_amount_minor IS NULL OR cost_amount_minor >= 0),
        cost_currency TEXT CHECK (cost_currency IS NULL OR length(cost_currency) = 3),
        cost_source TEXT CHECK (cost_source IS NULL OR length(cost_source) BETWEEN 1 AND 128),
        UNIQUE (agent_run_id, attempt_ordinal),
        CHECK (
            (state = 'admitted' AND settled_at_unix IS NULL AND finish_reason IS NULL AND error_code IS NULL)
            OR (state != 'admitted' AND settled_at_unix IS NOT NULL)
        ),
        CHECK (settled_at_unix IS NULL OR settled_at_unix >= admitted_at_unix),
        CHECK (
            (usage_availability = 'unavailable'
                AND input_tokens IS NULL AND output_tokens IS NULL AND total_tokens IS NULL)
            OR (usage_availability = 'available'
                AND (input_tokens IS NOT NULL OR output_tokens IS NOT NULL OR total_tokens IS NOT NULL))
        ),
        CHECK (
            (input_tokens IS NULL OR output_tokens IS NULL OR total_tokens IS NULL
                OR total_tokens = input_tokens + output_tokens)
        ),
        CHECK (
            (cost_availability = 'unavailable'
                AND cost_amount_minor IS NULL AND cost_currency IS NULL AND cost_source IS NULL)
            OR (cost_availability = 'available'
                AND cost_amount_minor IS NOT NULL AND cost_currency IS NOT NULL AND cost_source IS NOT NULL)
        )
    )
    """,
    """
    CREATE INDEX agent_run_model_requests_run
        ON agent_run_model_requests(workspace_id, agent_run_id, attempt_ordinal)
    """,
    """
    CREATE TABLE agent_run_terminal_metrics (
        agent_run_id TEXT PRIMARY KEY REFERENCES agent_runs(agent_run_id),
        workspace_id TEXT NOT NULL,
        finish_reason TEXT NOT NULL CHECK (finish_reason IN ('stop', 'cancelled', 'error')),
        stop_code TEXT CHECK (
            stop_code IS NULL OR stop_code IN (
                'provider_auth', 'provider_network', 'provider_rate_limit', 'provider_timeout',
                'invalid_response', 'model_output_limit', 'content_filtered', 'context_budget',
                'model_call_limit', 'tool_call_limit', 'run_timeout', 'loop_detected', 'internal'
            )
        ),
        model_attempts INTEGER NOT NULL CHECK (model_attempts >= 0),
        retry_count INTEGER NOT NULL CHECK (retry_count >= 0),
        tool_rounds INTEGER NOT NULL CHECK (tool_rounds >= 0),
        tool_calls INTEGER NOT NULL CHECK (tool_calls >= 0),
        max_estimated_request_chars INTEGER NOT NULL CHECK (max_estimated_request_chars >= 0),
        request_char_budget INTEGER NOT NULL CHECK (request_char_budget > 0),
        cleared_cycle_count INTEGER NOT NULL CHECK (cleared_cycle_count >= 0),
        dropped_turn_count INTEGER NOT NULL CHECK (dropped_turn_count >= 0),
        dropped_cycle_count INTEGER NOT NULL CHECK (dropped_cycle_count >= 0),
        dropped_record_count INTEGER NOT NULL CHECK (dropped_record_count >= 0),
        usage_availability TEXT NOT NULL CHECK (usage_availability IN ('available', 'unavailable')),
        input_tokens INTEGER CHECK (input_tokens IS NULL OR input_tokens >= 0),
        output_tokens INTEGER CHECK (output_tokens IS NULL OR output_tokens >= 0),
        total_tokens INTEGER CHECK (total_tokens IS NULL OR total_tokens >= 0),
        cost_availability TEXT NOT NULL CHECK (cost_availability IN ('available', 'unavailable')),
        cost_amount_minor INTEGER CHECK (cost_amount_minor IS NULL OR cost_amount_minor >= 0),
        cost_currency TEXT CHECK (cost_currency IS NULL OR length(cost_currency) = 3),
        cost_source TEXT CHECK (cost_source IS NULL OR length(cost_source) BETWEEN 1 AND 128),
        tool_terminal_counts_json TEXT NOT NULL CHECK (length(tool_terminal_counts_json) BETWEEN 2 AND 2048),
        finalized_at_unix INTEGER NOT NULL,
        CHECK (
            (usage_availability = 'unavailable'
                AND input_tokens IS NULL AND output_tokens IS NULL AND total_tokens IS NULL)
            OR (usage_availability = 'available'
                AND (input_tokens IS NOT NULL OR output_tokens IS NOT NULL OR total_tokens IS NOT NULL))
        ),
        CHECK (
            (input_tokens IS NULL OR output_tokens IS NULL OR total_tokens IS NULL
                OR total_tokens = input_tokens + output_tokens)
        ),
        CHECK (
            (cost_availability = 'unavailable'
                AND cost_amount_minor IS NULL AND cost_currency IS NULL AND cost_source IS NULL)
            OR (cost_availability = 'available'
                AND cost_amount_minor IS NOT NULL AND cost_currency IS NOT NULL AND cost_source IS NOT NULL)
        )
    )
    """,
    """
    CREATE INDEX agent_run_terminal_metrics_workspace
        ON agent_run_terminal_metrics(workspace_id, finalized_at_unix, agent_run_id)
    """,
    """
    CREATE TRIGGER agent_run_model_requests_workspace_guard_insert
    BEFORE INSERT ON agent_run_model_requests
    BEGIN
        SELECT CASE WHEN NOT EXISTS (
            SELECT 1 FROM agent_runs r
            JOIN turns t ON t.turn_id = r.turn_id
            JOIN sessions s ON s.session_id = t.session_id
            WHERE r.agent_run_id = NEW.agent_run_id
              AND r.session_id = s.session_id
              AND s.workspace_id = NEW.workspace_id
        ) THEN RAISE(ABORT, 'AgentRun observation workspace mismatch') END;
    END
    """,
    """
    CREATE TRIGGER agent_run_model_requests_workspace_guard_update
    BEFORE UPDATE OF workspace_id, agent_run_id ON agent_run_model_requests
    BEGIN
        SELECT CASE WHEN NOT EXISTS (
            SELECT 1 FROM agent_runs r
            JOIN turns t ON t.turn_id = r.turn_id
            JOIN sessions s ON s.session_id = t.session_id
            WHERE r.agent_run_id = NEW.agent_run_id
              AND r.session_id = s.session_id
              AND s.workspace_id = NEW.workspace_id
        ) THEN RAISE(ABORT, 'AgentRun observation workspace mismatch') END;
    END
    """,
    """
    CREATE TRIGGER agent_run_terminal_metrics_workspace_guard_insert
    BEFORE INSERT ON agent_run_terminal_metrics
    BEGIN
        SELECT CASE WHEN NOT EXISTS (
            SELECT 1 FROM agent_runs r
            JOIN turns t ON t.turn_id = r.turn_id
            JOIN sessions s ON s.session_id = t.session_id
            WHERE r.agent_run_id = NEW.agent_run_id
              AND r.session_id = s.session_id
              AND s.workspace_id = NEW.workspace_id
        ) THEN RAISE(ABORT, 'AgentRun metrics workspace mismatch') END;
    END
    """,
    """
    CREATE TRIGGER agent_run_terminal_metrics_workspace_guard_update
    BEFORE UPDATE OF workspace_id, agent_run_id ON agent_run_terminal_metrics
    BEGIN
        SELECT CASE WHEN NOT EXISTS (
            SELECT 1 FROM agent_runs r
            JOIN turns t ON t.turn_id = r.turn_id
            JOIN sessions s ON s.session_id = t.session_id
            WHERE r.agent_run_id = NEW.agent_run_id
              AND r.session_id = s.session_id
              AND s.workspace_id = NEW.workspace_id
        ) THEN RAISE(ABORT, 'AgentRun metrics workspace mismatch') END;
    END
    """,
)

__all__ = ["V17_NAME", "V17_STATEMENTS"]
