"""Learning review and evidence repository mixin for the v10 SQLite adapter."""

from __future__ import annotations

from datetime import datetime

from morrow.adapters.state.learning_journal import (
    _EVIDENCE_COLUMNS,
    _EVIDENCE_COLUMNS_QUALIFIED,
    _REVIEW_COLUMNS,
    _evidence_from_row,
    _json_text,
    _missing,
    _optional_unix,
    _review_from_row,
    _stale,
    _unix,
    _workspace_error,
)
from morrow.adapters.state.transaction import SqliteJournalBackend
from morrow.core.learning import (
    LEARNING_MAX_REVIEW_ATTEMPTS,
    LearningEvidence,
    LearningReview,
    LearningReviewStatus,
)
from morrow.core.store import StorageError, StorageErrorCode


class SqliteLearningReviewMixin:
    """Persist review lifecycle state and its bounded evidence records."""

    backend: SqliteJournalBackend

    def get_learning_review(self, workspace_id: str, review_id: str) -> LearningReview | None:
        row = self.backend.read_one(
            f"SELECT {_REVIEW_COLUMNS} FROM learning_reviews WHERE review_id = ?",
            (review_id,),
        )
        if row is None:
            return None
        if str(row[1]) != workspace_id:
            raise _workspace_error("review")
        return _review_from_row(row)

    def list_learning_reviews(
        self,
        workspace_id: str,
        *,
        status: LearningReviewStatus | None = None,
        task_outcome_id: str | None = None,
        limit: int = 100,
    ) -> tuple[LearningReview, ...]:
        if not 1 <= limit <= 500:
            raise StorageError(StorageErrorCode.UNAVAILABLE, "learning review page is invalid")
        sql = f"SELECT {_REVIEW_COLUMNS} FROM learning_reviews WHERE workspace_id = ?"
        parameters: list[object] = [workspace_id]
        if status is not None:
            sql += " AND status = ?"
            parameters.append(status.value)
        if task_outcome_id is not None:
            sql += " AND task_outcome_id = ?"
            parameters.append(task_outcome_id)
        sql += " ORDER BY created_at_unix ASC, review_version ASC, review_id ASC LIMIT ?"
        parameters.append(limit)
        return tuple(_review_from_row(row) for row in self.backend.read_all(sql, tuple(parameters)))

    def count_learning_reviews(
        self,
        workspace_id: str,
        *,
        status: LearningReviewStatus | None = None,
    ) -> int:
        sql = "SELECT COUNT(*) FROM learning_reviews WHERE workspace_id = ?"
        parameters: list[object] = [workspace_id]
        if status is not None:
            sql += " AND status = ?"
            parameters.append(status.value)
        row = self.backend.read_one(sql, tuple(parameters))
        if row is None:
            raise StorageError(
                StorageErrorCode.UNAVAILABLE, "learning review count could not be read"
            )
        return int(row[0])

    def put_learning_review(self, workspace_id: str, review: LearningReview) -> LearningReview:
        if review.workspace_id != workspace_id:
            raise _workspace_error("review")

        def work() -> LearningReview:
            if self.get_learning_review(workspace_id, review.review_id) is not None:
                raise StorageError(StorageErrorCode.UNAVAILABLE, "learning review already exists")
            self._assert_subject_scope(workspace_id, review.task_run_id, review.task_outcome_id)
            self.backend.executor().execute(
                f"INSERT INTO learning_reviews({_REVIEW_COLUMNS}) VALUES ({','.join('?' for _ in range(23))})",
                self._review_values(review),
            )
            loaded = self.get_learning_review(workspace_id, review.review_id)
            if loaded is None:
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE, "learning review could not be read"
                )
            return loaded

        return self.backend.transact(work)

    def _assert_subject_scope(self, workspace_id: str, task_run_id: str, outcome_id: str) -> None:
        task = self.backend.read_one(
            "SELECT workspace_id FROM task_runs WHERE task_run_id = ?", (task_run_id,)
        )
        outcome = self.backend.read_one(
            "SELECT workspace_id FROM task_outcomes WHERE outcome_id = ?", (outcome_id,)
        )
        if task is None or outcome is None:
            raise _missing("review subject")
        if str(task[0]) != workspace_id or str(outcome[0]) != workspace_id:
            raise _workspace_error("review subject")

    @staticmethod
    def _review_values(review: LearningReview) -> tuple[object, ...]:
        snapshot = _json_text(review.policy_snapshot_json, label="review policy snapshot")
        return (
            review.review_id,
            review.workspace_id,
            review.task_run_id,
            review.task_outcome_id,
            review.review_version,
            review.trigger.value,
            review.status.value,
            snapshot,
            len(snapshot.encode("utf-8")),
            review.policy_digest,
            review.reviewer_provider_id,
            review.reviewer_model_id,
            review.reviewer_prompt_version,
            review.reviewer_schema_version,
            review.supersedes_review_id,
            review.lease_id,
            _optional_unix(review.lease_expires_at),
            review.attempt_count,
            review.row_version,
            _unix(review.created_at),
            _optional_unix(review.started_at),
            _optional_unix(review.completed_at),
            review.failure_code.value if review.failure_code is not None else None,
        )

    def save_learning_review(
        self,
        workspace_id: str,
        review: LearningReview,
        *,
        expected_row_version: int,
    ) -> LearningReview:
        if review.workspace_id != workspace_id:
            raise _workspace_error("review")

        def work() -> LearningReview:
            existing = self.get_learning_review(workspace_id, review.review_id)
            if existing is None:
                raise _missing("review")
            if (
                existing.row_version != expected_row_version
                or review.row_version != expected_row_version + 1
            ):
                raise _stale("review")
            immutable = (
                "workspace_id",
                "task_run_id",
                "task_outcome_id",
                "review_version",
                "trigger",
                "policy_snapshot_json",
                "policy_digest",
                "supersedes_review_id",
                "created_at",
            )
            if any(getattr(existing, name) != getattr(review, name) for name in immutable):
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE, "learning review identity is immutable"
                )
            self.backend.executor().execute(
                """
                UPDATE learning_reviews
                SET status = ?, reviewer_provider_id = ?, reviewer_model_id = ?,
                    reviewer_prompt_version = ?, reviewer_schema_version = ?, lease_id = ?,
                    lease_expires_at_unix = ?, attempt_count = ?, row_version = ?,
                    started_at_unix = ?, completed_at_unix = ?, failure_code = ?
                WHERE review_id = ? AND workspace_id = ? AND row_version = ?
                """,
                (
                    review.status.value,
                    review.reviewer_provider_id,
                    review.reviewer_model_id,
                    review.reviewer_prompt_version,
                    review.reviewer_schema_version,
                    review.lease_id,
                    _optional_unix(review.lease_expires_at),
                    review.attempt_count,
                    review.row_version,
                    _optional_unix(review.started_at),
                    _optional_unix(review.completed_at),
                    review.failure_code.value if review.failure_code is not None else None,
                    review.review_id,
                    workspace_id,
                    expected_row_version,
                ),
            )
            loaded = self.get_learning_review(workspace_id, review.review_id)
            if loaded is None or loaded.row_version != review.row_version:
                raise _stale("review")
            return loaded

        return self.backend.transact(work)

    def claim_learning_review(
        self,
        workspace_id: str,
        review_id: str,
        *,
        expected_row_version: int,
        lease_id: str,
        lease_expires_at: datetime,
        started_at: datetime,
    ) -> LearningReview:
        def work() -> LearningReview:
            existing = self.get_learning_review(workspace_id, review_id)
            if existing is None:
                raise _missing("review")
            now = self.backend.now()
            if existing.row_version != expected_row_version:
                raise _stale("review")
            if existing.status is LearningReviewStatus.RUNNING and (
                existing.lease_expires_at is not None and existing.lease_expires_at > now
            ):
                raise StorageError(StorageErrorCode.BUSY, "learning review is leased")
            if existing.status in {
                LearningReviewStatus.COMPLETED,
                LearningReviewStatus.SUPERSEDED,
            }:
                raise StorageError(StorageErrorCode.UNAVAILABLE, "learning review is not claimable")
            if existing.attempt_count >= LEARNING_MAX_REVIEW_ATTEMPTS:
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE,
                    "learning review retry limit is exhausted",
                )
            if lease_expires_at <= started_at:
                raise StorageError(StorageErrorCode.UNAVAILABLE, "learning review lease is invalid")
            claimed = LearningReview.model_validate(
                {
                    **existing.model_dump(),
                    "status": LearningReviewStatus.RUNNING,
                    "lease_id": lease_id,
                    "lease_expires_at": lease_expires_at,
                    "attempt_count": existing.attempt_count + 1,
                    "row_version": existing.row_version + 1,
                    "started_at": started_at,
                    "completed_at": None,
                    "failure_code": None,
                }
            )
            return self._save_learning_review_in_txn(
                workspace_id, claimed, expected_row_version=expected_row_version
            )

        return self.backend.transact(work)

    def _save_learning_review_in_txn(
        self, workspace_id: str, review: LearningReview, *, expected_row_version: int
    ) -> LearningReview:
        existing = self.get_learning_review(workspace_id, review.review_id)
        if existing is None:
            raise _missing("review")
        if (
            existing.row_version != expected_row_version
            or review.row_version != expected_row_version + 1
        ):
            raise _stale("review")
        self.backend.executor().execute(
            """
            UPDATE learning_reviews
            SET status = ?, lease_id = ?, lease_expires_at_unix = ?, attempt_count = ?,
                row_version = ?, started_at_unix = ?, completed_at_unix = ?, failure_code = ?
            WHERE review_id = ? AND workspace_id = ? AND row_version = ?
            """,
            (
                review.status.value,
                review.lease_id,
                _optional_unix(review.lease_expires_at),
                review.attempt_count,
                review.row_version,
                _optional_unix(review.started_at),
                _optional_unix(review.completed_at),
                review.failure_code.value if review.failure_code is not None else None,
                review.review_id,
                workspace_id,
                expected_row_version,
            ),
        )
        loaded = self.get_learning_review(workspace_id, review.review_id)
        if loaded is None:
            raise _stale("review")
        return loaded

    def get_learning_evidence(self, workspace_id: str, evidence_id: str) -> LearningEvidence | None:
        row = self.backend.read_one(
            f"SELECT {_EVIDENCE_COLUMNS} FROM learning_evidence WHERE evidence_id = ?",
            (evidence_id,),
        )
        if row is None:
            return None
        if str(row[1]) != workspace_id:
            raise _workspace_error("evidence")
        return _evidence_from_row(row)

    def list_learning_evidence(
        self,
        workspace_id: str,
        *,
        review_id: str | None = None,
        task_run_id: str | None = None,
        limit: int = 100,
    ) -> tuple[LearningEvidence, ...]:
        if not 1 <= limit <= 500:
            raise StorageError(StorageErrorCode.UNAVAILABLE, "learning evidence page is invalid")
        sql = f"SELECT {_EVIDENCE_COLUMNS} FROM learning_evidence WHERE workspace_id = ?"
        parameters: list[object] = [workspace_id]
        if review_id is not None:
            sql += " AND origin_review_id = ?"
            parameters.append(review_id)
        if task_run_id is not None:
            sql += " AND task_run_id = ?"
            parameters.append(task_run_id)
        sql += " ORDER BY created_at_unix ASC, evidence_id ASC LIMIT ?"
        parameters.append(limit)
        return tuple(
            _evidence_from_row(row) for row in self.backend.read_all(sql, tuple(parameters))
        )

    def count_learning_evidence(
        self,
        workspace_id: str,
        *,
        review_id: str | None = None,
        task_run_id: str | None = None,
    ) -> int:
        sql = "SELECT COUNT(*) FROM learning_evidence WHERE workspace_id = ?"
        parameters: list[object] = [workspace_id]
        if review_id is not None:
            sql += " AND origin_review_id = ?"
            parameters.append(review_id)
        if task_run_id is not None:
            sql += " AND task_run_id = ?"
            parameters.append(task_run_id)
        row = self.backend.read_one(sql, tuple(parameters))
        if row is None:
            raise StorageError(
                StorageErrorCode.UNAVAILABLE, "learning evidence count could not be read"
            )
        return int(row[0])

    def put_learning_evidence(
        self, workspace_id: str, evidence: LearningEvidence
    ) -> LearningEvidence:
        if evidence.workspace_id != workspace_id:
            raise _workspace_error("evidence")

        def work() -> LearningEvidence:
            if self.get_learning_evidence(workspace_id, evidence.evidence_id) is not None:
                raise StorageError(StorageErrorCode.UNAVAILABLE, "learning evidence already exists")
            review = self.get_learning_review(workspace_id, evidence.origin_review_id)
            if review is None:
                raise _missing("evidence review")
            count = self.backend.read_one(
                "SELECT COUNT(*) FROM learning_evidence WHERE origin_review_id = ?",
                (evidence.origin_review_id,),
            )
            policy = self.get_effective_learning_policy(workspace_id)
            if count is not None and int(count[0]) >= policy.max_evidence_per_review:
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE, "learning review evidence budget is exhausted"
                )
            task = self.backend.read_one(
                "SELECT workspace_id FROM task_runs WHERE task_run_id = ?", (evidence.task_run_id,)
            )
            if task is None:
                raise _missing("evidence task")
            if str(task[0]) != workspace_id:
                raise _workspace_error("evidence task")
            self.backend.executor().execute(
                f"INSERT INTO learning_evidence({_EVIDENCE_COLUMNS}) VALUES ({','.join('?' for _ in range(18))})",
                self._evidence_values(evidence),
            )
            loaded = self.get_learning_evidence(workspace_id, evidence.evidence_id)
            if loaded is None:
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE, "learning evidence could not be read"
                )
            return loaded

        return self.backend.transact(work)

    @staticmethod
    def _evidence_values(evidence: LearningEvidence) -> tuple[object, ...]:
        excerpt_bytes = (
            len(evidence.excerpt_redacted.encode("utf-8"))
            if evidence.excerpt_redacted is not None
            else 0
        )
        return (
            evidence.evidence_id,
            evidence.workspace_id,
            evidence.origin_review_id,
            evidence.task_run_id,
            evidence.source_kind.value,
            evidence.source_id,
            evidence.source_pointer or "",
            evidence.actor.value,
            evidence.authority.value,
            evidence.explicitness.value,
            evidence.polarity.value,
            evidence.scope_hint.value if evidence.scope_hint is not None else None,
            evidence.excerpt_redacted,
            excerpt_bytes,
            evidence.content_digest,
            evidence.safety_rejection_code.value if evidence.safety_rejection_code else None,
            _unix(evidence.observed_at),
            _unix(evidence.created_at),
        )

    def link_learning_review_evidence(
        self, workspace_id: str, review_id: str, evidence_id: str
    ) -> None:
        def work() -> None:
            review = self.get_learning_review(workspace_id, review_id)
            evidence = self.get_learning_evidence(workspace_id, evidence_id)
            if review is None or evidence is None:
                raise _missing("review evidence link")
            if evidence.origin_review_id != review_id:
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE,
                    "review evidence must belong to its origin review",
                )
            existing = self.backend.read_one(
                "SELECT 1 FROM learning_review_evidence WHERE review_id = ? AND evidence_id = ?",
                (review_id, evidence_id),
            )
            if existing is not None:
                return
            count = self.backend.read_one(
                "SELECT COUNT(*) FROM learning_review_evidence WHERE review_id = ?",
                (review_id,),
            )
            policy = self.get_effective_learning_policy(workspace_id)
            if count is not None and int(count[0]) >= policy.max_evidence_per_review:
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE, "learning review evidence budget is exhausted"
                )
            self.backend.executor().execute(
                "INSERT INTO learning_review_evidence(workspace_id, review_id, evidence_id) VALUES (?, ?, ?)",
                (workspace_id, review_id, evidence_id),
            )

        self.backend.transact(work)

    def list_learning_review_evidence(
        self, workspace_id: str, review_id: str
    ) -> tuple[LearningEvidence, ...]:
        rows = self.backend.read_all(
            f"SELECT {_EVIDENCE_COLUMNS_QUALIFIED} FROM learning_evidence e "
            "JOIN learning_review_evidence l ON l.evidence_id = e.evidence_id "
            "WHERE l.workspace_id = ? AND l.review_id = ? "
            "ORDER BY e.created_at_unix ASC, e.evidence_id ASC",
            (workspace_id, review_id),
        )
        return tuple(_evidence_from_row(row) for row in rows)
