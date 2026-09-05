"""Allow compaction requests in the existing admission/usage ledger."""

from morrow.adapters.state.migrations_v17_observability import V17_STATEMENTS
from morrow.adapters.state.migrations_v19_request_evidence import V19_STATEMENTS
from morrow.adapters.state.migrations_v20_long_horizon_observability import V20_STATEMENTS

V30_NAME = "compaction_request_accounting"

# SQLite cannot widen a CHECK constraint in place. Reuse the immutable historical DDL so
# rebuilding preserves every old column and row. The existing normalized overflow error also
# needs to be representable; otherwise its settlement is lost to the older SQL CHECK.
V30_STATEMENTS = (
    "ALTER TABLE agent_run_model_requests RENAME TO agent_run_model_requests_v29",
    V17_STATEMENTS[0].replace(
        "'invalid_response', 'internal'", "'invalid_response', 'context_overflow', 'internal'"
    ),
    *(
        s.replace("'agent', 'outcome_intent'", "'agent', 'outcome_intent', 'compaction'")
        for s in V19_STATEMENTS
    ),
    *(s for s in V20_STATEMENTS if "ALTER TABLE agent_run_model_requests" in s),
    "INSERT INTO agent_run_model_requests SELECT * FROM agent_run_model_requests_v29",
    "DROP TABLE agent_run_model_requests_v29",
    *(
        s
        for s in V17_STATEMENTS[1:]
        if "CREATE INDEX agent_run_model_requests" in s
        or "CREATE TRIGGER agent_run_model_requests" in s
    ),
)
