"""Bounded read models for immutable Memory selections."""

from __future__ import annotations

from datetime import datetime

from pydantic import Field, field_validator

from morrow.core.domain import DIGEST_PATTERN, validate_prefixed_id
from morrow.core.memory_selection import MemorySelection
from morrow.core.models import ProtocolModel


class MemorySelectionSummary(ProtocolModel):
    """Metadata safe to show in a bounded selection listing."""

    selection_id: str
    workspace_id: str
    source_memory_revision: int = Field(ge=0)
    item_count: int = Field(ge=0)
    omitted_count: int = Field(ge=0)
    rendered_chars: int = Field(ge=0)
    selection_digest: str
    created_at: datetime
    agent_run_ids: tuple[str, ...] = ()

    @classmethod
    def from_selection(
        cls,
        selection: MemorySelection,
        *,
        agent_run_ids: tuple[str, ...] = (),
    ) -> MemorySelectionSummary:
        return cls(
            selection_id=selection.selection_id,
            workspace_id=selection.workspace_id,
            source_memory_revision=selection.source_memory_revision,
            item_count=selection.item_count,
            omitted_count=selection.omitted_count,
            rendered_chars=selection.rendered_chars,
            selection_digest=selection.selection_digest,
            created_at=selection.created_at,
            agent_run_ids=agent_run_ids,
        )

    @field_validator("selection_id")
    @classmethod
    def validate_selection_id(cls, value: str) -> str:
        return validate_prefixed_id(value, "msel")

    @field_validator("workspace_id")
    @classmethod
    def validate_workspace_id(cls, value: str) -> str:
        return validate_prefixed_id(value, "ws")

    @field_validator("selection_digest")
    @classmethod
    def validate_digest(cls, value: str) -> str:
        if not DIGEST_PATTERN.fullmatch(value):
            raise ValueError("memory selection digest must be a SHA-256 hex digest")
        return value


class MemorySelectionView(ProtocolModel):
    """One selection and the bounded AgentRuns that reference it."""

    selection: MemorySelection
    agent_run_ids: tuple[str, ...] = ()


__all__ = ["MemorySelectionSummary", "MemorySelectionView"]
