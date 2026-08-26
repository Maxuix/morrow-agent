"""Operational Store v18 additive observability fields for completion truth."""

from __future__ import annotations

V18_NAME = "agent_run_completion_truth"

V18_STATEMENTS = (
    """
    DROP TRIGGER IF EXISTS agent_run_terminal_metrics_workspace_guard_insert
    """,
    """
    DROP TRIGGER IF EXISTS agent_run_terminal_metrics_workspace_guard_update
    """,
    """
    DROP INDEX IF EXISTS agent_run_terminal_metrics_workspace
    """,
    """
    ALTER TABLE agent_run_terminal_metrics RENAME TO agent_run_terminal_metrics_v17
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
                'model_call_limit', 'tool_call_limit', 'run_timeout', 'loop_detected',
                'missing_required_change', 'validation_missing', 'validation_failed',
                'unexpected_workspace_change', 'forbidden_workspace_change', 'unresolved_tool',
                'known_failure', 'verifier_failed', 'completion_inconclusive', 'internal'
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
        validation_outcome TEXT NOT NULL DEFAULT 'not_run'
            CHECK (validation_outcome IN ('not_run', 'passed', 'failed', 'timeout', 'cancelled')),
        completion_outcome TEXT NOT NULL DEFAULT 'not_run'
            CHECK (completion_outcome IN ('not_run', 'passed', 'rejected', 'inconclusive')),
        completion_basis TEXT NOT NULL DEFAULT 'not_completed'
            CHECK (completion_basis IN (
                'verified', 'runtime_evidence_without_verifier', 'not_completed', 'inconclusive'
            )),
        completion_reason_code TEXT,
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
        ),
        CHECK (
            (finish_reason IN ('stop', 'cancelled') AND stop_code IS NULL)
            OR (finish_reason = 'error' AND stop_code IS NOT NULL)
        )
    )
    """,
    """
    INSERT INTO agent_run_terminal_metrics(
        agent_run_id, workspace_id, finish_reason, stop_code, model_attempts, retry_count,
        tool_rounds, tool_calls, max_estimated_request_chars, request_char_budget,
        cleared_cycle_count, dropped_turn_count, dropped_cycle_count, dropped_record_count,
        usage_availability, input_tokens, output_tokens, total_tokens, cost_availability,
        cost_amount_minor, cost_currency, cost_source, tool_terminal_counts_json,
        validation_outcome, completion_outcome, completion_basis, completion_reason_code,
        finalized_at_unix
    )
    SELECT agent_run_id, workspace_id, finish_reason, stop_code, model_attempts, retry_count,
        tool_rounds, tool_calls, max_estimated_request_chars, request_char_budget,
        cleared_cycle_count, dropped_turn_count, dropped_cycle_count, dropped_record_count,
        usage_availability, input_tokens, output_tokens, total_tokens, cost_availability,
        cost_amount_minor, cost_currency, cost_source, tool_terminal_counts_json,
        'not_run', 'not_run', 'not_completed', NULL, finalized_at_unix
    FROM agent_run_terminal_metrics_v17
    """,
    """
    DROP TABLE agent_run_terminal_metrics_v17
    """,
    """
    CREATE INDEX agent_run_terminal_metrics_workspace
        ON agent_run_terminal_metrics(workspace_id, finalized_at_unix, agent_run_id)
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

__all__ = ["V18_NAME", "V18_STATEMENTS"]
