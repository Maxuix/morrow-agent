"""LearningPolicy repository mixin for the v10 SQLite adapter."""

from __future__ import annotations

from morrow.adapters.state.learning_journal import (
    _POLICY_COLUMNS,
    _policy_from_row,
    _stale,
    _unix,
    _workspace_error,
)
from morrow.adapters.state.transaction import SqliteJournalBackend
from morrow.core.learning import LearningPolicy
from morrow.core.store import StorageError, StorageErrorCode


class SqliteLearningPolicyMixin:
    backend: SqliteJournalBackend

    def get_learning_policy(self, workspace_id: str) -> LearningPolicy | None:
        row = self.backend.read_one(
            f"SELECT {_POLICY_COLUMNS} FROM learning_policies WHERE workspace_id = ?",
            (workspace_id,),
        )
        return None if row is None else _policy_from_row(row)

    def get_effective_learning_policy(self, workspace_id: str) -> LearningPolicy:
        return self.get_learning_policy(workspace_id) or LearningPolicy.default_for(
            workspace_id, now=self.backend.now()
        )

    def save_learning_policy(
        self,
        workspace_id: str,
        policy: LearningPolicy,
        *,
        expected_row_version: int | None,
    ) -> LearningPolicy:
        if policy.workspace_id != workspace_id:
            raise _workspace_error("policy")
        if policy.mode.value == "explicit_auto":
            raise StorageError(
                StorageErrorCode.UNAVAILABLE, "learning policy mode is not available"
            )

        def work() -> LearningPolicy:
            existing = self.get_learning_policy(workspace_id)
            executor = self.backend.executor()
            if existing is None:
                if expected_row_version not in {None, 0} or policy.row_version != 1:
                    raise _stale("policy")
                executor.execute(
                    "INSERT INTO learning_policies("
                    f"{_POLICY_COLUMNS}"
                    ") VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    self._policy_values(policy),
                )
            else:
                if expected_row_version != existing.row_version:
                    raise _stale("policy")
                if policy.row_version != existing.row_version + 1:
                    raise _stale("policy")
                if policy.created_at != existing.created_at:
                    raise StorageError(
                        StorageErrorCode.UNAVAILABLE, "learning policy identity is immutable"
                    )
                executor.execute(
                    """
                    UPDATE learning_policies
                    SET mode = ?, candidate_ttl_days = ?, max_candidates_per_review = ?,
                        max_evidence_per_review = ?, row_version = ?, updated_at_unix = ?
                    WHERE workspace_id = ? AND row_version = ?
                    """,
                    (
                        policy.mode.value,
                        policy.candidate_ttl_days,
                        policy.max_candidates_per_review,
                        policy.max_evidence_per_review,
                        policy.row_version,
                        _unix(policy.updated_at),
                        workspace_id,
                        existing.row_version,
                    ),
                )
            loaded = self.get_learning_policy(workspace_id)
            if loaded is None:
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE, "learning policy could not be read"
                )
            return loaded

        return self.backend.transact(work)

    @staticmethod
    def _policy_values(policy: LearningPolicy) -> tuple[object, ...]:
        return (
            policy.workspace_id,
            policy.mode.value,
            policy.candidate_ttl_days,
            policy.max_candidates_per_review,
            policy.max_evidence_per_review,
            policy.row_version,
            _unix(policy.created_at),
            _unix(policy.updated_at),
        )
