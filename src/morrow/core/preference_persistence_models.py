"""Durable Review, Evidence, Proposal, and Writer saga contracts."""

from __future__ import annotations

import hashlib
from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import Field, field_validator, model_validator

from morrow.core.domain import canonical_json_bytes, validate_prefixed_id
from morrow.core.models import ProtocolModel, utc_now
from morrow.core.preference_models import (
    PREFERENCE_EVIDENCE_ID_PREFIX,
    PREFERENCE_ID_PREFIX,
    PREFERENCE_MAX_ACTIVE_SNAPSHOT_BYTES,
    PREFERENCE_MAX_ACTIVE_SNAPSHOT_ENTRIES,
    PREFERENCE_MAX_EXCERPT_BYTES,
    PREFERENCE_MAX_EXCERPT_CHARS,
    PREFERENCE_MAX_OPERATIONS,
    PREFERENCE_PROPOSAL_ID_PREFIX,
    PREFERENCE_REVIEW_JOB_ID_PREFIX,
    PREFERENCE_WRITE_BATCH_ID_PREFIX,
    PreferenceOperation,
    PreferenceOperationKind,
    PreferenceScope,
    _aware,
    _digest,
    _refs,
    _workspace_id,
    normalize_preference_statement,
)


class PreferenceReviewJobStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    EXHAUSTED = "exhausted"
    CANCELLED = "cancelled"
    SUPERSEDED = "superseded"


class PreferenceReviewFailureCode(StrEnum):
    CONTEXT_BUDGET = "context_budget"
    REQUEST_BUDGET = "request_budget"
    SAFETY_REJECTED = "safety_rejected"
    SNAPSHOT_INVALID = "snapshot_invalid"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    TIMEOUT = "timeout"
    MALFORMED_OUTPUT = "malformed_output"
    LEASE_LOST = "lease_lost"
    CANCELLED = "cancelled"
    PERSISTENCE = "persistence"


class PreferenceProposalStatus(StrEnum):
    PROPOSED = "proposed"
    ACCEPTED = "accepted"
    EDITED_AND_ACCEPTED = "edited_and_accepted"
    REJECTED = "rejected"
    SUPPRESSED = "suppressed"
    STALE = "stale"
    CONFLICT = "conflict"
    EXPIRED = "expired"


class PreferenceWriteBatchStatus(StrEnum):
    PREPARED = "prepared"
    YAML_APPLIED = "yaml_applied"
    FINALIZED = "finalized"
    NEEDS_RESOLUTION = "needs_resolution"
    FAILED = "failed"


class PreferenceSafetyRejectionCode(StrEnum):
    SECRET_MATERIAL = "secret_material"
    PROHIBITED_PERSONAL_DATA = "prohibited_personal_data"
    HIDDEN_UNICODE_CONTROL = "hidden_unicode_control"
    CAPABILITY_AUTHORIZATION = "capability_authorization"
    PROMPT_INJECTION = "prompt_injection"


class PreferenceReviewJob(ProtocolModel):
    """Durable delayed Review queue item."""

    job_id: str
    workspace_id: str
    session_id: str | None = None
    turn_id: str
    review_version: int = Field(default=1, ge=1)
    status: PreferenceReviewJobStatus = PreferenceReviewJobStatus.PENDING
    source_global_revision: int = Field(default=0, ge=0)
    source_workspace_revision: int = Field(default=0, ge=0)
    active_snapshot_json: str
    active_snapshot_count: int = Field(ge=0, le=PREFERENCE_MAX_ACTIVE_SNAPSHOT_ENTRIES)
    active_snapshot_bytes: int = Field(ge=2, le=PREFERENCE_MAX_ACTIVE_SNAPSHOT_BYTES)
    active_snapshot_digest: str
    reviewer_provider_id: str | None = None
    reviewer_model_id: str | None = None
    reviewer_prompt_version: str = Field(default="preference-v2", min_length=1, max_length=64)
    reviewer_schema_version: str = Field(
        default="preference-operations-v2", min_length=1, max_length=64
    )
    lease_id: str | None = None
    lease_expires_at: datetime | None = None
    attempt_count: int = Field(default=0, ge=0, le=3)
    row_version: int = Field(default=1, ge=1)
    created_at: datetime = Field(default_factory=utc_now)
    started_at: datetime | None = None
    completed_at: datetime | None = None
    failure_code: PreferenceReviewFailureCode | None = None

    _valid_id = field_validator("job_id")(
        lambda value: validate_prefixed_id(value, PREFERENCE_REVIEW_JOB_ID_PREFIX)
    )
    _valid_workspace = field_validator("workspace_id")(_workspace_id)
    _valid_session = field_validator("session_id")(
        lambda value: None if value is None else validate_prefixed_id(value, "ses")
    )
    _valid_turn = field_validator("turn_id")(lambda value: validate_prefixed_id(value, "turn"))
    _valid_digest = field_validator("active_snapshot_digest")(
        lambda value: _digest(value, label="active_snapshot_digest")
    )
    _normalize_time = field_validator(
        "created_at", "started_at", "completed_at", "lease_expires_at", mode="before"
    )(_aware)

    @field_validator("active_snapshot_json")
    @classmethod
    def valid_snapshot_json(cls, value: str) -> str:
        if not value or len(value.encode("utf-8")) > PREFERENCE_MAX_ACTIVE_SNAPSHOT_BYTES:
            raise ValueError("active Preference snapshot exceeds its byte budget")
        return value

    @model_validator(mode="after")
    def valid_snapshot_metadata(self) -> PreferenceReviewJob:
        encoded = self.active_snapshot_json.encode("utf-8")
        if len(encoded) != self.active_snapshot_bytes:
            raise ValueError("active Preference snapshot byte count is inconsistent")
        if hashlib.sha256(encoded).hexdigest() != self.active_snapshot_digest:
            raise ValueError("active Preference snapshot digest is inconsistent")
        if self.status is PreferenceReviewJobStatus.RUNNING and (
            self.lease_id is None or self.lease_expires_at is None
        ):
            raise ValueError("running Preference Review requires a lease")
        if self.failure_code is not None and self.status not in {
            PreferenceReviewJobStatus.FAILED,
            PreferenceReviewJobStatus.EXHAUSTED,
        }:
            raise ValueError("failure_code requires a failed or exhausted Review job")
        terminal_statuses = {
            PreferenceReviewJobStatus.COMPLETED,
            PreferenceReviewJobStatus.FAILED,
            PreferenceReviewJobStatus.EXHAUSTED,
            PreferenceReviewJobStatus.CANCELLED,
            PreferenceReviewJobStatus.SUPERSEDED,
        }
        if self.status in {PreferenceReviewJobStatus.PENDING, PreferenceReviewJobStatus.RUNNING}:
            if self.completed_at is not None:
                raise ValueError("pending or running Preference Review cannot be completed")
        elif self.status in terminal_statuses and self.completed_at is None:
            raise ValueError("terminal Preference Review requires completed_at")
        return self


class PreferenceEvidence(ProtocolModel):
    """Exactly one current-user evidence row attached to a Review job."""

    evidence_id: str
    workspace_id: str
    job_id: str
    turn_id: str
    source_kind: Literal["user_turn"] = "user_turn"
    actor: Literal["user"] = "user"
    excerpt_redacted: str | None = None
    excerpt_bytes: int = Field(default=0, ge=0, le=PREFERENCE_MAX_EXCERPT_BYTES)
    content_digest: str
    safety_rejection_code: PreferenceSafetyRejectionCode | None = None
    observed_at: datetime = Field(default_factory=utc_now)
    created_at: datetime = Field(default_factory=utc_now)

    _valid_id = field_validator("evidence_id")(
        lambda value: validate_prefixed_id(value, PREFERENCE_EVIDENCE_ID_PREFIX)
    )
    _valid_workspace = field_validator("workspace_id")(_workspace_id)
    _valid_job = field_validator("job_id")(
        lambda value: validate_prefixed_id(value, PREFERENCE_REVIEW_JOB_ID_PREFIX)
    )
    _valid_turn = field_validator("turn_id")(lambda value: validate_prefixed_id(value, "turn"))
    _valid_digest = field_validator("content_digest")(
        lambda value: _digest(value, label="Preference evidence content digest")
    )
    _normalize_time = field_validator("observed_at", "created_at", mode="before")(_aware)

    @field_validator("excerpt_redacted")
    @classmethod
    def valid_excerpt(cls, value: str | None) -> str | None:
        if value is None:
            return value
        normalized = normalize_preference_statement(value)
        if len(normalized) > PREFERENCE_MAX_EXCERPT_CHARS:
            raise ValueError("Preference evidence excerpt is too long")
        return normalized

    @model_validator(mode="after")
    def valid_excerpt_metadata(self) -> PreferenceEvidence:
        if self.safety_rejection_code is not None and self.excerpt_redacted is not None:
            raise ValueError("rejected evidence must not retain an excerpt")
        expected = (
            0 if self.excerpt_redacted is None else len(self.excerpt_redacted.encode("utf-8"))
        )
        if self.excerpt_bytes != expected:
            raise ValueError("Preference evidence excerpt byte count is inconsistent")
        return self


class PreferenceProposal(ProtocolModel):
    """One independent Inbox proposal produced by a validated Review operation."""

    proposal_id: str
    workspace_id: str
    job_id: str
    evidence_id: str
    operation: PreferenceOperation
    fingerprint: str
    expected_target_revision: int | None = Field(default=None, ge=1)
    expected_document_revision: int = Field(default=0, ge=0)
    status: PreferenceProposalStatus = PreferenceProposalStatus.PROPOSED
    final_operation: PreferenceOperation | None = None
    decision_command_id: str | None = None
    decision_reason: str | None = None
    row_version: int = Field(default=1, ge=1)
    created_at: datetime = Field(default_factory=utc_now)
    resolved_at: datetime | None = None

    _valid_id = field_validator("proposal_id")(
        lambda value: validate_prefixed_id(value, PREFERENCE_PROPOSAL_ID_PREFIX)
    )
    _valid_workspace = field_validator("workspace_id")(_workspace_id)
    _valid_job = field_validator("job_id")(
        lambda value: validate_prefixed_id(value, PREFERENCE_REVIEW_JOB_ID_PREFIX)
    )
    _valid_evidence = field_validator("evidence_id")(
        lambda value: validate_prefixed_id(value, PREFERENCE_EVIDENCE_ID_PREFIX)
    )
    _valid_fingerprint = field_validator("fingerprint")(
        lambda value: _digest(value, label="Preference proposal fingerprint")
    )
    _valid_command = field_validator("decision_command_id")(
        lambda value: None if value is None else validate_prefixed_id(value, "cmd")
    )
    _normalize_time = field_validator("created_at", "resolved_at", mode="before")(_aware)

    @model_validator(mode="after")
    def operation_matches_scope(self) -> PreferenceProposal:
        if self.operation.scope is PreferenceScope.SESSION:
            raise ValueError("durable Preference proposals cannot use session scope")
        if (
            self.final_operation is not None
            and self.final_operation.scope is not self.operation.scope
        ):
            raise ValueError("edited Preference operation cannot change scope")
        return self


class PreferenceWriteBatch(ProtocolModel):
    """Prepared same-scope Writer saga metadata."""

    batch_id: str
    workspace_id: str
    scope: PreferenceScope
    command_id: str
    operations: tuple[PreferenceOperation, ...]
    allocated_add_ids: tuple[str, ...] = ()
    proposal_ids: tuple[str, ...] = ()
    expected_document_revision: int = Field(ge=0)
    before_document_revision: int = Field(ge=0)
    before_document_digest: str
    after_document_revision: int = Field(ge=0)
    after_document_digest: str
    status: PreferenceWriteBatchStatus = PreferenceWriteBatchStatus.PREPARED
    before_document_json: str | None = None
    after_document_json: str | None = None
    recovery_code: str | None = None
    row_version: int = Field(default=1, ge=1)
    created_at: datetime = Field(default_factory=utc_now)
    prepared_at: datetime | None = None
    applied_at: datetime | None = None
    finalized_at: datetime | None = None

    _valid_id = field_validator("batch_id")(
        lambda value: validate_prefixed_id(value, PREFERENCE_WRITE_BATCH_ID_PREFIX)
    )
    _valid_workspace = field_validator("workspace_id")(_workspace_id)
    _valid_command = field_validator("command_id")(lambda value: validate_prefixed_id(value, "cmd"))
    _valid_digests = field_validator("before_document_digest", "after_document_digest")(
        lambda value: _digest(value, label="Preference write batch digest")
    )
    _valid_allocated_ids = field_validator("allocated_add_ids")(
        lambda values: _refs(values, prefix=PREFERENCE_ID_PREFIX, label="allocated Preference IDs")
    )
    _valid_proposals = field_validator("proposal_ids")(
        lambda values: _refs(values, prefix=PREFERENCE_PROPOSAL_ID_PREFIX, label="proposal IDs")
    )
    _normalize_time = field_validator(
        "created_at", "prepared_at", "applied_at", "finalized_at", mode="before"
    )(_aware)

    @model_validator(mode="after")
    def valid_batch(self) -> PreferenceWriteBatch:
        if not 1 <= len(self.operations) <= PREFERENCE_MAX_OPERATIONS:
            raise ValueError("Preference write batch must contain one to eight operations")
        if any(operation.scope is not self.scope for operation in self.operations):
            raise ValueError("Preference write batch must use one scope")
        if self.scope is PreferenceScope.SESSION:
            raise ValueError("durable Preference write batches cannot use session scope")
        add_count = sum(
            operation.operation is PreferenceOperationKind.ADD for operation in self.operations
        )
        if len(self.allocated_add_ids) not in {0, add_count}:
            raise ValueError("allocated Preference IDs must match add operations")
        return self


def preference_operation_fingerprint(operation: PreferenceOperation) -> str:
    """Return the stable digest used for idempotent proposal and batch writes."""

    return hashlib.sha256(canonical_json_bytes(operation.model_dump(mode="json"))).hexdigest()


__all__ = [
    "PreferenceEvidence",
    "PreferenceProposal",
    "PreferenceProposalStatus",
    "PreferenceReviewFailureCode",
    "PreferenceReviewJob",
    "PreferenceReviewJobStatus",
    "PreferenceSafetyRejectionCode",
    "PreferenceWriteBatch",
    "PreferenceWriteBatchStatus",
    "preference_operation_fingerprint",
]
