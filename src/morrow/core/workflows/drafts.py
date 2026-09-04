"""Durable pre-freeze Workflow Draft contracts."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated

from pydantic import Field, field_validator, model_validator

from morrow.core.agent_definitions import Digest, WorkspaceId
from morrow.core.models import ProtocolModel
from morrow.core.workflows.definitions import WorkflowDefinitionSource, WorkflowRevisionId

WorkflowDraftId = Annotated[str, Field(pattern=r"^wdraft_[A-Za-z0-9_-]+$")]


class WorkflowDraftStatus(StrEnum):
    DRAFT = "draft"
    VALIDATING = "validating"
    VALID = "valid"
    INVALID = "invalid"
    REJECTED = "rejected"
    FROZEN = "frozen"


class WorkflowDraftDiagnostic(ProtocolModel):
    severity: str = Field(pattern=r"^(error|warning)$")
    code: str = Field(min_length=1, max_length=128)
    message: str = Field(min_length=1, max_length=2048)
    node_id: str | None = Field(default=None, max_length=128)
    edge_id: str | None = Field(default=None, max_length=260)


class WorkflowDraft(ProtocolModel):
    """Mutable OCC editor state; immutable Revisions remain a separate authority."""

    draft_id: WorkflowDraftId
    workspace_id: WorkspaceId
    source: WorkflowDefinitionSource
    source_hash: Digest
    base_workflow_revision_id: WorkflowRevisionId | None = None
    base_head_row_version: int = Field(default=0, ge=0, strict=True)
    base_source_revision: int = Field(default=0, ge=0, strict=True)
    base_definition_source_hash: Digest | None = None
    status: WorkflowDraftStatus = WorkflowDraftStatus.DRAFT
    diagnostics: tuple[WorkflowDraftDiagnostic, ...] = Field(default=(), max_length=256)
    frozen_workflow_revision_id: WorkflowRevisionId | None = None
    row_version: int = Field(ge=1, strict=True)
    created_at: datetime
    updated_at: datetime

    @field_validator("created_at", "updated_at")
    @classmethod
    def aware_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Workflow Draft timestamps must be timezone-aware")
        return value

    @model_validator(mode="after")
    def consistent_state(self):
        if self.source_hash != self.source.content_hash:
            raise ValueError("Workflow Draft source hash mismatch")
        if self.updated_at < self.created_at:
            raise ValueError("Workflow Draft timestamps are out of order")
        has_errors = any(item.severity == "error" for item in self.diagnostics)
        if self.status is WorkflowDraftStatus.VALID and has_errors:
            raise ValueError("valid Workflow Draft cannot carry errors")
        if self.status is WorkflowDraftStatus.INVALID and not has_errors:
            raise ValueError("invalid Workflow Draft requires an error")
        if (self.status is WorkflowDraftStatus.FROZEN) != (
            self.frozen_workflow_revision_id is not None
        ):
            raise ValueError("frozen Workflow Draft must reference its Revision")
        return self


class WorkflowDraftView(ProtocolModel):
    draft: WorkflowDraft
    stale_reasons: tuple[str, ...] = Field(default=(), max_length=16)
