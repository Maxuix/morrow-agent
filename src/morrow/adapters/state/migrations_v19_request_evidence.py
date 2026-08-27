"""Schema v19: per-request purpose, prompt evidence, and resolved completion intent."""

V19_NAME = "agent_run_request_evidence"
V19_STATEMENTS = (
    """
    ALTER TABLE agent_run_model_requests
    ADD COLUMN purpose TEXT NOT NULL DEFAULT 'agent'
        CHECK (purpose IN ('agent', 'outcome_intent'))
    """,
    """
    ALTER TABLE agent_run_model_requests
    ADD COLUMN prompt_evidence_json TEXT
    """,
    """
    ALTER TABLE agent_run_model_requests
    ADD COLUMN resolved_outcome_contract_json TEXT
    """,
)

__all__ = ["V19_NAME", "V19_STATEMENTS"]
