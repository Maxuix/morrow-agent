"""Typed contracts for bounded, redacted durable Artifact evidence.

Artifact metadata is operational state; this module deliberately contains no
filesystem or SQLite implementation.  The byte store and journal adapters use
these models as their validation boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path

from pydantic import Field, field_validator, model_validator

from morrow.core.domain import (
    AGENT_RUN_ID_PREFIX,
    ARTIFACT_ID_PREFIX,
    DIGEST_PATTERN,
    SESSION_ID_PREFIX,
    TASK_OUTCOME_ID_PREFIX,
    TASK_RUN_ID_PREFIX,
    TURN_ID_PREFIX,
    WORKSPACE_ID_PREFIX,
    canonical_json_bytes,
    refuse_secret_material,
    require_payload_budget,
    validate_prefixed_id,
)
from morrow.core.models import ProtocolModel, utc_now

ARTIFACT_METADATA_MAX_BYTES = 32 * 1024
ARTIFACT_EXCERPT_MAX_BYTES = 8 * 1024
ARTIFACT_MAX_BYTES = 64 * 1024 * 1024
TASK_ARTIFACT_MAX_BYTES = 256 * 1024 * 1024
ARTIFACT_CONTENT_MAX_BYTES = ARTIFACT_MAX_BYTES
ARTIFACT_FILE_SUFFIX = ".artifact"
ARTIFACT_TEMP_SUFFIX = ".artifact.tmp"

ARTIFACT_PAYLOAD_BUDGETS: dict[str, int] = {
    "artifact_metadata": ARTIFACT_METADATA_MAX_BYTES,
    "artifact_excerpt": ARTIFACT_EXCERPT_MAX_BYTES,
    "artifact_content": ARTIFACT_MAX_BYTES,
    "task_artifacts": TASK_ARTIFACT_MAX_BYTES,
}


class ArtifactKind(StrEnum):
    COMMAND_OUTPUT = "command_output"
    PATCH = "patch"
    DIFF = "diff"
    TEST_REPORT = "test_report"
    DIAGNOSTIC_REPORT = "diagnostic_report"
    TASK_SUMMARY = "task_summary"
    CONTEXT_SUMMARY = "context_summary"


class ArtifactSensitivity(StrEnum):
    """Only explicitly safe classifications may be persisted."""

    NON_SENSITIVE = "non_sensitive"
    REDACTED = "redacted"


class ArtifactRetention(StrEnum):
    STANDARD = "standard"
    PINNED = "pinned"


class ArtifactState(StrEnum):
    STAGING = "staging"
    AVAILABLE = "available"
    MISSING = "missing"
    CORRUPT = "corrupt"


ArtifactRetentionState = ArtifactRetention
ArtifactSensitivityState = ArtifactSensitivity


class ArtifactProvenanceKind(StrEnum):
    TOOL_EXECUTION = "tool_execution"
    TASK_OUTCOME = "task_outcome"
    TURN = "turn"
    AGENT_RUN = "agent_run"
    CHECKPOINT = "checkpoint"
    MANUAL = "manual"


_PROVENANCE_PREFIXES = {
    ArtifactProvenanceKind.TOOL_EXECUTION: "tex",
    ArtifactProvenanceKind.TASK_OUTCOME: TASK_OUTCOME_ID_PREFIX,
    ArtifactProvenanceKind.TURN: TURN_ID_PREFIX,
    ArtifactProvenanceKind.AGENT_RUN: AGENT_RUN_ID_PREFIX,
    ArtifactProvenanceKind.CHECKPOINT: "chk",
}


class ArtifactErrorCode(StrEnum):
    BUDGET = "artifact_budget"
    CONFLICT = "artifact_conflict"
    INTEGRITY = "artifact_integrity"
    INVALID = "artifact_invalid"
    MISSING = "artifact_missing"
    PATH = "artifact_path"
    UNAVAILABLE = "artifact_unavailable"


class ArtifactError(RuntimeError):
    """Stable, non-secret Artifact boundary error."""

    def __init__(self, code: ArtifactErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class ArtifactBudgetError(ArtifactError):
    def __init__(self, message: str = "artifact byte budget exceeded") -> None:
        super().__init__(ArtifactErrorCode.BUDGET, message)


class ArtifactIntegrityError(ArtifactError):
    def __init__(
        self,
        code: ArtifactErrorCode = ArtifactErrorCode.INTEGRITY,
        message: str = "artifact bytes failed integrity verification",
    ) -> None:
        super().__init__(code, message)


class ArtifactPathError(ArtifactError):
    def __init__(self, message: str = "artifact path is outside the managed store") -> None:
        super().__init__(ArtifactErrorCode.PATH, message)


class ArtifactProvenanceRef(ProtocolModel):
    kind: ArtifactProvenanceKind
    reference_id: str
    role: str = Field(default="source", min_length=1, max_length=64)

    @field_validator("reference_id")
    @classmethod
    def valid_reference_id(cls, value: str) -> str:
        if value.startswith("chk_"):
            return validate_prefixed_id(value, "chk")
        if not value or len(value) > 128 or any(char in value for char in "/\\\x00"):
            raise ValueError("artifact provenance reference must be an opaque token")
        return value

    @field_validator("role")
    @classmethod
    def clean_role(cls, value: str) -> str:
        cleaned = " ".join(value.split())
        if not cleaned:
            raise ValueError("artifact provenance role must not be empty")
        return cleaned

    @model_validator(mode="after")
    def matches_kind(self) -> ArtifactProvenanceRef:
        expected = _PROVENANCE_PREFIXES.get(self.kind)
        if expected is not None:
            validate_prefixed_id(self.reference_id, expected)
        return self


class ArtifactMetadata(ProtocolModel):
    """SQLite authority for one immutable, ID-addressed Artifact payload."""

    artifact_id: str
    workspace_id: str
    session_id: str | None = None
    task_run_id: str | None = None
    kind: ArtifactKind
    sensitivity: ArtifactSensitivity
    state: ArtifactState = ArtifactState.STAGING
    retention: ArtifactRetention = ArtifactRetention.STANDARD
    sha256: str
    byte_size: int = Field(ge=0, le=ARTIFACT_MAX_BYTES)
    excerpt: str = ""
    provenance_refs: tuple[ArtifactProvenanceRef, ...] = ()
    row_version: int = Field(default=1, ge=1)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @field_validator("artifact_id")
    @classmethod
    def valid_artifact_id(cls, value: str) -> str:
        return validate_prefixed_id(value, ARTIFACT_ID_PREFIX)

    @field_validator("workspace_id")
    @classmethod
    def valid_workspace_id(cls, value: str) -> str:
        return validate_prefixed_id(value, WORKSPACE_ID_PREFIX)

    @field_validator("session_id")
    @classmethod
    def valid_session_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return validate_prefixed_id(value, SESSION_ID_PREFIX)

    @field_validator("task_run_id")
    @classmethod
    def valid_task_run_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return validate_prefixed_id(value, TASK_RUN_ID_PREFIX)

    @field_validator("sha256")
    @classmethod
    def valid_sha256(cls, value: str) -> str:
        if not DIGEST_PATTERN.match(value):
            raise ValueError("artifact hash must be a SHA-256 hex digest")
        return value

    @field_validator("excerpt")
    @classmethod
    def bounded_excerpt(cls, value: str) -> str:
        require_payload_budget(
            value.encode("utf-8"), ARTIFACT_EXCERPT_MAX_BYTES, label="artifact excerpt"
        )
        refuse_secret_material(value, label="artifact excerpt")
        return value

    @field_validator("provenance_refs")
    @classmethod
    def bounded_provenance(
        cls, values: tuple[ArtifactProvenanceRef, ...]
    ) -> tuple[ArtifactProvenanceRef, ...]:
        if len(values) > 32:
            raise ValueError("artifact has too many provenance references")
        if len(set(values)) != len(values):
            raise ValueError("artifact provenance references must be unique")
        return values

    @model_validator(mode="after")
    def validate_scope_and_budget(self) -> ArtifactMetadata:
        if self.task_run_id is not None and self.session_id is None:
            raise ValueError("task-scoped artifact requires a session")
        payload = canonical_json_bytes(self.model_dump(mode="json"))
        require_payload_budget(payload, ARTIFACT_METADATA_MAX_BYTES, label="artifact metadata")
        refuse_secret_material(payload, label="artifact metadata")
        return self

    @property
    def filename(self) -> str:
        return f"{self.artifact_id}{ARTIFACT_FILE_SUFFIX}"

    @property
    def provenance(self) -> tuple[ArtifactProvenanceRef, ...]:
        return self.provenance_refs

    @property
    def retention_state(self) -> ArtifactRetention:
        return self.retention


@dataclass(frozen=True)
class ArtifactRead:
    metadata: ArtifactMetadata
    content: bytes


@dataclass(frozen=True)
class ArtifactOrphanCandidate:
    artifact_id: str | None
    path: Path
    reason: str


@dataclass(frozen=True)
class ArtifactOrphanReport:
    candidates: tuple[ArtifactOrphanCandidate, ...]


@dataclass(frozen=True)
class ArtifactRetentionReport:
    referenced: tuple[str, ...]
    pinned: tuple[str, ...]
    candidates: tuple[str, ...]
