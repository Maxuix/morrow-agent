"""Bounded query and preview projections for the Stage 5 Learning Inbox."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import Field, field_validator, model_validator

from morrow.core.domain import canonical_json_bytes, refuse_secret_material
from morrow.core.learning import (
    LearningCandidate,
    LearningCandidateOperation,
    LearningCandidateStatus,
    LearningCandidateType,
    LearningEvidence,
    LearningPolicy,
    LearningReviewFailureCode,
    LearningReviewStatus,
    LearningReviewTrigger,
    LearningScope,
)
from morrow.core.learning_memory import (
    LearningCandidateDecisionKind,
    LearningConflictResolution,
    ProjectKnowledgeHead,
    ProjectKnowledgeRevision,
    ProjectKnowledgeStatus,
)
from morrow.core.models import ProtocolModel

LEARNING_QUERY_MAX_PAGE_SIZE = 100
LEARNING_QUERY_MAX_EVIDENCE = 16
LEARNING_QUERY_MAX_CONFLICTS = 16
LEARNING_QUERY_MAX_TIMELINE = 32
LEARNING_QUERY_VALUE_MAX_BYTES = 8 * 1024


class LearningEvidenceSummary(ProtocolModel):
    evidence_id: str
    source_kind: str
    source_id: str
    source_pointer: str | None = None
    actor: str
    authority: str
    explicitness: str
    polarity: str
    scope_hint: LearningScope | None = None
    excerpt_redacted: str | None = Field(default=None, max_length=512)
    content_digest: str
    observed_at: datetime

    @classmethod
    def from_evidence(cls, evidence: LearningEvidence) -> LearningEvidenceSummary:
        return cls(
            evidence_id=evidence.evidence_id,
            source_kind=evidence.source_kind.value,
            source_id=evidence.source_id,
            source_pointer=evidence.source_pointer,
            actor=evidence.actor.value,
            authority=evidence.authority.value,
            explicitness=evidence.explicitness.value,
            polarity=evidence.polarity.value,
            scope_hint=evidence.scope_hint,
            excerpt_redacted=evidence.excerpt_redacted,
            content_digest=evidence.content_digest,
            observed_at=evidence.observed_at,
        )


class LearningCandidateSummary(ProtocolModel):
    candidate_id: str
    candidate_type: LearningCandidateType
    operation: LearningCandidateOperation
    semantic_key: str
    proposed_scope: LearningScope
    status: LearningCandidateStatus
    fingerprint: str
    row_version: int = Field(ge=1)

    @classmethod
    def from_candidate(cls, candidate: LearningCandidate) -> LearningCandidateSummary:
        return cls(
            candidate_id=candidate.candidate_id,
            candidate_type=candidate.candidate_type,
            operation=candidate.operation,
            semantic_key=candidate.semantic_key,
            proposed_scope=candidate.proposed_scope,
            status=candidate.status,
            fingerprint=candidate.fingerprint,
            row_version=candidate.row_version,
        )


class LearningSuppressionSummary(ProtocolModel):
    suppression_id: str
    candidate_type: LearningCandidateType
    scope: LearningScope
    semantic_key: str | None = None
    fingerprint: str | None = None
    status: str
    expires_at: datetime | None = None
    row_version: int = Field(ge=1)


class LearningTargetSummary(ProtocolModel):
    """Current target information without pretending YAML targets are Active memory."""

    target_kind: str
    semantic_key: str
    available: bool
    status: ProjectKnowledgeStatus | None = None
    revision: int | None = Field(default=None, ge=1)
    revision_id: str | None = None
    statement: str | None = Field(default=None, max_length=4_096)
    statement_digest: str | None = None
    reason: str | None = Field(default=None, max_length=128)


class LearningCandidateView(ProtocolModel):
    candidate: LearningCandidate
    evidence: tuple[LearningEvidenceSummary, ...] = ()
    conflicts: tuple[LearningCandidateSummary, ...] = ()
    duplicate_of: LearningCandidateSummary | None = None
    suppressions: tuple[LearningSuppressionSummary, ...] = ()
    target: LearningTargetSummary

    @model_validator(mode="after")
    def bounded_children(self) -> LearningCandidateView:
        if len(self.evidence) > LEARNING_QUERY_MAX_EVIDENCE:
            raise ValueError("candidate evidence projection is too large")
        if len(self.conflicts) > LEARNING_QUERY_MAX_CONFLICTS:
            raise ValueError("candidate conflict projection is too large")
        if len(self.suppressions) > LEARNING_QUERY_MAX_CONFLICTS:
            raise ValueError("candidate suppression projection is too large")
        return self


class LearningReviewView(ProtocolModel):
    review_id: str
    task_run_id: str
    task_outcome_id: str
    review_version: int = Field(ge=1)
    trigger: LearningReviewTrigger
    status: LearningReviewStatus
    attempt_count: int = Field(ge=0)
    row_version: int = Field(ge=1)
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    failure_code: LearningReviewFailureCode | None = None
    candidate_count: int = Field(ge=0)
    evidence_count: int = Field(ge=0)


class LearningStatusView(ProtocolModel):
    policy: LearningPolicy
    persisted: bool
    pending_reviews: int = Field(ge=0)
    running_reviews: int = Field(ge=0)
    failed_reviews: int = Field(ge=0)
    proposed_candidates: int = Field(ge=0)


class LearningPreviewValue(ProtocolModel):
    target_kind: str
    semantic_key: str
    scope: LearningScope
    value: dict[str, Any] | None = None
    value_digest: str

    @field_validator("value")
    @classmethod
    def bounded_value(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        if value is None:
            return value
        encoded = canonical_json_bytes(value)
        if len(encoded) > LEARNING_QUERY_VALUE_MAX_BYTES:
            raise ValueError("learning preview value is too large")
        refuse_secret_material(encoded, label="learning preview value")
        return value


class LearningCandidateDecisionPreview(ProtocolModel):
    candidate: LearningCandidateSummary
    decision_kind: LearningCandidateDecisionKind
    expected_row_version: int = Field(ge=1)
    scope: LearningScope
    conflict_resolution: LearningConflictResolution
    before: LearningPreviewValue | None = None
    after: LearningPreviewValue
    available: bool
    reason: str | None = Field(default=None, max_length=128)
    conflict: str | None = Field(default=None, max_length=128)


class ProjectKnowledgeSummary(ProtocolModel):
    head: ProjectKnowledgeHead
    current_revision: ProjectKnowledgeRevision | None = None


class ProjectKnowledgeView(ProtocolModel):
    head: ProjectKnowledgeHead
    revision: ProjectKnowledgeRevision | None = None
    timeline: tuple[ProjectKnowledgeRevision, ...] = ()
    evidence: tuple[LearningEvidenceSummary, ...] = ()

    @model_validator(mode="after")
    def bounded_history(self) -> ProjectKnowledgeView:
        if len(self.timeline) > LEARNING_QUERY_MAX_TIMELINE:
            raise ValueError("knowledge timeline projection is too large")
        if len(self.evidence) > LEARNING_QUERY_MAX_EVIDENCE:
            raise ValueError("knowledge evidence projection is too large")
        return self


__all__ = [
    "LEARNING_QUERY_MAX_EVIDENCE",
    "LEARNING_QUERY_MAX_PAGE_SIZE",
    "LearningCandidateDecisionPreview",
    "LearningCandidateSummary",
    "LearningCandidateView",
    "LearningEvidenceSummary",
    "LearningPreviewValue",
    "LearningReviewView",
    "LearningStatusView",
    "LearningSuppressionSummary",
    "LearningTargetSummary",
    "ProjectKnowledgeSummary",
    "ProjectKnowledgeView",
]
