"""Truthful Skill catalog projection: discovery + exact conflict rules.

No binding, selection or injection happens here; the catalog only reports
valid, invalid, conflicted and unavailable Skills. Conflict rules:

- same (scope, skill_id), same tree digest -> folded into one visible content;
- same (scope, skill_id), different digest -> ``identity_conflict``, both blocked;
- different skill_id, same normalized name -> ``name_conflict``, explicit ambiguity;
- scope/source kinds never silently override each other.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from morrow.adapters.skills.discovery import (
    DiscoveredPackage,
    DiscoveryResult,
    PackageLoadError,
    scan_source_root,
    to_catalog_definition,
    to_catalog_version,
)
from morrow.core.skills.catalog import (
    SkillAvailability,
    SkillCatalogEntry,
    SkillConflictStatus,
)
from morrow.core.skills.trust import SourceKind, TrustLevel, scope_key


@dataclass(frozen=True, slots=True)
class SkillCatalogView:
    entries: tuple[SkillCatalogEntry, ...] = ()
    failures: tuple[PackageLoadError, ...] = ()

    def entry(self, skill_id: str, *, scope_id: str | None = None) -> SkillCatalogEntry | None:
        key = scope_key(scope_id)
        for entry in self.entries:
            if (
                entry.definition.skill_id == skill_id
                and scope_key(entry.definition.scope_id) == key
            ):
                return entry
        return None


class SkillCatalogService:
    """Scan configured roots and project the catalog without enabling anything."""

    def __init__(self, roots: dict[SourceKind, tuple[Path | tuple[Path, str | None], ...]]) -> None:
        self.roots = roots

    def scan(self) -> SkillCatalogView:
        """Deterministic full scan over all configured source roots."""
        result = DiscoveryResult(())
        for source_kind, roots in sorted(self.roots.items(), key=lambda item: item[0].value):
            for root_spec in roots:
                root, scope_id = _root_spec(root_spec, requested_scope=None)
                result = result.merge(
                    scan_source_root(root, source_kind=source_kind, scope_id=scope_id)
                )
        return self._project(result)

    def scan_scope(self, scope_id: str | None = None) -> SkillCatalogView:
        """Scan one scope only (global when scope_id is None)."""
        result = DiscoveryResult(())
        key = scope_key(scope_id)
        for source_kind, roots in sorted(self.roots.items(), key=lambda item: item[0].value):
            for root_spec in roots:
                root, configured_scope = _root_spec(root_spec, requested_scope=scope_id)
                if configured_scope != scope_id:
                    continue
                candidate = scan_source_root(
                    root,
                    source_kind=source_kind,
                    scope_id=configured_scope,
                )
                result = result.merge(
                    DiscoveryResult(
                        packages=tuple(
                            package
                            for package in candidate.packages
                            if scope_key(package.scope_id) == key
                        ),
                        failures=candidate.failures,
                    )
                )
        return self._project(result)

    @staticmethod
    def _project(result: DiscoveryResult) -> SkillCatalogView:
        failures = list(result.failures)
        valid_packages: list[DiscoveredPackage] = []
        for package in result.packages:
            try:
                # Validate the projection boundary too: callers may provide a
                # DiscoveryResult assembled outside scan_source_root.
                to_catalog_version(package)
                to_catalog_definition(package)
            except (KeyError, OverflowError, TypeError, ValueError) as exc:
                failures.append(
                    PackageLoadError(
                        package.version_dir,
                        package.skill_id,
                        (f"catalog projection failed: {type(exc).__name__}",),
                    )
                )
                continue
            valid_packages.append(package)
        by_scope: dict[str, dict[str, list[DiscoveredPackage]]] = {}
        for package in valid_packages:
            scope = scope_key(package.scope_id)
            by_scope.setdefault(scope, {}).setdefault(package.skill_id, []).append(package)

        entries: list[SkillCatalogEntry] = []
        for scope in sorted(by_scope):
            packages_by_id = by_scope[scope]
            name_owners: dict[str, list[str]] = {}
            for skill_id, packages in packages_by_id.items():
                folded = _folded_name(packages[0])
                name_owners.setdefault(folded, []).append(skill_id)
            for skill_id in sorted(packages_by_id):
                entries.append(_project_entry(packages_by_id[skill_id], name_owners))
        return SkillCatalogView(tuple(entries), tuple(failures))


def _root_spec(
    value: Path | tuple[Path, str | None], *, requested_scope: str | None
) -> tuple[Path, str | None]:
    if isinstance(value, tuple):
        return value
    return value, requested_scope


def _folded_name(package: DiscoveredPackage) -> str:
    name = package.manifest.name or package.version_dir.parent.name
    return unicodedata.normalize("NFC", name).casefold()


def _project_entry(
    packages: list[DiscoveredPackage], name_owners: dict[str, list[str]]
) -> SkillCatalogEntry:
    ordered = sorted(packages, key=lambda package: package.version_dir.name)
    digests = {package.tree.tree_digest for package in ordered}
    identity_status = (
        SkillConflictStatus.IDENTITY_CONFLICT if len(digests) > 1 else SkillConflictStatus.NONE
    )
    owners = name_owners.get(_folded_name(ordered[0]), [])
    name_status = SkillConflictStatus.NAME_CONFLICT if len(owners) > 1 else SkillConflictStatus.NONE
    conflict = identity_status if identity_status is not SkillConflictStatus.NONE else name_status
    availability = (
        SkillAvailability.CONFLICTED
        if conflict is not SkillConflictStatus.NONE
        else SkillAvailability.AVAILABLE
    )

    versions = tuple(to_catalog_version(package) for package in ordered)
    newest = max(
        versions,
        key=lambda version: version.created_at or datetime.min.replace(tzinfo=UTC),
    )
    effective = newest.effective_trust if versions else TrustLevel.UNKNOWN
    definition = to_catalog_definition(ordered[0]).model_copy(
        update={
            "availability": availability,
            "conflict_status": conflict,
            "effective_trust": effective,
        }
    )
    return SkillCatalogEntry(
        definition=definition,
        versions=versions,
        newest_available=newest if availability is SkillAvailability.AVAILABLE else None,
        effective_trust=effective,
        requested_trust=ordered[0].manifest.requested_trust,
    )
