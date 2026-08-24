"""Catalog, source and dependency resolution for Skill lifecycle commands."""

from __future__ import annotations

import unicodedata

from morrow.adapters.skills.managed_store import PreparedLocalSkill, SkillPackageError
from morrow.adapters.state.extension_yaml import ExtensionYamlLoadStatus
from morrow.core.skills.bindings import SkillValidationReport
from morrow.core.skills.catalog import (
    SkillAvailability,
    SkillCatalogEntry,
    SkillConflictStatus,
    SkillVersion,
)
from morrow.core.skills.trust import SourceKind

from .errors import SkillLifecycleError


class SkillLifecycleResolutionMixin:
    """Read-only resolution helpers mixed into the lifecycle application service."""

    def _with_conflicts(self, prepared: PreparedLocalSkill) -> SkillValidationReport:
        conflicts: list[str] = []
        try:
            view = self.catalog.scan_scope(prepared.scope_id)
            entry = view.entry(prepared.skill_id, scope_id=prepared.scope_id)
            if entry is not None:
                if entry.definition.conflict_status is not SkillConflictStatus.NONE:
                    conflicts.append(entry.definition.conflict_status.value)
                if any(item.tree_digest != prepared.tree.tree_digest for item in entry.versions):
                    conflicts.append("identity_conflict")
            folded_name = unicodedata.normalize("NFC", prepared.name).casefold()
            if any(
                item.definition.skill_id != prepared.skill_id
                and unicodedata.normalize("NFC", item.definition.name).casefold() == folded_name
                for item in view.entries
            ):
                conflicts.append("name_conflict")
        except Exception:
            conflicts.append("catalog_unavailable")
        return prepared.report.model_copy(update={"conflicts": tuple(sorted(set(conflicts)))})

    def _resolve_entry(
        self,
        skill_id_or_name: str,
        scope_id: str | None,
        source_kind: SourceKind | None,
    ) -> tuple[SkillCatalogEntry, SourceKind]:
        view = self.catalog.scan_scope(scope_id)
        entry = view.entry(skill_id_or_name, scope_id=scope_id)
        if entry is None:
            folded = unicodedata.normalize("NFC", skill_id_or_name).casefold()
            matches = [
                item
                for item in view.entries
                if item.definition.name
                and unicodedata.normalize("NFC", item.definition.name).casefold() == folded
            ]
            if len(matches) > 1:
                raise SkillLifecycleError("ambiguous", "Skill name resolves to multiple identities")
            entry = matches[0] if matches else None
        if entry is None:
            raise SkillLifecycleError("not_found", "Skill identity is unavailable")
        if entry.definition.conflict_status is not SkillConflictStatus.NONE:
            raise SkillLifecycleError("conflict", "Skill identity conflict is not selectable")
        if entry.definition.availability is not SkillAvailability.AVAILABLE:
            raise SkillLifecycleError("unavailable", "Skill is not available")
        sources = {item.source_kind for item in entry.versions}
        if source_kind is None:
            if len(sources) != 1:
                raise SkillLifecycleError("ambiguous", "Skill source is ambiguous; specify source")
            resolved = next(iter(sources))
        else:
            if source_kind not in sources:
                raise SkillLifecycleError("unavailable", "requested Skill source is unavailable")
            resolved = source_kind
        return entry, resolved

    def _resolve_version(
        self,
        entry: SkillCatalogEntry,
        version_id: str,
        source_kind: SourceKind,
    ) -> SkillVersion:
        try:
            from morrow.core.skills.identity import validate_skv_id

            validate_skv_id(version_id)
        except ValueError as exc:
            raise SkillLifecycleError("invalid", "Skill version ID is invalid") from exc
        version = next(
            (
                item
                for item in entry.versions
                if item.version_id == version_id and item.source_kind is source_kind
            ),
            None,
        )
        if version is None:
            raise SkillLifecycleError("unavailable", "requested Skill version is unavailable")
        return version

    def _check_dependencies(
        self,
        entry: SkillCatalogEntry,
        source_kind: SourceKind,
        version_id: str | None,
        scope_id: str | None,
    ) -> None:
        version = next(
            (
                item
                for item in entry.versions
                if item.source_kind is source_kind and item.version_id == version_id
            ),
            None,
        )
        if version is None:
            raise SkillLifecycleError("unavailable", "requested Skill version is unavailable")
        if self.dependency_checker is not None:
            missing = self.dependency_checker(version)
        else:
            missing = self._default_missing_dependencies(version, scope_id)
        if missing:
            raise SkillLifecycleError("unavailable", "Skill dependencies are unavailable")

    def _default_missing_dependencies(
        self, version: SkillVersion, scope_id: str | None
    ) -> tuple[str, ...]:
        if version.source_kind not in {SourceKind.IMPORTED, SourceKind.GENERATED}:
            return ()
        if self.available_tools is None and self.available_mcp_servers is None:
            return ()
        try:
            manifest = self.package_store.manifest_for_version(
                skill_id=version.skill_id,
                version_id=version.version_id,
                source_kind=version.source_kind,
                scope_id=scope_id,
            )
        except SkillPackageError:
            return ("manifest",)
        missing: list[str] = []
        if self.available_tools is not None:
            missing.extend(
                f"tool:{item}"
                for item in manifest.required_tools
                if item not in self.available_tools
            )
        if self.available_mcp_servers is not None:
            missing.extend(
                f"mcp:{item}"
                for item in manifest.required_mcp_servers
                if item not in self.available_mcp_servers
            )
        return tuple(missing[:32])

    def _binding_for(self, skill_id: str, scope_id: str | None):
        scope = "global" if scope_id is None else "workspace"
        load = self.bindings.load(scope, scope_id=scope_id)
        if load.status is not ExtensionYamlLoadStatus.OK or load.value is None:
            return None
        return next((item for item in load.value.bindings if item.skill_id == skill_id), None)

    def _binding_source(self, skill_id: str, scope_id: str | None) -> SourceKind | None:
        binding = self._binding_for(skill_id, scope_id)
        return binding.source_kind if binding else None


__all__ = ["SkillLifecycleResolutionMixin"]
