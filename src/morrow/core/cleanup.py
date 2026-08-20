"""Deterministic, non-history-mutating orphan cleanup contracts."""

from __future__ import annotations

from pydantic import Field

from morrow.core.models import ProtocolModel


class OrphanCleanupReport(ProtocolModel):
    dry_run: bool
    inspected: int = Field(ge=0)
    eligible: int = Field(ge=0)
    removed: int = Field(ge=0)
    quarantined: int = Field(default=0, ge=0)
    refused: int = Field(ge=0)
    reasons: tuple[str, ...] = ()
