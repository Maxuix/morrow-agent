"""Catalog projection contracts: definitions, versions, bindings and selections.

Four concepts stay separate: SkillDefinition (stable identity), SkillVersion
(immutable managed content), SkillBinding (desired enabled/pinned state) and
SkillSelection (exact version chosen for one AgentRun). Bindings live in YAML
(Subplan 66); selections and contexts land in the v14 store (Subplan 67).
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from morrow.core.models import ProtocolModel

from .bindings import SkillBinding
from .selection import SkillSelection
from .trust import SourceKind, TrustLevel

__all__ = [
    "SkillAvailability",
    "SkillBinding",
    "SkillCatalogEntry",
    "SkillConflictStatus",
    "SkillDefinition",
    "SkillSelection",
    "SkillVersion",
]


class SkillConflictStatus(StrEnum):
    NONE = "none"
    IDENTITY_CONFLICT = "identity_conflict"
    NAME_CONFLICT = "name_conflict"


class SkillAvailability(StrEnum):
    AVAILABLE = "available"
    INVALID = "invalid"
    CONFLICTED = "conflicted"
    UNAVAILABLE = "unavailable"


class SkillDefinition(ProtocolModel):
    """Stable identity plus the catalog view for one (scope, source, skill_id)."""

    skill_id: str
    name: str
    description: str | None = None
    source_kind: SourceKind
    scope_id: str | None = None  # None means global
    availability: SkillAvailability = SkillAvailability.UNAVAILABLE
    conflict_status: SkillConflictStatus = SkillConflictStatus.NONE
    effective_trust: TrustLevel = TrustLevel.UNKNOWN


class SkillVersion(ProtocolModel):
    """One immutable managed package version (the envelope's catalog projection)."""

    version_id: str
    skill_id: str
    display_version: str | None = None
    tree_digest: str
    file_count: int
    total_bytes: int
    source_kind: SourceKind
    scope_id: str | None = None
    provenance: str = ""
    evidence_refs: tuple[str, ...] = ()
    effective_trust: TrustLevel = TrustLevel.UNKNOWN
    created_at: datetime | None = None


class SkillCatalogEntry(ProtocolModel):
    """One row of the truthful catalog view; nothing is enabled by viewing."""

    definition: SkillDefinition
    versions: tuple[SkillVersion, ...] = ()
    newest_available: SkillVersion | None = None
    validation_errors: tuple[str, ...] = ()
    effective_trust: TrustLevel = TrustLevel.UNKNOWN
    requested_trust: TrustLevel | None = None
