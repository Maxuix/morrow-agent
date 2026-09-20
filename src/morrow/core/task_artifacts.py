"""Read-only contracts for the session-scoped task/artifact workbench.

The contracts keep identity useful to the client without making opaque IDs the
user-facing description of an item.  Content is always fetched through the
authorized session/task route; the list projection contains bounded excerpts
and provenance only.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field, field_validator

from morrow.core.artifacts import ArtifactKind
from morrow.core.domain import (
    ARTIFACT_ID_PREFIX,
    SESSION_ID_PREFIX,
    TASK_RUN_ID_PREFIX,
    validate_prefixed_id,
)
from morrow.core.models import ProtocolModel

TASK_ARTIFACTS_SCHEMA_VERSION = 1
TASK_ARTIFACT_PREVIEW_MAX_BYTES = 64 * 1024
TASK_ARTIFACT_PREVIEW_TOTAL_BYTES = 512 * 1024

ArtifactAvailability = Literal["available", "staging", "missing", "corrupt", "unavailable"]
TaskArtifactSource = Literal[
    "task_evidence", "registered_result", "workflow_output", "command_output"
]
ContentEncoding = Literal["utf8", "binary", "unknown"]


def _bounded_text(value: str, limit: int) -> str:
    if not isinstance(value, str):
        raise ValueError("text must be a string")
    return value[:limit]


class ArtifactProvenanceWire(ProtocolModel):
    kind: str = Field(min_length=1, max_length=64)
    role: str = Field(min_length=1, max_length=64)
    reference_id: str = Field(min_length=1, max_length=128)

    @field_validator("kind", "role", "reference_id")
    @classmethod
    def bounded_tokens(cls, value: str) -> str:
        if any(char in value for char in "/\\\x00"):
            raise ValueError("provenance token is invalid")
        return " ".join(value.split())[:128]


class TaskArtifactWire(ProtocolModel):
    """One registered result/evidence row in the TaskArtifacts projection."""

    artifact_id: str
    kind: ArtifactKind
    name: str = Field(min_length=1, max_length=512)
    path: str | None = Field(default=None, max_length=512)
    mime: str | None = Field(default=None, max_length=128)
    source: TaskArtifactSource
    availability: ArtifactAvailability
    byte_size: int = Field(ge=0)
    excerpt: str = Field(default="", max_length=8192)
    diff: str | None = Field(default=None, max_length=TASK_ARTIFACT_PREVIEW_MAX_BYTES)
    diff_truncated: bool = False
    content_complete: bool | None = None
    content_encoding: ContentEncoding = "unknown"
    omission_reason: str | None = Field(default=None, max_length=256)
    retention: str = Field(min_length=1, max_length=32)
    row_version: int = Field(ge=1)
    session_id: str | None = Field(default=None, max_length=128)
    task_run_id: str | None = Field(default=None, max_length=128)
    workflow_run_id: str | None = Field(default=None, max_length=128)
    node_run_id: str | None = Field(default=None, max_length=128)
    output_slot: str | None = Field(default=None, max_length=128)
    provenance: tuple[ArtifactProvenanceWire, ...] = Field(default=(), max_length=32)
    created_at: datetime
    updated_at: datetime

    @field_validator("artifact_id")
    @classmethod
    def artifact_identity(cls, value: str) -> str:
        return validate_prefixed_id(value, ARTIFACT_ID_PREFIX)

    @field_validator("session_id")
    @classmethod
    def artifact_session_identity(cls, value: str | None) -> str | None:
        return None if value is None else validate_prefixed_id(value, SESSION_ID_PREFIX)

    @field_validator("task_run_id")
    @classmethod
    def artifact_task_identity(cls, value: str | None) -> str | None:
        return None if value is None else validate_prefixed_id(value, TASK_RUN_ID_PREFIX)

    @field_validator("workflow_run_id")
    @classmethod
    def artifact_workflow_identity(cls, value: str | None) -> str | None:
        return None if value is None else validate_prefixed_id(value, "wrun")

    @field_validator("node_run_id")
    @classmethod
    def artifact_node_identity(cls, value: str | None) -> str | None:
        return None if value is None else validate_prefixed_id(value, "nrun")

    @field_validator("name")
    @classmethod
    def artifact_name(cls, value: str) -> str:
        return _bounded_text(value, 512)

    @field_validator("path")
    @classmethod
    def relative_path(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if value == "." or value.startswith("/") or "\\" in value or "\x00" in value:
            raise ValueError("artifact path must be relative")
        if any(part in {"", ".", ".."} for part in value.split("/")):
            raise ValueError("artifact path must be relative")
        return value


class TaskFileChangeWire(ProtocolModel):
    """A file change with an explicit source and honest representation state."""

    path: str = Field(min_length=1, max_length=512)
    operation: str = Field(min_length=1, max_length=64)
    status: str = Field(min_length=1, max_length=64)
    artifact_id: str | None = None
    source: TaskArtifactSource = "task_evidence"
    availability: ArtifactAvailability
    mime: str | None = Field(default=None, max_length=128)
    diff: str | None = Field(default=None, max_length=TASK_ARTIFACT_PREVIEW_MAX_BYTES)
    diff_truncated: bool = False
    content_complete: bool | None = None
    content_encoding: ContentEncoding = "unknown"
    omission_reason: str | None = Field(default=None, max_length=256)
    message: str | None = Field(default=None, max_length=256)
    updated_at: datetime | None = None

    @field_validator("path")
    @classmethod
    def relative_change_path(cls, value: str) -> str:
        if value.startswith("/") or "\\" in value or "\x00" in value:
            raise ValueError("changed path must be relative")
        if any(part in {"", ".", ".."} for part in value.split("/")):
            raise ValueError("changed path must be relative")
        return value

    @field_validator("artifact_id")
    @classmethod
    def change_artifact_identity(cls, value: str | None) -> str | None:
        return None if value is None else validate_prefixed_id(value, ARTIFACT_ID_PREFIX)


class CommandOutputWire(ProtocolModel):
    """Durable command metadata plus a bounded output association."""

    tool_execution_id: str
    tool_name: str = Field(min_length=1, max_length=64)
    command_class: str | None = Field(default=None, max_length=64)
    cwd: str | None = Field(default=None, max_length=512)
    ordinal: int = Field(ge=1)
    state: str = Field(min_length=1, max_length=32)
    disposition: str = Field(min_length=1, max_length=32)
    started_at: datetime
    ended_at: datetime | None = None
    duration_ms: int | None = Field(default=None, ge=0, le=120_000)
    exit_code: int | None = Field(default=None, ge=0, le=255)
    signal: int | None = Field(default=None, ge=1, le=255)
    output_artifact_id: str | None = None
    output_availability: Literal["available", "missing", "corrupt", "staging", "not_persisted"]
    output_excerpt: str = Field(default="", max_length=8192)
    output_truncated: bool = False
    output_source: Literal["artifact", "activity", "none"] = "none"
    message: str | None = Field(default=None, max_length=256)

    @field_validator("tool_execution_id")
    @classmethod
    def execution_identity(cls, value: str) -> str:
        return validate_prefixed_id(value, "tex")

    @field_validator("output_artifact_id")
    @classmethod
    def output_artifact_identity(cls, value: str | None) -> str | None:
        return None if value is None else validate_prefixed_id(value, ARTIFACT_ID_PREFIX)


class TaskArtifactsWire(ProtocolModel):
    """Complete read model for the Artifacts and Terminal Inspector tabs."""

    schema_version: Literal[TASK_ARTIFACTS_SCHEMA_VERSION] = TASK_ARTIFACTS_SCHEMA_VERSION
    session_id: str
    task_run_id: str | None = None
    title: str = Field(min_length=1, max_length=256)
    status: str | None = Field(default=None, max_length=64)
    purpose: str | None = Field(default=None, max_length=64)
    workflow_run_id: str | None = None
    workflow_run_ids: tuple[str, ...] = Field(default=(), max_length=32)
    node_run_id: str | None = None
    task: dict[str, object] | None = None
    outcomes: tuple[dict[str, object], ...] = Field(default=(), max_length=128)
    artifacts: tuple[TaskArtifactWire, ...] = Field(default=(), max_length=256)
    files: tuple[TaskFileChangeWire, ...] = Field(default=(), max_length=256)
    command_outputs: tuple[CommandOutputWire, ...] = Field(default=(), max_length=256)
    availability: Literal["available", "partial", "missing", "empty"]
    message: str | None = Field(default=None, max_length=256)

    @field_validator("session_id")
    @classmethod
    def session_identity(cls, value: str) -> str:
        return validate_prefixed_id(value, SESSION_ID_PREFIX)

    @field_validator("task_run_id")
    @classmethod
    def task_identity(cls, value: str | None) -> str | None:
        return None if value is None else validate_prefixed_id(value, TASK_RUN_ID_PREFIX)

    @field_validator("workflow_run_id")
    @classmethod
    def workflow_identity(cls, value: str | None) -> str | None:
        return None if value is None else validate_prefixed_id(value, "wrun")

    @field_validator("workflow_run_ids")
    @classmethod
    def workflow_identities(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(validate_prefixed_id(value, "wrun") for value in values)

    @field_validator("node_run_id")
    @classmethod
    def node_identity(cls, value: str | None) -> str | None:
        return None if value is None else validate_prefixed_id(value, "nrun")
