"""Narrow persistence and Reviewer contracts for Stage 5 Learning.

The contracts intentionally describe data access, not orchestration.  Implementations
may be SQLite-backed, but Core callers never receive a SQLite connection or a parent
journal facade.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, TypeVar

from pydantic import Field, field_validator, model_validator

from morrow.core.domain import DurableTaskOutcome, validate_prefixed_id
from morrow.core.journal import TransactionalJournalPort
from morrow.core.learning import (
    LEARNING_MAX_CANDIDATES_PER_REVIEW,
    LEARNING_WORKSPACE_ID_PREFIX,
    CandidateDraftBatch,
    LearningCandidate,
    LearningCandidateStatus,
    LearningCandidateType,
    LearningEvidence,
    LearningPolicy,
    LearningReview,
    LearningReviewStatus,
    LearningScope,
    LearningSuppression,
)
from morrow.core.models import ModelErrorCode, ModelRef, ProtocolModel

T = TypeVar("T")

LEARNING_CONTEXT_MAX_RENDERED_CHARS = 16 * 1024


class LearningActiveSummary(ProtocolModel):
    """Small, attributable Active-state summary permitted in Reviewer context."""

    semantic_key: str
    scope: LearningScope
    value_summary: str
    value_digest: str


class LearningContext(ProtocolModel):
    """Bounded Reviewer input; never a full Session or ConversationLog projection."""

    workspace_id: str
    task_outcome: DurableTaskOutcome
    evidence: tuple[LearningEvidence, ...] = ()
    active_summaries: tuple[LearningActiveSummary, ...] = ()
    suppressions: tuple[LearningSuppression, ...] = ()
    policy: LearningPolicy
    candidate_budget: int = Field(ge=0, le=LEARNING_MAX_CANDIDATES_PER_REVIEW)
    rendered_char_budget: int = Field(
        default=LEARNING_CONTEXT_MAX_RENDERED_CHARS,
        ge=256,
        le=LEARNING_CONTEXT_MAX_RENDERED_CHARS,
    )

    _valid_workspace = field_validator("workspace_id")(
        lambda value: validate_prefixed_id(value, LEARNING_WORKSPACE_ID_PREFIX)
    )

    @model_validator(mode="after")
    def scoped_and_bounded(self) -> LearningContext:
        if self.task_outcome.workspace_id != self.workspace_id:
            raise ValueError("learning context task outcome is outside the workspace")
        if self.policy.workspace_id != self.workspace_id:
            raise ValueError("learning context policy is outside the workspace")
        if self.candidate_budget > self.policy.max_candidates_per_review:
            raise ValueError("learning context candidate budget exceeds policy")
        if len(self.evidence) > self.policy.max_evidence_per_review:
            raise ValueError("learning context contains too much evidence")
        if any(item.workspace_id != self.workspace_id for item in self.evidence):
            raise ValueError("learning context evidence is outside the workspace")
        if any(item.workspace_id != self.workspace_id for item in self.suppressions):
            raise ValueError("learning context suppressions are outside the workspace")
        if len(self.model_dump_json().encode("utf-8")) > self.rendered_char_budget:
            raise ValueError("learning context exceeds its rendered character budget")
        return self


class LearningReviewerError(RuntimeError):
    """Sanitized, typed failure raised by a production Reviewer adapter."""

    def __init__(self, code: ModelErrorCode, message: str, *, category: str) -> None:
        super().__init__(message)
        self.code = code
        self.category = category


class LearningJournalPort(TransactionalJournalPort, Protocol):
    """The v10 Learning repository surface over the shared transaction backend."""

    def get_learning_policy(self, workspace_id: str) -> LearningPolicy | None: ...

    def get_effective_learning_policy(self, workspace_id: str) -> LearningPolicy: ...

    def save_learning_policy(
        self,
        workspace_id: str,
        policy: LearningPolicy,
        *,
        expected_row_version: int | None,
    ) -> LearningPolicy: ...

    def put_learning_review(self, workspace_id: str, review: LearningReview) -> LearningReview: ...

    def get_learning_review(self, workspace_id: str, review_id: str) -> LearningReview | None: ...

    def list_learning_reviews(
        self,
        workspace_id: str,
        *,
        status: LearningReviewStatus | None = None,
        task_outcome_id: str | None = None,
        limit: int = 100,
    ) -> tuple[LearningReview, ...]: ...

    def count_learning_reviews(
        self,
        workspace_id: str,
        *,
        status: LearningReviewStatus | None = None,
    ) -> int: ...

    def save_learning_review(
        self,
        workspace_id: str,
        review: LearningReview,
        *,
        expected_row_version: int,
    ) -> LearningReview: ...

    def claim_learning_review(
        self,
        workspace_id: str,
        review_id: str,
        *,
        expected_row_version: int,
        lease_id: str,
        lease_expires_at: datetime,
        started_at: datetime,
    ) -> LearningReview: ...

    def put_learning_evidence(
        self, workspace_id: str, evidence: LearningEvidence
    ) -> LearningEvidence: ...

    def get_learning_evidence(
        self, workspace_id: str, evidence_id: str
    ) -> LearningEvidence | None: ...

    def list_learning_evidence(
        self,
        workspace_id: str,
        *,
        review_id: str | None = None,
        task_run_id: str | None = None,
        limit: int = 100,
    ) -> tuple[LearningEvidence, ...]: ...

    def count_learning_evidence(
        self,
        workspace_id: str,
        *,
        review_id: str | None = None,
        task_run_id: str | None = None,
    ) -> int: ...

    def link_learning_review_evidence(
        self, workspace_id: str, review_id: str, evidence_id: str
    ) -> None: ...

    def list_learning_review_evidence(
        self, workspace_id: str, review_id: str
    ) -> tuple[LearningEvidence, ...]: ...

    def put_learning_candidate(
        self, workspace_id: str, candidate: LearningCandidate
    ) -> LearningCandidate: ...

    def get_learning_candidate(
        self, workspace_id: str, candidate_id: str
    ) -> LearningCandidate | None: ...

    def list_learning_candidates(
        self,
        workspace_id: str,
        *,
        status: LearningCandidateStatus | None = None,
        candidate_type: LearningCandidateType | None = None,
        origin_review_id: str | None = None,
        fingerprint: str | None = None,
        semantic_key: str | None = None,
        expires_before: datetime | None = None,
        limit: int = 100,
    ) -> tuple[LearningCandidate, ...]: ...

    def count_learning_candidates(
        self,
        workspace_id: str,
        *,
        status: LearningCandidateStatus | None = None,
        candidate_type: LearningCandidateType | None = None,
        origin_review_id: str | None = None,
    ) -> int: ...

    def save_learning_candidate(
        self,
        workspace_id: str,
        candidate: LearningCandidate,
        *,
        expected_row_version: int,
    ) -> LearningCandidate: ...

    def link_learning_candidate_evidence(
        self,
        workspace_id: str,
        candidate_id: str,
        evidence_id: str,
        *,
        expected_row_version: int,
    ) -> LearningCandidate: ...

    def list_learning_candidate_evidence(
        self, workspace_id: str, candidate_id: str
    ) -> tuple[LearningEvidence, ...]: ...

    def put_learning_suppression(
        self, workspace_id: str, suppression: LearningSuppression
    ) -> LearningSuppression: ...

    def get_learning_suppression(
        self, workspace_id: str, suppression_id: str
    ) -> LearningSuppression | None: ...

    def list_learning_suppressions(
        self,
        workspace_id: str,
        *,
        candidate_type: str | None = None,
        scope: LearningScope | None = None,
        semantic_key: str | None = None,
        fingerprint: str | None = None,
        limit: int = 100,
    ) -> tuple[LearningSuppression, ...]: ...

    def save_learning_suppression(
        self,
        workspace_id: str,
        suppression: LearningSuppression,
        *,
        expected_row_version: int,
    ) -> LearningSuppression: ...


class LearningReviewerPort(Protocol):
    """No-tool asynchronous Reviewer boundary."""

    async def review(
        self,
        context: LearningContext,
        *,
        model: ModelRef,
        timeout_seconds: float,
    ) -> CandidateDraftBatch: ...


__all__ = [
    "LearningActiveSummary",
    "LearningContext",
    "LearningJournalPort",
    "LearningReviewerPort",
    "LearningReviewerError",
    "LEARNING_CONTEXT_MAX_RENDERED_CHARS",
]
