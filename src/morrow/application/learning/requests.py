"""Application orchestration for automatic and explicit Learning Reviews."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from morrow.core.application import ApplicationError, ApplicationErrorCode
from morrow.core.domain import (
    DurableTaskOutcome,
    TaskRunStatus,
    canonical_json_bytes,
    sha256_digest,
)
from morrow.core.learning import (
    LEARNING_REVIEW_ID_PREFIX,
    LearningMode,
    LearningPolicy,
    LearningReview,
    LearningReviewStatus,
    LearningReviewTrigger,
)
from morrow.core.ports import IdSource


def _utc(clock: Callable[[], datetime]) -> datetime:
    value = clock()
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


@dataclass(frozen=True)
class LearningReviewRequestDecision:
    review: LearningReview | None
    reason: str | None = None


class LearningReviewRequestService:
    """Own the Review identity/version rules without touching TaskService."""

    def __init__(
        self,
        *,
        journal,
        workspace_id: str,
        id_source: IdSource,
        clock: Callable[[], datetime],
    ) -> None:
        self.journal = journal
        self.workspace_id = workspace_id
        self.id_source = id_source
        self.clock = clock

    def ensure_for_accepted_outcome(
        self,
        txn,
        outcome: DurableTaskOutcome,
    ) -> LearningReviewRequestDecision:
        """Return the existing automatic Review or create exactly version one."""

        if outcome.workspace_id != self.workspace_id:
            raise ApplicationError(
                ApplicationErrorCode.CROSS_WORKSPACE,
                "TaskOutcome is outside the workspace",
            )
        if outcome.task_status is not TaskRunStatus.ACCEPTED:
            return LearningReviewRequestDecision(None, "outcome_not_accepted")
        policy = txn.get_effective_learning_policy(self.workspace_id)
        if policy.mode is LearningMode.OFF:
            return LearningReviewRequestDecision(None, "learning_policy_off")
        if outcome.unresolved_items:
            return LearningReviewRequestDecision(None, "outcome_contains_unresolved_facts")

        existing = self.reviews_for_outcome(txn, outcome.outcome_id)
        if existing:
            return LearningReviewRequestDecision(existing[-1])
        return LearningReviewRequestDecision(
            self._create_review(
                txn,
                outcome,
                policy=policy,
                trigger=LearningReviewTrigger.TASK_ACCEPTED,
                review_version=1,
            )
        )

    def request_explicit(
        self,
        txn,
        *,
        outcome_id: str,
        expected_latest_version: int | None,
    ) -> LearningReview:
        outcome = txn.get_task_outcome(self.workspace_id, outcome_id)
        if outcome is None:
            raise ApplicationError(ApplicationErrorCode.NOT_FOUND, "TaskOutcome is missing")
        if outcome.task_status is not TaskRunStatus.ACCEPTED:
            raise ApplicationError(
                ApplicationErrorCode.INVALID,
                "only an accepted TaskOutcome can be reviewed",
            )
        if expected_latest_version is not None and (
            isinstance(expected_latest_version, bool)
            or not isinstance(expected_latest_version, int)
            or expected_latest_version < 0
        ):
            raise ApplicationError(
                ApplicationErrorCode.INVALID,
                "latest Review version is invalid",
            )

        existing = self.reviews_for_outcome(txn, outcome_id)
        latest = existing[-1] if existing else None
        latest_version = latest.review_version if latest is not None else 0
        if expected_latest_version not in {None, latest_version}:
            raise ApplicationError(ApplicationErrorCode.STALE, "latest Review version is stale")
        if latest is not None:
            superseded = latest.model_copy(
                update={
                    "status": LearningReviewStatus.SUPERSEDED,
                    "lease_id": None,
                    "lease_expires_at": None,
                    "completed_at": _utc(self.clock),
                    "failure_code": None,
                    "row_version": latest.row_version + 1,
                }
            )
            txn.save_learning_review(
                self.workspace_id,
                superseded,
                expected_row_version=latest.row_version,
            )

        policy = txn.get_effective_learning_policy(self.workspace_id)
        return self._create_review(
            txn,
            outcome,
            policy=policy,
            trigger=LearningReviewTrigger.EXPLICIT_REQUEST,
            review_version=latest_version + 1,
            supersedes_review_id=latest.review_id if latest is not None else None,
        )

    def reviews_for_outcome(self, txn, outcome_id: str) -> tuple[LearningReview, ...]:
        return txn.list_learning_reviews(
            self.workspace_id,
            task_outcome_id=outcome_id,
            limit=500,
        )

    def _create_review(
        self,
        txn,
        outcome: DurableTaskOutcome,
        *,
        policy: LearningPolicy,
        trigger: LearningReviewTrigger,
        review_version: int,
        supersedes_review_id: str | None = None,
    ) -> LearningReview:
        snapshot = canonical_json_bytes(policy.model_dump(mode="json")).decode("utf-8")
        review = LearningReview(
            review_id=self.id_source.new_id(LEARNING_REVIEW_ID_PREFIX),
            workspace_id=self.workspace_id,
            task_run_id=outcome.task_run_id,
            task_outcome_id=outcome.outcome_id,
            review_version=review_version,
            trigger=trigger,
            policy_snapshot_json=snapshot,
            policy_digest=sha256_digest(snapshot),
            supersedes_review_id=supersedes_review_id,
            created_at=_utc(self.clock),
        )
        return txn.put_learning_review(self.workspace_id, review)


def policy_from_review(review: LearningReview) -> LearningPolicy:
    """Decode the immutable policy snapshot captured at request time."""

    try:
        value = json.loads(review.policy_snapshot_json)
        return LearningPolicy.model_validate(value)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ApplicationError(
            ApplicationErrorCode.NEEDS_RECOVERY,
            "Learning Review policy snapshot is invalid",
        ) from exc


__all__ = [
    "LearningReviewRequestDecision",
    "LearningReviewRequestService",
    "policy_from_review",
]
