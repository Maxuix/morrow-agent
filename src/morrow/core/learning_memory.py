"""Immutable Learning decisions and the first Active memory type.

These models describe the v11 persistence boundary only.  They deliberately do
not decide whether a candidate is eligible for promotion; that policy belongs to
the application services.
"""

from __future__ import annotations

import json
from datetime import datetime
from enum import StrEnum

from pydantic import Field, field_validator, model_validator

from morrow.core.domain import (
    COMMAND_ID_PREFIX,
    canonical_json_bytes,
    refuse_secret_material,
    sha256_digest,
    validate_prefixed_id,
)
from morrow.core.learning import (
    LEARNING_CANDIDATE_ID_PREFIX,
    LEARNING_DECISION_ID_PREFIX,
    LEARNING_DECISION_PROPOSAL_MAX_BYTES,
    LEARNING_EVIDENCE_ID_PREFIX,
    LEARNING_KNOWLEDGE_ID_PREFIX,
    LEARNING_KNOWLEDGE_REVISION_ID_PREFIX,
    LEARNING_KNOWLEDGE_STATEMENT_MAX_CHARS,
    LearningResolutionActor,
    LearningScope,
    LearningSensitivity,
    ProjectKnowledgeCategory,
    _digest,
    _semantic_key,
    _utc,
)
from morrow.core.models import ProtocolModel, utc_now


class LearningCandidateDecisionKind(StrEnum):
    ACCEPT = "accept"
    EDIT_AND_ACCEPT = "edit_and_accept"
    REJECT = "reject"
    REJECT_AND_SUPPRESS = "reject_and_suppress"
    EXPIRE = "expire"
    SUPERSEDE = "supersede"


class LearningConflictResolution(StrEnum):
    NONE = "none"
    CONFIRM = "confirm"
    REPLACE = "replace"
    MERGE = "merge"
    RE_ENABLE = "re_enable"
    RESOLVE_DISPUTE = "resolve_dispute"


class ProjectKnowledgeStatus(StrEnum):
    ACTIVE = "active"
    DISABLED = "disabled"
    DISPUTED = "disputed"
    DELETED = "deleted"


def _canonical_object(value: str, *, label: str, maximum: int) -> str:
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} must be canonical JSON") from exc
    if not isinstance(parsed, dict):
        raise ValueError(f"{label} must be a JSON object")
    encoded = canonical_json_bytes(parsed)
    if encoded.decode("utf-8") != value:
        raise ValueError(f"{label} must be canonical JSON")
    if len(encoded) > maximum:
        raise ValueError(f"{label} exceeds its durable payload budget")
    refuse_secret_material(encoded, label=label)
    return value


class LearningCandidateDecision(ProtocolModel):
    """One immutable operator or policy decision over a Candidate."""

    decision_id: str
    workspace_id: str
    candidate_id: str
    kind: LearningCandidateDecisionKind
    actor: LearningResolutionActor
    original_proposal_digest: str
    final_proposal_json: str | None = None
    final_proposal_bytes: int | None = Field(
        default=None, ge=1, le=LEARNING_DECISION_PROPOSAL_MAX_BYTES
    )
    scope: LearningScope
    conflict_resolution: LearningConflictResolution = LearningConflictResolution.NONE
    command_id: str
    created_at: datetime = Field(default_factory=utc_now)

    _valid_decision = field_validator("decision_id")(
        lambda value: validate_prefixed_id(value, LEARNING_DECISION_ID_PREFIX)
    )
    _valid_workspace = field_validator("workspace_id")(
        lambda value: validate_prefixed_id(value, "ws")
    )
    _valid_candidate = field_validator("candidate_id")(
        lambda value: validate_prefixed_id(value, LEARNING_CANDIDATE_ID_PREFIX)
    )
    _valid_digest = field_validator("original_proposal_digest")(
        lambda value: _digest(value, label="original proposal digest")
    )
    _valid_command = field_validator("command_id")(
        lambda value: validate_prefixed_id(value, COMMAND_ID_PREFIX)
    )
    _normalize_time = field_validator("created_at", mode="before")(_utc)

    @field_validator("final_proposal_json")
    @classmethod
    def valid_final_proposal(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _canonical_object(
            value,
            label="final proposal",
            maximum=LEARNING_DECISION_PROPOSAL_MAX_BYTES,
        )

    @model_validator(mode="after")
    def final_proposal_matches_kind(self) -> LearningCandidateDecision:
        has_json = self.final_proposal_json is not None
        has_bytes = self.final_proposal_bytes is not None
        if has_json != has_bytes:
            raise ValueError("final proposal JSON and byte count must be provided together")
        if self.kind is LearningCandidateDecisionKind.EDIT_AND_ACCEPT:
            if not has_json:
                raise ValueError("edit-and-accept decisions require a final proposal")
            if self.final_proposal_bytes != len(self.final_proposal_json.encode("utf-8")):
                raise ValueError("final proposal byte count does not match its JSON")
        elif has_json:
            raise ValueError("only edit-and-accept decisions may retain a final proposal")
        return self


class ProjectKnowledgeHead(ProtocolModel):
    """Mutable pointer to the current immutable Knowledge revision."""

    knowledge_id: str
    workspace_id: str
    semantic_key: str
    category: ProjectKnowledgeCategory
    status: ProjectKnowledgeStatus = ProjectKnowledgeStatus.ACTIVE
    current_revision_id: str | None = None
    row_version: int = Field(default=1, ge=1)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    _valid_knowledge = field_validator("knowledge_id")(
        lambda value: validate_prefixed_id(value, LEARNING_KNOWLEDGE_ID_PREFIX)
    )
    _valid_workspace = field_validator("workspace_id")(
        lambda value: validate_prefixed_id(value, "ws")
    )
    _valid_key = field_validator("semantic_key")(_semantic_key)
    _valid_revision = field_validator("current_revision_id")(
        lambda value: (
            None
            if value is None
            else validate_prefixed_id(value, LEARNING_KNOWLEDGE_REVISION_ID_PREFIX)
        )
    )
    _normalize_time = field_validator("created_at", "updated_at", mode="before")(_utc)

    @model_validator(mode="after")
    def ordered_timestamps(self) -> ProjectKnowledgeHead:
        if self.updated_at < self.created_at:
            raise ValueError("knowledge head updated_at must not precede created_at")
        return self


class ProjectKnowledgeRevision(ProtocolModel):
    """Immutable statement and provenance for a Knowledge head."""

    knowledge_revision_id: str
    knowledge_id: str
    workspace_id: str
    revision: int = Field(ge=1)
    statement: str
    statement_digest: str
    source_candidate_id: str | None = None
    source_decision_id: str | None = None
    supersedes_revision_id: str | None = None
    sensitivity: LearningSensitivity = LearningSensitivity.NORMAL
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    created_at: datetime = Field(default_factory=utc_now)
    last_confirmed_at: datetime = Field(default_factory=utc_now)

    _valid_revision_id = field_validator("knowledge_revision_id")(
        lambda value: validate_prefixed_id(value, LEARNING_KNOWLEDGE_REVISION_ID_PREFIX)
    )
    _valid_knowledge = field_validator("knowledge_id")(
        lambda value: validate_prefixed_id(value, LEARNING_KNOWLEDGE_ID_PREFIX)
    )
    _valid_workspace = field_validator("workspace_id")(
        lambda value: validate_prefixed_id(value, "ws")
    )
    _valid_digest = field_validator("statement_digest")(
        lambda value: _digest(value, label="knowledge statement digest")
    )
    _valid_candidate = field_validator("source_candidate_id")(
        lambda value: (
            None if value is None else validate_prefixed_id(value, LEARNING_CANDIDATE_ID_PREFIX)
        )
    )
    _valid_decision = field_validator("source_decision_id")(
        lambda value: (
            None if value is None else validate_prefixed_id(value, LEARNING_DECISION_ID_PREFIX)
        )
    )
    _valid_supersedes = field_validator("supersedes_revision_id")(
        lambda value: (
            None
            if value is None
            else validate_prefixed_id(value, LEARNING_KNOWLEDGE_REVISION_ID_PREFIX)
        )
    )
    _normalize_time = field_validator(
        "valid_from", "valid_until", "created_at", "last_confirmed_at", mode="before"
    )(_utc)

    @field_validator("statement")
    @classmethod
    def bounded_statement(cls, value: str) -> str:
        from morrow.core.learning_safety import normalize_learning_text

        return normalize_learning_text(
            value,
            label="knowledge statement",
            maximum=LEARNING_KNOWLEDGE_STATEMENT_MAX_CHARS,
        )

    @classmethod
    def digest_for(cls, statement: str) -> str:
        return sha256_digest(statement)

    @model_validator(mode="after")
    def valid_provenance_and_window(self) -> ProjectKnowledgeRevision:
        if self.source_candidate_id is None and self.source_decision_id is None:
            raise ValueError("knowledge revision requires candidate or decision provenance")
        if self.statement_digest != self.digest_for(self.statement):
            raise ValueError("knowledge statement digest does not match statement")
        if self.valid_from is not None and self.valid_until is not None:
            if self.valid_until <= self.valid_from:
                raise ValueError("knowledge valid_until must follow valid_from")
        if self.last_confirmed_at < self.created_at:
            raise ValueError("knowledge confirmation time must not precede creation")
        return self


class ProjectKnowledgeEvidenceLink(ProtocolModel):
    """Workspace-scoped link from a Knowledge revision to bounded evidence."""

    workspace_id: str
    knowledge_revision_id: str
    evidence_id: str

    _valid_workspace = field_validator("workspace_id")(
        lambda value: validate_prefixed_id(value, "ws")
    )
    _valid_revision = field_validator("knowledge_revision_id")(
        lambda value: validate_prefixed_id(value, LEARNING_KNOWLEDGE_REVISION_ID_PREFIX)
    )
    _valid_evidence = field_validator("evidence_id")(
        lambda value: validate_prefixed_id(value, LEARNING_EVIDENCE_ID_PREFIX)
    )


class MemoryWorkspaceState(ProtocolModel):
    """Monotonic Active-memory token used by selection and invalidation later."""

    workspace_id: str
    memory_revision: int = Field(default=0, ge=0)
    row_version: int = Field(default=1, ge=1)
    updated_at: datetime = Field(default_factory=utc_now)

    _valid_workspace = field_validator("workspace_id")(
        lambda value: validate_prefixed_id(value, "ws")
    )
    _normalize_time = field_validator("updated_at", mode="before")(_utc)


__all__ = [
    "LearningCandidateDecision",
    "LearningCandidateDecisionKind",
    "LearningConflictResolution",
    "MemoryWorkspaceState",
    "ProjectKnowledgeEvidenceLink",
    "ProjectKnowledgeHead",
    "ProjectKnowledgeRevision",
    "ProjectKnowledgeStatus",
]
