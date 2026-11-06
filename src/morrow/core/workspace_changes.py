"""Versioned file-change and restore intent contracts; no source text in metadata."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Protocol

from pydantic import Field, field_validator, model_validator

from morrow.core.capabilities import _clean_relative_path
from morrow.core.models import ProtocolModel, utc_now

MAX_SNAPSHOT_BYTES = 8 * 1024 * 1024
WORKSPACE_SNAPSHOT_BYTES = 2 * 1024 * 1024 * 1024


class FileManifest(ProtocolModel):
    exists: bool = False
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    byte_size: int = Field(default=0, ge=0, le=MAX_SNAPSHOT_BYTES)
    mode: int | None = Field(default=None, ge=0, le=0o7777)
    snapshot_id: str | None = None
    file_identity: tuple[int, int] | None = None

    @model_validator(mode="after")
    def consistent(self):
        if self.exists:
            if self.sha256 is None or self.mode is None:
                raise ValueError("existing file requires digest and mode")
        elif (
            self.sha256 is not None
            or self.mode is not None
            or self.byte_size
            or self.snapshot_id
            or self.file_identity
        ):
            raise ValueError("absent file cannot contain snapshot metadata")
        return self


class WorkspaceChange(ProtocolModel):
    schema_version: Literal[1] = 1
    change_id: str
    workspace_id: str
    session_id: str
    task_run_id: str
    agent_run_id: str | None = None
    tool_execution_id: str | None = None
    mutation_key: str
    workspace_sequence: int = Field(default=0, ge=0)
    root_identity: tuple[int, int] | None = None
    added_lines: int = Field(default=0, ge=0)
    removed_lines: int = Field(default=0, ge=0)
    path: str
    destination_path: str | None = None
    source_parent_identity: tuple[int, int] | None = None
    destination_parent_identity: tuple[int, int] | None = None
    operation: Literal["create", "patch", "replace", "delete", "move", "rename", "restore"]
    origin: Literal["tool", "restore"] = "tool"
    related_change_ids: tuple[str, ...] = ()
    restore_operation_id: str | None = None
    before: FileManifest
    after: FileManifest
    effect_state: Literal["prepared", "applied", "not_applied", "outcome_unknown"] = "prepared"
    coverage: Literal["complete", "metadata_only"] = "complete"
    omission_reason: str | None = None
    review_state: Literal["unreviewed", "reviewed"] = "unreviewed"
    reviewed_digest: str | None = None
    row_version: int = Field(default=1, ge=1)
    created_at: datetime = Field(default_factory=utc_now)

    _path = field_validator("path")(_clean_relative_path)

    @field_validator("destination_path")
    @classmethod
    def clean_destination(cls, value):
        return _clean_relative_path(value) if value is not None else None


class RestoreItem(ProtocolModel):
    change_ids: tuple[str, ...] = Field(min_length=1, max_length=1000)
    path: str
    expected: FileManifest
    desired: FileManifest
    parent_identity: tuple[int, int]
    destination_path: str | None = None
    destination_parent_identity: tuple[int, int] | None = None
    structure: Literal["delete", "move"] | None = None
    published_identity: tuple[int, int] | None = None
    file_identity: tuple[int, int] | None = None
    hunk_ids: tuple[str, ...] = Field(default=(), max_length=200)
    capture_name: str
    temporary_name: str
    change_id: str | None = None
    state: Literal[
        "pending", "applied", "target_reached", "conflict", "not_executed", "needs_reconciliation"
    ] = "pending"
    reason: str | None = None

    _path = field_validator("path")(_clean_relative_path)

    @field_validator("destination_path")
    @classmethod
    def clean_destination(cls, value):
        return _clean_relative_path(value) if value is not None else None


class RestoreOperation(ProtocolModel):
    schema_version: Literal[1] = 1
    operation_id: str
    workspace_id: str
    session_id: str
    command_id: str
    request_digest: str
    preview_digest: str
    selection_digest: str
    selectors: dict[str, str | None] = {}
    root_identity: tuple[int, int]
    state: Literal[
        "previewed",
        "prepared",
        "applying",
        "completed",
        "blocked",
        "partially_applied",
        "needs_reconciliation",
    ] = "previewed"
    items: tuple[RestoreItem, ...] = Field(min_length=1, max_length=50)
    created_at: datetime
    expires_at: datetime
    row_version: int = Field(default=1, ge=1)
    apply_command_id: str | None = None
    apply_digest: str | None = None


class WorkspaceChangeJournalPort(Protocol):
    def get(self, workspace_id: str, change_id: str) -> WorkspaceChange | None: ...
    def insert(self, change: WorkspaceChange) -> WorkspaceChange: ...
    def save(self, change: WorkspaceChange) -> WorkspaceChange: ...
