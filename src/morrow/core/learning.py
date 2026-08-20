"""Bounded Stage 5 learning contracts.

This module contains immutable domain values only.  It has no knowledge of SQLite,
YAML, Providers, Sessions, tools, or user interfaces.  Application services decide
when a value is eligible to be persisted or promoted.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from enum import StrEnum

from pydantic import Field, field_validator, model_validator

from morrow.core.domain import (
    DIGEST_PATTERN,
    canonical_json_bytes,
    require_payload_budget,
    sha256_digest,
    validate_prefixed_id,
)
from morrow.core.learning_safety import (
    LearningSafetyCode,
    LearningSafetyFinding,
    learning_safety_codes,
    normalize_learning_text,
    scan_learning_text,
)
from morrow.core.models import ProtocolModel, utc_now

LEARNING_POLICY_SCHEMA_VERSION = 1
LEARNING_REVIEW_SCHEMA_VERSION = 1
LEARNING_CANDIDATE_SCHEMA_VERSION = 1

LEARNING_WORKSPACE_ID_PREFIX = "ws"
LEARNING_REVIEW_ID_PREFIX = "lrv"
LEARNING_EVIDENCE_ID_PREFIX = "lev"
LEARNING_CANDIDATE_ID_PREFIX = "lcn"
LEARNING_SUPPRESSION_ID_PREFIX = "lsp"
LEARNING_LEASE_ID_PREFIX = "lease"
LEARNING_DECISION_ID_PREFIX = "lcd"
LEARNING_KNOWLEDGE_ID_PREFIX = "knw"
LEARNING_KNOWLEDGE_REVISION_ID_PREFIX = "krv"

LEARNING_POLICY_SNAPSHOT_MAX_BYTES = 8 * 1024
LEARNING_EVIDENCE_EXCERPT_MAX_CHARS = 512
LEARNING_EVIDENCE_EXCERPT_MAX_BYTES = LEARNING_EVIDENCE_EXCERPT_MAX_CHARS * 4
LEARNING_MAX_EVIDENCE_PER_REVIEW = 32
LEARNING_CANDIDATE_PROPOSAL_MAX_BYTES = 8 * 1024
LEARNING_DECISION_PROPOSAL_MAX_BYTES = LEARNING_CANDIDATE_PROPOSAL_MAX_BYTES
LEARNING_KNOWLEDGE_STATEMENT_MAX_CHARS = 4_096
LEARNING_SEMANTIC_KEY_MAX_CHARS = 128
LEARNING_MAX_REFERENCE_IDS = 16
LEARNING_MAX_CANDIDATES_PER_REVIEW = 3
LEARNING_MAX_REVIEW_ATTEMPTS = 3
LEARNING_REVIEW_LEASE_SECONDS = 60
LEARNING_SUPPRESSION_REASON_MAX_CHARS = 256

_SEMANTIC_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+){0,31}$")
_LOCAL_CODE_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_SOURCE_POINTER_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_.:/-]{0,127}$")


class LearningDomainError(ValueError):
    """Stable, sanitized domain validation error."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class LearningMode(StrEnum):
    OFF = "off"
    REVIEW_ONLY = "review_only"
    EXPLICIT_AUTO = "explicit_auto"


class LearningScope(StrEnum):
    GLOBAL = "global"
    WORKSPACE = "workspace"
    SESSION = "session"
    TASK = "task"


class LearningReviewTrigger(StrEnum):
    TASK_ACCEPTED = "task_accepted"
    EXPLICIT_REQUEST = "explicit_request"


class LearningReviewStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SUPERSEDED = "superseded"


class LearningReviewFailureCode(StrEnum):
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    TIMEOUT = "timeout"
    INVALID_OUTPUT = "invalid_output"
    SAFETY_REJECTED = "safety_rejected"
    LEASE_LOST = "lease_lost"
    CANCELLED = "cancelled"
    INTERNAL = "internal"


class LearningEvidenceActor(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"
    SYSTEM = "system"


class LearningEvidenceAuthority(StrEnum):
    USER_EXPLICIT_PERSISTENT = "user_explicit_persistent"
    USER_CORRECTION = "user_correction"
    USER_ACCEPTANCE = "user_acceptance"
    CONFIGURATION_CHANGE = "configuration_change"
    DETERMINISTIC_TASK_FACT = "deterministic_task_fact"
    DETERMINISTIC_ARTIFACT_FACT = "deterministic_artifact_fact"
    BEHAVIORAL_SIGNAL = "behavioral_signal"
    UNTRUSTED_EXTERNAL_CONTENT = "untrusted_external_content"


class LearningEvidenceExplicitness(StrEnum):
    EXPLICIT = "explicit"
    BEHAVIORAL = "behavioral"
    INFERRED = "inferred"


class LearningEvidencePolarity(StrEnum):
    POSITIVE = "positive"
    NEGATIVE = "negative"
    NEUTRAL = "neutral"


class LearningEvidenceSourceKind(StrEnum):
    USER_TURN = "user_turn"
    TASK_TRANSITION = "task_transition"
    TASK_OUTCOME = "task_outcome"
    ARTIFACT = "artifact"
    TOOL_EXECUTION = "tool_execution"
    CONFIGURATION = "configuration"
    CANDIDATE_DECISION = "candidate_decision"
    SYSTEM = "system"


class LearningCandidateType(StrEnum):
    PREFERENCE = "preference"
    PROFILE = "profile"
    PROJECT_KNOWLEDGE = "project_knowledge"
    SKILL_CANDIDATE = "skill_candidate"
    WORKFLOW_FEEDBACK = "workflow_feedback"
    ORCHESTRATION_POLICY_CANDIDATE = "orchestration_policy_candidate"


class LearningCandidateOperation(StrEnum):
    SET = "set"
    APPEND = "append"
    REPLACE = "replace"
    REMOVE = "remove"


class LearningCandidateStatus(StrEnum):
    PROPOSED = "proposed"
    PROMOTING = "promoting"
    ACCEPTED = "accepted"
    EDITED_AND_ACCEPTED = "edited_and_accepted"
    REJECTED = "rejected"
    EXPIRED = "expired"
    SUPERSEDED = "superseded"


class LearningSensitivity(StrEnum):
    NORMAL = "normal"
    PERSONAL = "personal"
    SENSITIVE = "sensitive"
    PROHIBITED = "prohibited"


class LearningResolutionActor(StrEnum):
    USER = "user"
    POLICY = "policy"


class LearningSuppressionStatus(StrEnum):
    ACTIVE = "active"
    DISABLED = "disabled"
    EXPIRED = "expired"


class LearningConfidenceBand(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _workspace_id(value: str) -> str:
    return validate_prefixed_id(value, LEARNING_WORKSPACE_ID_PREFIX)


def _digest(value: str, *, label: str) -> str:
    if not DIGEST_PATTERN.fullmatch(value):
        raise ValueError(f"{label} must be a SHA-256 hex digest")
    return value


def _opaque(value: str, *, label: str, prefix: str | None = None) -> str:
    if prefix is not None:
        return validate_prefixed_id(value, prefix)
    if not _LOCAL_CODE_PATTERN.fullmatch(value) and not re.fullmatch(
        r"^[A-Za-z][A-Za-z0-9_.:/-]{0,127}$", value
    ):
        raise ValueError(f"{label} must be a bounded opaque token")
    return value


def _semantic_key(value: str) -> str:
    cleaned = value.strip().lower()
    if len(cleaned) > LEARNING_SEMANTIC_KEY_MAX_CHARS or not _SEMANTIC_KEY_PATTERN.fullmatch(
        cleaned
    ):
        raise ValueError("semantic_key must be bounded lowercase dotted tokens")
    return cleaned


def _local_code(value: str, *, label: str) -> str:
    if not _LOCAL_CODE_PATTERN.fullmatch(value):
        raise ValueError(f"{label} must be a bounded lowercase code")
    return value


def _source_pointer(value: str) -> str:
    if not _SOURCE_POINTER_PATTERN.fullmatch(value):
        raise ValueError("source_pointer must be a bounded opaque pointer")
    return value


def _clean_bounded(value: str, *, label: str, maximum: int, safe: bool = True) -> str:
    if safe:
        return normalize_learning_text(value, label=label, maximum=maximum)
    cleaned = " ".join(value.split())
    if not cleaned or len(cleaned) > maximum:
        raise ValueError(f"{label} must be bounded and non-empty")
    return cleaned


def _bounded_codes(values: tuple[str, ...], *, label: str, maximum: int = 16) -> tuple[str, ...]:
    if len(values) > maximum:
        raise ValueError(f"{label} contains too many items")
    cleaned: list[str] = []
    for value in values:
        cleaned_value = _clean_bounded(value, label=label, maximum=128)
        if cleaned_value not in cleaned:
            cleaned.append(cleaned_value)
    return tuple(cleaned)


from morrow.core.learning_payloads import (  # noqa: E402
    CandidateDraft,
    CandidateDraftBatch,
    CandidatePayload,
    LearningCandidateDraft,
    LearningPayload,
    OrchestrationPolicyCandidatePayload,
    PreferenceCandidatePayload,
    ProfileCandidatePayload,
    ProjectKnowledgeCandidatePayload,
    ProjectKnowledgeCategory,
    SkillCandidatePayload,
    WorkflowFeedbackCandidatePayload,
)


class LearningPolicy(ProtocolModel):
    workspace_id: str
    mode: LearningMode = LearningMode.REVIEW_ONLY
    candidate_ttl_days: int = Field(default=30, ge=1, le=365)
    max_candidates_per_review: int = Field(default=3, ge=1, le=LEARNING_MAX_CANDIDATES_PER_REVIEW)
    max_evidence_per_review: int = Field(default=32, ge=1, le=LEARNING_MAX_EVIDENCE_PER_REVIEW)
    row_version: int = Field(default=1, ge=1)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    _valid_workspace = field_validator("workspace_id")(_workspace_id)
    _normalize_time = field_validator("created_at", "updated_at", mode="before")(_utc)

    @model_validator(mode="after")
    def ordered_timestamps(self) -> LearningPolicy:
        if self.updated_at < self.created_at:
            raise ValueError("learning policy updated_at must not precede created_at")
        return self

    @classmethod
    def default_for(cls, workspace_id: str, *, now: datetime | None = None) -> LearningPolicy:
        timestamp = _utc(now or utc_now())
        return cls(workspace_id=workspace_id, created_at=timestamp, updated_at=timestamp)


class LearningReview(ProtocolModel):
    review_id: str
    workspace_id: str
    task_run_id: str
    task_outcome_id: str
    review_version: int = Field(default=1, ge=1)
    trigger: LearningReviewTrigger
    status: LearningReviewStatus = LearningReviewStatus.PENDING
    policy_snapshot_json: str
    policy_digest: str
    reviewer_provider_id: str | None = None
    reviewer_model_id: str | None = None
    reviewer_prompt_version: str = Field(default="stage5-v1", min_length=1, max_length=64)
    reviewer_schema_version: str = Field(default="stage5-learning-v1", min_length=1, max_length=64)
    supersedes_review_id: str | None = None
    lease_id: str | None = None
    lease_expires_at: datetime | None = None
    attempt_count: int = Field(default=0, ge=0, le=32)
    row_version: int = Field(default=1, ge=1)
    created_at: datetime = Field(default_factory=utc_now)
    started_at: datetime | None = None
    completed_at: datetime | None = None
    failure_code: LearningReviewFailureCode | None = None

    _valid_review = field_validator("review_id")(
        lambda value: validate_prefixed_id(value, LEARNING_REVIEW_ID_PREFIX)
    )
    _valid_workspace = field_validator("workspace_id")(_workspace_id)
    _valid_task = field_validator("task_run_id")(lambda value: validate_prefixed_id(value, "task"))
    _valid_outcome = field_validator("task_outcome_id")(
        lambda value: validate_prefixed_id(value, "out")
    )
    _valid_supersedes = field_validator("supersedes_review_id")(
        lambda value: (
            None if value is None else validate_prefixed_id(value, LEARNING_REVIEW_ID_PREFIX)
        )
    )
    _valid_lease = field_validator("lease_id")(
        lambda value: (
            None if value is None else validate_prefixed_id(value, LEARNING_LEASE_ID_PREFIX)
        )
    )
    _valid_digest = field_validator("policy_digest")(
        lambda value: _digest(value, label="policy_digest")
    )
    _normalize_time = field_validator(
        "created_at", "started_at", "completed_at", "lease_expires_at", mode="before"
    )(_utc)

    @field_validator("policy_snapshot_json")
    @classmethod
    def bounded_policy_snapshot(cls, value: str) -> str:
        require_payload_budget(
            value.encode("utf-8"),
            LEARNING_POLICY_SNAPSHOT_MAX_BYTES,
            label="learning policy snapshot",
        )
        if not value.strip():
            raise ValueError("policy snapshot must not be empty")
        return value

    @field_validator("reviewer_provider_id", "reviewer_model_id")
    @classmethod
    def bounded_model_ref(cls, value: str | None) -> str | None:
        return (
            None
            if value is None
            else _clean_bounded(value, label="reviewer model reference", maximum=128)
        )

    @model_validator(mode="after")
    def valid_lifecycle(self) -> LearningReview:
        if self.status is LearningReviewStatus.RUNNING and (
            self.lease_id is None or self.lease_expires_at is None
        ):
            raise ValueError("running review requires a lease")
        if self.completed_at is not None and self.completed_at < self.created_at:
            raise ValueError("review completed_at must not precede created_at")
        if self.failure_code is not None and self.status is not LearningReviewStatus.FAILED:
            raise ValueError("failure_code requires a failed review")
        return self


class LearningEvidence(ProtocolModel):
    evidence_id: str
    workspace_id: str
    origin_review_id: str
    task_run_id: str
    source_kind: LearningEvidenceSourceKind
    source_id: str
    source_pointer: str | None = None
    actor: LearningEvidenceActor
    authority: LearningEvidenceAuthority
    explicitness: LearningEvidenceExplicitness
    polarity: LearningEvidencePolarity
    scope_hint: LearningScope | None = None
    excerpt_redacted: str | None = None
    content_digest: str
    safety_rejection_code: LearningSafetyCode | None = None
    observed_at: datetime = Field(default_factory=utc_now)
    created_at: datetime = Field(default_factory=utc_now)

    _valid_evidence = field_validator("evidence_id")(
        lambda value: validate_prefixed_id(value, LEARNING_EVIDENCE_ID_PREFIX)
    )
    _valid_workspace = field_validator("workspace_id")(_workspace_id)
    _valid_review = field_validator("origin_review_id")(
        lambda value: validate_prefixed_id(value, LEARNING_REVIEW_ID_PREFIX)
    )
    _valid_task = field_validator("task_run_id")(lambda value: validate_prefixed_id(value, "task"))
    _valid_source_id = field_validator("source_id")(lambda value: _opaque(value, label="source_id"))
    _valid_source_pointer = field_validator("source_pointer")(
        lambda value: None if value is None else _source_pointer(value)
    )
    _valid_digest = field_validator("content_digest")(
        lambda value: _digest(value, label="content_digest")
    )
    _normalize_time = field_validator("observed_at", "created_at", mode="before")(_utc)

    @field_validator("excerpt_redacted")
    @classmethod
    def bounded_excerpt(cls, value: str | None) -> str | None:
        if value is None:
            return value
        return normalize_learning_text(
            value, label="evidence excerpt", maximum=LEARNING_EVIDENCE_EXCERPT_MAX_CHARS
        )

    @model_validator(mode="after")
    def validate_safety_and_authority(self) -> LearningEvidence:
        if self.safety_rejection_code is not None and self.excerpt_redacted is not None:
            raise ValueError("rejected evidence must not retain an excerpt")
        if self.actor is LearningEvidenceActor.ASSISTANT and self.authority in {
            LearningEvidenceAuthority.USER_EXPLICIT_PERSISTENT,
            LearningEvidenceAuthority.USER_CORRECTION,
            LearningEvidenceAuthority.USER_ACCEPTANCE,
        }:
            raise ValueError("assistant evidence cannot claim user authority")
        return self


def is_positive_explicit_user_evidence(evidence: LearningEvidence) -> bool:
    """Return whether evidence is sufficient for a durable Preference/Profile write."""

    return (
        evidence.authority is LearningEvidenceAuthority.USER_EXPLICIT_PERSISTENT
        and evidence.explicitness is LearningEvidenceExplicitness.EXPLICIT
        and evidence.polarity is LearningEvidencePolarity.POSITIVE
        and evidence.actor is LearningEvidenceActor.USER
        and evidence.safety_rejection_code is None
    )


class LearningCandidate(ProtocolModel):
    candidate_id: str
    workspace_id: str
    origin_review_id: str
    candidate_type: LearningCandidateType
    operation: LearningCandidateOperation
    semantic_key: str
    proposed_scope: LearningScope
    proposed_payload: CandidatePayload
    fingerprint: str
    status: LearningCandidateStatus = LearningCandidateStatus.PROPOSED
    evidence_ids: tuple[str, ...]
    confidence_band: LearningConfidenceBand
    confidence_basis: tuple[str, ...]
    sensitivity: LearningSensitivity = LearningSensitivity.NORMAL
    expected_target_revision: int | None = Field(default=None, ge=1)
    duplicate_of_id: str | None = None
    supersedes_id: str | None = None
    conflict_refs: tuple[str, ...] = ()
    expires_at: datetime
    row_version: int = Field(default=1, ge=1)
    created_at: datetime = Field(default_factory=utc_now)
    resolved_at: datetime | None = None
    resolved_by: LearningResolutionActor | None = None
    rejection_reason: str | None = None

    _valid_candidate = field_validator("candidate_id")(
        lambda value: validate_prefixed_id(value, LEARNING_CANDIDATE_ID_PREFIX)
    )
    _valid_workspace = field_validator("workspace_id")(_workspace_id)
    _valid_review = field_validator("origin_review_id")(
        lambda value: validate_prefixed_id(value, LEARNING_REVIEW_ID_PREFIX)
    )
    _valid_key = field_validator("semantic_key")(_semantic_key)
    _valid_digest = field_validator("fingerprint")(
        lambda value: _digest(value, label="candidate fingerprint")
    )
    _valid_duplicate = field_validator("duplicate_of_id", "supersedes_id")(
        lambda value: (
            None if value is None else validate_prefixed_id(value, LEARNING_CANDIDATE_ID_PREFIX)
        )
    )

    @field_validator("evidence_ids", "conflict_refs")
    @classmethod
    def valid_refs(cls, values: tuple[str, ...], info) -> tuple[str, ...]:
        if len(values) > LEARNING_MAX_REFERENCE_IDS:
            raise ValueError(f"{info.field_name} contains too many references")
        prefix = (
            LEARNING_EVIDENCE_ID_PREFIX
            if info.field_name == "evidence_ids"
            else LEARNING_CANDIDATE_ID_PREFIX
        )
        cleaned = tuple(validate_prefixed_id(value, prefix) for value in values)
        if len(cleaned) != len(set(cleaned)):
            raise ValueError(f"{info.field_name} references must be unique")
        return cleaned

    _normalize_time = field_validator("expires_at", "created_at", "resolved_at", mode="before")(
        _utc
    )

    @field_validator("confidence_basis")
    @classmethod
    def valid_confidence_basis(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return _bounded_codes(values, label="confidence basis")

    @field_validator("rejection_reason")
    @classmethod
    def valid_rejection_reason(cls, value: str | None) -> str | None:
        return (
            None if value is None else _clean_bounded(value, label="rejection reason", maximum=256)
        )

    @model_validator(mode="after")
    def valid_candidate(self) -> LearningCandidate:
        if self.proposed_payload.candidate_type is not self.candidate_type:
            raise ValueError("candidate type does not match its payload")
        if self.candidate_type is LearningCandidateType.PREFERENCE:
            if self.proposed_scope not in {LearningScope.GLOBAL, LearningScope.WORKSPACE}:
                raise ValueError("preference candidate scope must be global or workspace")
        elif self.proposed_scope is not LearningScope.WORKSPACE:
            raise ValueError("candidate type is workspace scoped")
        terminal_statuses = {
            LearningCandidateStatus.REJECTED,
            LearningCandidateStatus.EXPIRED,
            LearningCandidateStatus.ACCEPTED,
            LearningCandidateStatus.EDITED_AND_ACCEPTED,
            LearningCandidateStatus.SUPERSEDED,
        }
        if self.status in terminal_statuses:
            if self.resolved_at is None or self.resolved_by is None:
                raise ValueError("resolved candidate must have resolution metadata")
        elif self.resolved_at is not None or self.resolved_by is not None:
            raise ValueError("unresolved candidate must not have resolution metadata")
        if self.status is LearningCandidateStatus.REJECTED and self.rejection_reason is None:
            raise ValueError("rejected candidate must have a rejection reason")
        if (
            self.status is not LearningCandidateStatus.REJECTED
            and self.rejection_reason is not None
        ):
            raise ValueError("rejection reason requires a rejected candidate")
        expected_fingerprint = self.fingerprint_for(
            candidate_type=self.candidate_type,
            scope=self.proposed_scope,
            semantic_key=self.semantic_key,
            operation=self.operation,
            proposed_payload=self.proposed_payload,
        )
        if self.fingerprint != expected_fingerprint:
            raise ValueError("candidate fingerprint does not match its proposal")
        proposal_budget = canonical_json_bytes(self.proposed_payload.model_dump(mode="json"))
        require_payload_budget(
            proposal_budget, LEARNING_CANDIDATE_PROPOSAL_MAX_BYTES, label="candidate proposal"
        )
        return self

    @classmethod
    def fingerprint_for(
        cls,
        *,
        candidate_type: LearningCandidateType,
        scope: LearningScope,
        semantic_key: str,
        operation: LearningCandidateOperation,
        proposed_payload: CandidatePayload,
    ) -> str:
        payload = {
            "candidate_type": candidate_type.value,
            "scope": scope.value,
            "semantic_key": _semantic_key(semantic_key),
            "operation": operation.value,
            "proposed_payload": proposed_payload.model_dump(mode="json"),
        }
        return sha256_digest(canonical_json_bytes(payload))

    @classmethod
    def from_draft(
        cls,
        *,
        candidate_id: str,
        workspace_id: str,
        origin_review_id: str,
        draft: LearningCandidateDraft,
        confidence_band: LearningConfidenceBand,
        confidence_basis: tuple[str, ...],
        sensitivity: LearningSensitivity,
        expires_at: datetime,
        now: datetime | None = None,
    ) -> LearningCandidate:
        fingerprint = cls.fingerprint_for(
            candidate_type=draft.candidate_type,
            scope=draft.proposed_scope,
            semantic_key=draft.semantic_key,
            operation=draft.operation,
            proposed_payload=draft.proposed_payload,
        )
        timestamp = _utc(now or utc_now())
        return cls(
            candidate_id=candidate_id,
            workspace_id=workspace_id,
            origin_review_id=origin_review_id,
            candidate_type=draft.candidate_type,
            operation=draft.operation,
            semantic_key=draft.semantic_key,
            proposed_scope=draft.proposed_scope,
            proposed_payload=draft.proposed_payload,
            fingerprint=fingerprint,
            evidence_ids=draft.evidence_ids,
            confidence_band=confidence_band,
            confidence_basis=confidence_basis,
            sensitivity=sensitivity,
            expires_at=_utc(expires_at),
            created_at=timestamp,
        )


class LearningSuppression(ProtocolModel):
    suppression_id: str
    workspace_id: str
    candidate_type: LearningCandidateType
    scope: LearningScope
    semantic_key: str | None = None
    fingerprint: str | None = None
    source_candidate_id: str | None = None
    reason: str
    status: LearningSuppressionStatus = LearningSuppressionStatus.ACTIVE
    expires_at: datetime | None = None
    row_version: int = Field(default=1, ge=1)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    _valid_suppression = field_validator("suppression_id")(
        lambda value: validate_prefixed_id(value, LEARNING_SUPPRESSION_ID_PREFIX)
    )
    _valid_workspace = field_validator("workspace_id")(_workspace_id)
    _valid_key = field_validator("semantic_key")(
        lambda value: None if value is None else _semantic_key(value)
    )
    _valid_fingerprint = field_validator("fingerprint")(
        lambda value: None if value is None else _digest(value, label="suppression fingerprint")
    )
    _valid_source = field_validator("source_candidate_id")(
        lambda value: (
            None if value is None else validate_prefixed_id(value, LEARNING_CANDIDATE_ID_PREFIX)
        )
    )
    _normalize_time = field_validator("expires_at", "created_at", "updated_at", mode="before")(_utc)

    @field_validator("reason")
    @classmethod
    def bounded_reason(cls, value: str) -> str:
        return _clean_bounded(
            value, label="suppression reason", maximum=LEARNING_SUPPRESSION_REASON_MAX_CHARS
        )

    @model_validator(mode="after")
    def has_match_key(self) -> LearningSuppression:
        if self.semantic_key is None and self.fingerprint is None:
            raise ValueError("suppression requires a semantic key or exact fingerprint")
        if self.updated_at < self.created_at:
            raise ValueError("suppression updated_at must not precede created_at")
        return self


__all__ = [
    "CandidateDraft",
    "CandidateDraftBatch",
    "CandidatePayload",
    "LearningCandidate",
    "LearningCandidateDraft",
    "LearningCandidateOperation",
    "LearningCandidateStatus",
    "LearningCandidateType",
    "LearningConfidenceBand",
    "LearningDomainError",
    "LearningEvidence",
    "LearningEvidenceActor",
    "LearningEvidenceAuthority",
    "LearningEvidenceExplicitness",
    "LearningEvidencePolarity",
    "is_positive_explicit_user_evidence",
    "LearningEvidenceSourceKind",
    "LearningMode",
    "LearningPolicy",
    "LearningPayload",
    "LearningReview",
    "LearningReviewFailureCode",
    "LearningReviewStatus",
    "LearningReviewTrigger",
    "LearningSafetyCode",
    "LearningSafetyFinding",
    "LearningScope",
    "LearningSensitivity",
    "LearningSuppression",
    "LearningSuppressionStatus",
    "LEARNING_MAX_REVIEW_ATTEMPTS",
    "LEARNING_DECISION_ID_PREFIX",
    "LEARNING_DECISION_PROPOSAL_MAX_BYTES",
    "LEARNING_KNOWLEDGE_ID_PREFIX",
    "LEARNING_KNOWLEDGE_REVISION_ID_PREFIX",
    "LEARNING_KNOWLEDGE_STATEMENT_MAX_CHARS",
    "LEARNING_REVIEW_LEASE_SECONDS",
    "OrchestrationPolicyCandidatePayload",
    "PreferenceCandidatePayload",
    "ProfileCandidatePayload",
    "ProjectKnowledgeCandidatePayload",
    "ProjectKnowledgeCategory",
    "SkillCandidatePayload",
    "WorkflowFeedbackCandidatePayload",
    "learning_safety_codes",
    "scan_learning_text",
]
