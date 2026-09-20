"""Bounded Skill catalog plus Binding status projections."""

from __future__ import annotations

import unicodedata

from morrow.adapters.state.extension_yaml import ExtensionYamlLoadStatus
from morrow.application.skills.bindings import SkillBindingService
from morrow.core.models import ProtocolModel
from morrow.core.skills.bindings import SkillBinding
from morrow.core.skills.catalog import (
    SkillAvailability,
    SkillCatalogEntry,
    SkillConflictStatus,
    SkillVersion,
)
from morrow.core.skills.trust import SourceKind, TrustLevel


class SkillQueryError(ValueError):
    """A bounded Skill query cannot resolve its requested identity."""


class SkillStatusView(ProtocolModel):
    skill_id: str
    name: str
    scope_id: str | None = None
    source_kind: SourceKind
    availability: SkillAvailability
    conflict_status: SkillConflictStatus
    effective_trust: TrustLevel
    requested_trust: TrustLevel | None = None
    versions: tuple[SkillVersion, ...] = ()
    binding: SkillBinding | None = None
    validation_errors: tuple[str, ...] = ()

    @property
    def enabled(self) -> bool:
        return self.binding.enabled if self.binding else False

    @property
    def pinned_version_id(self) -> str | None:
        return self.binding.pinned_version_id if self.binding else None


class SkillQueries:
    """Read-only catalog and binding surface; it never changes Binding or packages."""

    def __init__(self, catalog, bindings: SkillBindingService) -> None:
        self.catalog = catalog
        self.bindings = bindings

    def list(
        self, *, scope_id: str | None = None, limit: int = 100, offset: int = 0
    ) -> tuple[SkillStatusView, ...]:
        if (
            not isinstance(limit, int)
            or isinstance(limit, bool)
            or not 1 <= limit <= 256
            or offset < 0
        ):
            raise SkillQueryError("Skill query limit is invalid")
        view = self.catalog.scan_scope(scope_id)
        binding_map = self._binding_map(scope_id)
        return tuple(
            self._status(entry, binding_map.get(entry.definition.skill_id))
            for entry in view.entries[offset : offset + limit]
        )

    def show(self, skill_id_or_name: str, *, scope_id: str | None = None) -> SkillStatusView:
        binding_map = self._binding_map(scope_id)
        values = tuple(
            self._status(e, binding_map.get(e.definition.skill_id))
            for e in self.catalog.scan_scope(scope_id).entries
        )
        direct = next((item for item in values if item.skill_id == skill_id_or_name), None)
        if direct is not None:
            return direct
        folded = unicodedata.normalize("NFC", skill_id_or_name).casefold()
        matches = [
            item for item in values if unicodedata.normalize("NFC", item.name).casefold() == folded
        ]
        if len(matches) != 1:
            raise SkillQueryError(
                "Skill identity is ambiguous"
                if len(matches) > 1
                else "Skill identity is unavailable"
            )
        return matches[0]

    def _binding_map(self, scope_id: str | None) -> dict[str, SkillBinding]:
        scope = "global" if scope_id is None else "workspace"
        load = self.bindings.load(scope, scope_id=scope_id)
        if load.status is not ExtensionYamlLoadStatus.OK or load.value is None:
            return {}
        return {item.skill_id: item for item in load.value.bindings}

    @staticmethod
    def _status(entry: SkillCatalogEntry, binding: SkillBinding | None) -> SkillStatusView:
        return SkillStatusView(
            skill_id=entry.definition.skill_id,
            name=entry.definition.name,
            scope_id=entry.definition.scope_id,
            source_kind=entry.definition.source_kind,
            availability=entry.definition.availability,
            conflict_status=entry.definition.conflict_status,
            effective_trust=entry.effective_trust,
            requested_trust=entry.requested_trust,
            versions=entry.versions,
            binding=binding,
            validation_errors=entry.validation_errors,
        )


__all__ = ["SkillQueries", "SkillQueryError", "SkillStatusView"]
