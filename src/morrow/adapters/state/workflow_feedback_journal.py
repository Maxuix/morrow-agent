"""One bounded repository on the existing operational transaction backend."""

from morrow.core.execution import StaleRowVersionError
from morrow.core.workflows.feedback import (
    WorkflowEvaluation,
    WorkflowFeedback,
    WorkflowPolicyCandidate,
)

TABLES = {
    WorkflowFeedback: ("workflow_feedback", "feedback_id"),
    WorkflowPolicyCandidate: ("workflow_policy_candidates", "candidate_id"),
    WorkflowEvaluation: ("workflow_evaluations", "evaluation_id"),
}


class SqliteWorkflowFeedbackJournal:
    def __init__(self, backend):
        self.backend = backend

    def get(self, model, workspace_id, record_id):
        table, _ = TABLES[model]
        row = self.backend.read_one(
            f"SELECT body_json FROM {table} WHERE workspace_id=? AND record_id=?",
            (workspace_id, record_id),
        )
        return model.model_validate_json(row[0]) if row else None

    def list(self, model, workspace_id):
        table, _ = TABLES[model]
        return tuple(
            model.model_validate_json(row[0])
            for row in self.backend.read_all(
                f"SELECT body_json FROM {table} WHERE workspace_id=? ORDER BY rowid",
                (workspace_id,),
            )
        )

    def put(self, value, *, expected_row_version=None):
        model = type(value)
        table, field = TABLES[model]
        value = model.model_validate_json(value.model_dump_json())
        identity = getattr(value, field)

        def work():
            old = self.get(model, value.workspace_id, identity)
            if old == value:
                return old
            if old is not None:
                if model is not WorkflowPolicyCandidate or expected_row_version is None:
                    raise ValueError("feedback/evaluation evidence is immutable")
                if old.row_version != expected_row_version:
                    raise StaleRowVersionError("stale Workflow policy candidate")
                if value.row_version != old.row_version + 1 or old.status in {
                    "accepted",
                    "rejected",
                }:
                    raise ValueError("invalid Workflow candidate transition")
                self.backend.executor().execute(
                    f"UPDATE {table} SET body_json=? WHERE workspace_id=? AND record_id=?",
                    (value.model_dump_json(), value.workspace_id, identity),
                )
            else:
                if expected_row_version is not None:
                    raise StaleRowVersionError("missing Workflow policy candidate")
                self.backend.executor().execute(
                    f"INSERT INTO {table} VALUES(?,?,?)",
                    (identity, value.workspace_id, value.model_dump_json()),
                )
            return value

        return self.backend.transact(work)
