"""Durable deterministic context-checkpoint and Session-lineage contracts."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import Field, field_validator, model_validator

from morrow.core.domain import (
    AGENT_RUN_ID_PREFIX,
    CHECKPOINT_ID_PREFIX,
    SESSION_ID_PREFIX,
    TASK_RUN_ID_PREFIX,
    WORKSPACE_ID_PREFIX,
    ArtifactReference,
    canonical_json_bytes,
    refuse_secret_material,
    validate_prefixed_id,
)
from morrow.core.models import ProtocolModel, utc_now

CONTEXT_CHECKPOINT_MAX_BYTES = 32 * 1024
CONTEXT_SECTION_MAX_BYTES = 8 * 1024
CONTEXT_CHECKPOINT_MAX_RECORDS = 512
CONTEXT_CHECKPOINT_MAX_SECTIONS = 128
CONTEXT_CHECKPOINT_MAX_ARTIFACT_REFS = 64


class CheckpointOmissionReason(StrEnum):
    OLDER_TURN = "older_turn"
    TOOL_OUTPUT = "tool_output"
    ARTIFACT_REFERENCE = "artifact_reference"
    BUDGET = "budget"
    RECOVERY_CONTEXT = "recovery_context"


class ContextCheckpointSection(ProtocolModel):
    """One deterministic, provenance-bounded section shown in a prompt projection."""

    kind: str = Field(min_length=1, max_length=64)
    content: str = Field(max_length=CONTEXT_SECTION_MAX_BYTES)
    source_start_position: int = Field(ge=0)
    source_end_position: int = Field(ge=1)
    artifact_refs: tuple[ArtifactReference, ...] = ()

    @field_validator("kind")
    @classmethod
    def clean_kind(cls, value: str) -> str:
        cleaned = "_".join(value.strip().casefold().split())
        if not cleaned:
            raise ValueError("checkpoint section kind must not be empty")
        return cleaned

    @field_validator("content")
    @classmethod
    def bounded_content(cls, value: str) -> str:
        if len(value.encode("utf-8")) > CONTEXT_SECTION_MAX_BYTES:
            raise ValueError("checkpoint section exceeds its byte budget")
        refuse_secret_material(value, label="checkpoint section")
        return value

    @field_validator("artifact_refs")
    @classmethod
    def bounded_artifacts(
        cls, value: tuple[ArtifactReference, ...]
    ) -> tuple[ArtifactReference, ...]:
        if len(value) > CONTEXT_CHECKPOINT_MAX_ARTIFACT_REFS:
            raise ValueError("checkpoint section contains too many Artifact references")
        if len(set(value)) != len(value):
            raise ValueError("checkpoint section Artifact references must be unique")
        return value

    @model_validator(mode="after")
    def valid_range(self) -> ContextCheckpointSection:
        if self.source_end_position <= self.source_start_position:
            raise ValueError("checkpoint section source range must be non-empty")
        return self


class ContextCheckpointOmission(ProtocolModel):
    """A typed reason for a source range omitted from the prompt projection."""

    reason: CheckpointOmissionReason
    source_start_position: int = Field(ge=0)
    source_end_position: int = Field(ge=1)
    record_ids: tuple[str, ...] = ()

    @field_validator("record_ids")
    @classmethod
    def bounded_record_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) > CONTEXT_CHECKPOINT_MAX_RECORDS:
            raise ValueError("checkpoint omission contains too many records")
        if len(value) != len(set(value)):
            raise ValueError("checkpoint omission record IDs must be unique")
        for record_id in value:
            validate_prefixed_id(record_id, "rec")
        return value

    @model_validator(mode="after")
    def valid_range(self) -> ContextCheckpointOmission:
        if self.source_end_position <= self.source_start_position:
            raise ValueError("checkpoint omission source range must be non-empty")
        return self


class ContextCheckpoint(ProtocolModel):
    """Immutable deterministic context projection over immutable conversation records."""

    checkpoint_id: str
    workspace_id: str
    session_id: str
    task_run_id: str | None = None
    source_agent_run_id: str | None = None
    codec: str = Field(default="deterministic", min_length=1, max_length=64)
    method_version: str = Field(default="v1", min_length=1, max_length=32)
    source_start_record_id: str | None = None
    source_start_position: int = Field(default=0, ge=0)
    source_end_record_id: str
    source_end_position: int = Field(ge=1)
    retained_record_ids: tuple[str, ...] = ()
    sections: tuple[ContextCheckpointSection, ...] = ()
    omitted_sections: tuple[ContextCheckpointOmission, ...] = ()
    artifact_refs: tuple[ArtifactReference, ...] = ()
    input_bytes: int = Field(default=0, ge=0)
    output_bytes: int = Field(default=0, ge=0)
    request_estimate_chars: int = Field(default=0, ge=0)
    created_at: datetime = Field(default_factory=utc_now)

    @field_validator("checkpoint_id")
    @classmethod
    def valid_checkpoint_id(cls, value: str) -> str:
        return validate_prefixed_id(value, CHECKPOINT_ID_PREFIX)

    @field_validator("workspace_id")
    @classmethod
    def valid_workspace_id(cls, value: str) -> str:
        return validate_prefixed_id(value, WORKSPACE_ID_PREFIX)

    @field_validator("session_id")
    @classmethod
    def valid_session_id(cls, value: str) -> str:
        return validate_prefixed_id(value, SESSION_ID_PREFIX)

    @field_validator("task_run_id")
    @classmethod
    def valid_task_run_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return validate_prefixed_id(value, TASK_RUN_ID_PREFIX)

    @field_validator("source_agent_run_id")
    @classmethod
    def valid_agent_run_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return validate_prefixed_id(value, AGENT_RUN_ID_PREFIX)

    @field_validator("source_start_record_id", "source_end_record_id")
    @classmethod
    def valid_record_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return validate_prefixed_id(value, "rec")

    @field_validator("retained_record_ids")
    @classmethod
    def bounded_retained_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) > CONTEXT_CHECKPOINT_MAX_RECORDS:
            raise ValueError("checkpoint retains too many records")
        if len(value) != len(set(value)):
            raise ValueError("checkpoint retained record IDs must be unique")
        for record_id in value:
            validate_prefixed_id(record_id, "rec")
        return value

    @field_validator("sections")
    @classmethod
    def bounded_sections(
        cls, value: tuple[ContextCheckpointSection, ...]
    ) -> tuple[ContextCheckpointSection, ...]:
        if len(value) > CONTEXT_CHECKPOINT_MAX_SECTIONS:
            raise ValueError("checkpoint contains too many sections")
        return value

    @field_validator("omitted_sections")
    @classmethod
    def bounded_omissions(
        cls, value: tuple[ContextCheckpointOmission, ...]
    ) -> tuple[ContextCheckpointOmission, ...]:
        if len(value) > CONTEXT_CHECKPOINT_MAX_SECTIONS:
            raise ValueError("checkpoint contains too many omission sections")
        return value

    @field_validator("artifact_refs")
    @classmethod
    def bounded_artifact_refs(
        cls, value: tuple[ArtifactReference, ...]
    ) -> tuple[ArtifactReference, ...]:
        if len(value) > CONTEXT_CHECKPOINT_MAX_ARTIFACT_REFS:
            raise ValueError("checkpoint contains too many Artifact references")
        if len(set(value)) != len(value):
            raise ValueError("checkpoint Artifact references must be unique")
        return value

    @model_validator(mode="after")
    def valid_projection(self) -> ContextCheckpoint:
        if self.task_run_id is not None and self.session_id is None:
            raise ValueError("checkpoint task scope requires a session")
        if self.source_end_position <= self.source_start_position:
            raise ValueError("checkpoint source range must be non-empty")
        if self.source_end_position - 1 < self.source_start_position:
            raise ValueError("checkpoint source range is invalid")
        payload = canonical_json_bytes(self.model_dump(mode="json"))
        if len(payload) > CONTEXT_CHECKPOINT_MAX_BYTES:
            raise ValueError("checkpoint metadata exceeds its byte budget")
        refuse_secret_material(payload, label="checkpoint metadata")
        return self


class SessionLineage(ProtocolModel):
    """Immutable parent-prefix provenance for one forked Session."""

    workspace_id: str
    child_session_id: str
    parent_session_id: str
    cut_record_id: str
    cut_position: int = Field(ge=1)
    checkpoint_id: str | None = None
    reason: str = Field(min_length=1, max_length=256)
    created_at: datetime = Field(default_factory=utc_now)

    @field_validator("workspace_id")
    @classmethod
    def valid_workspace_id(cls, value: str) -> str:
        return validate_prefixed_id(value, WORKSPACE_ID_PREFIX)

    @field_validator("child_session_id", "parent_session_id")
    @classmethod
    def valid_session_id(cls, value: str) -> str:
        return validate_prefixed_id(value, SESSION_ID_PREFIX)

    @field_validator("cut_record_id")
    @classmethod
    def valid_cut_record_id(cls, value: str) -> str:
        return validate_prefixed_id(value, "rec")

    @field_validator("checkpoint_id")
    @classmethod
    def valid_checkpoint_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return validate_prefixed_id(value, CHECKPOINT_ID_PREFIX)

    @model_validator(mode="after")
    def distinct_sessions(self) -> SessionLineage:
        if self.child_session_id == self.parent_session_id:
            raise ValueError("Session lineage cannot point to itself")
        refuse_secret_material(self.reason, label="Session fork reason")
        return self
