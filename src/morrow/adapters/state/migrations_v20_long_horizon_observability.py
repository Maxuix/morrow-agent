"""Operational Store v20 bounded long-horizon accounting evidence."""

from __future__ import annotations

V20_NAME = "agent_run_long_horizon_observability"

V20_STATEMENTS = (
    """
    ALTER TABLE agent_run_model_requests
    ADD COLUMN policy_schema_version INTEGER
        CHECK (policy_schema_version IS NULL OR policy_schema_version IN (1, 2))
    """,
    """
    ALTER TABLE agent_run_model_requests
    ADD COLUMN estimated_context_tokens INTEGER
        CHECK (estimated_context_tokens IS NULL OR estimated_context_tokens >= 0)
    """,
    """
    ALTER TABLE agent_run_model_requests
    ADD COLUMN context_window_tokens INTEGER
        CHECK (context_window_tokens IS NULL OR context_window_tokens > 0)
    """,
    """
    ALTER TABLE agent_run_model_requests
    ADD COLUMN reserve_tokens INTEGER
        CHECK (reserve_tokens IS NULL OR reserve_tokens > 0)
    """,
    """
    ALTER TABLE agent_run_model_requests
    ADD COLUMN keep_recent_tokens INTEGER
        CHECK (keep_recent_tokens IS NULL OR keep_recent_tokens > 0)
    """,
    """
    ALTER TABLE agent_run_model_requests
    ADD COLUMN accounting_basis TEXT
        CHECK (accounting_basis IS NULL OR accounting_basis IN ('provider_usage', 'pi_estimator'))
    """,
    """
    ALTER TABLE agent_run_model_requests
    ADD COLUMN compaction_required INTEGER
        CHECK (compaction_required IS NULL OR compaction_required IN (0, 1))
    """,
    """
    ALTER TABLE agent_run_terminal_metrics
    ADD COLUMN policy_schema_version INTEGER
        CHECK (policy_schema_version IS NULL OR policy_schema_version IN (1, 2))
    """,
    """
    ALTER TABLE agent_run_terminal_metrics
    ADD COLUMN max_context_tokens INTEGER
        CHECK (max_context_tokens IS NULL OR max_context_tokens >= 0)
    """,
    """
    ALTER TABLE agent_run_terminal_metrics
    ADD COLUMN last_context_tokens INTEGER
        CHECK (last_context_tokens IS NULL OR last_context_tokens >= 0)
    """,
    """
    ALTER TABLE agent_run_terminal_metrics
    ADD COLUMN context_window_tokens INTEGER
        CHECK (context_window_tokens IS NULL OR context_window_tokens > 0)
    """,
    """
    ALTER TABLE agent_run_terminal_metrics
    ADD COLUMN reserve_tokens INTEGER
        CHECK (reserve_tokens IS NULL OR reserve_tokens > 0)
    """,
    """
    ALTER TABLE agent_run_terminal_metrics
    ADD COLUMN keep_recent_tokens INTEGER
        CHECK (keep_recent_tokens IS NULL OR keep_recent_tokens > 0)
    """,
    """
    ALTER TABLE agent_run_terminal_metrics
    ADD COLUMN accounting_basis TEXT
        CHECK (accounting_basis IS NULL OR accounting_basis IN ('provider_usage', 'pi_estimator'))
    """,
    """
    ALTER TABLE agent_run_terminal_metrics
    ADD COLUMN compaction_count INTEGER NOT NULL DEFAULT 0
        CHECK (compaction_count >= 0)
    """,
    """
    ALTER TABLE agent_run_terminal_metrics
    ADD COLUMN overflow_recovery_count INTEGER NOT NULL DEFAULT 0
        CHECK (overflow_recovery_count >= 0)
    """,
)

__all__ = ["V20_NAME", "V20_STATEMENTS"]
