"""Discovery of managed Skill packages across configured read-only roots.

Layout per source kind:

    <root>/<skill-id>/<skv-id>/package/...
    <root>/<skill-id>/<skv-id>/managed-version.json

Discovery never writes; projection belongs to the application catalog.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from morrow.core.skills.catalog import (
    SkillDefinition,
    SkillVersion,
)
from morrow.core.skills.identity import RESERVED_PATH_NAMES, collides, validate_skill_id
from morrow.core.skills.trust import ManifestDocument, SourceKind, TrustLevel

from .envelope import EnvelopeError, read_envelope, verify_envelope_against_tree
from .manifest_parser import ManifestError, load_manifest
from .tree import CanonicalPackageTree, PackageTreeError, build_canonical_tree


@dataclass(frozen=True, slots=True)
class DiscoveredPackage:
    """One immutable version directory with its verified package."""

    version_dir: Path
    package_root: Path
    skill_id: str
    source_kind: SourceKind
    scope_id: str | None
    envelope: dict
    tree: CanonicalPackageTree
    manifest: ManifestDocument

    @property
    def display_version(self) -> str | None:
        return self.envelope.get("display_version")

    @property
    def version_id(self) -> str:
        return self.envelope["version_id"]


@dataclass(frozen=True, slots=True)
class PackageLoadError:
    """A bounded validation failure for one package; never raises discovery."""

    version_dir: Path
    skill_id: str | None
    errors: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DiscoveryResult:
    packages: tuple[DiscoveredPackage, ...]
    failures: tuple[PackageLoadError, ...] = ()

    def merge(self, other: DiscoveryResult) -> DiscoveryResult:
        return DiscoveryResult(
            packages=self.packages + other.packages,
            failures=self.failures + other.failures,
        )


def _skill_id_candidate(manifest: ManifestDocument) -> str | None:
    from morrow.core.skills.identity import skill_id_from_name

    if manifest.name is None:
        return None
    candidate = skill_id_from_name(manifest.name)
    if candidate is None or candidate in RESERVED_PATH_NAMES:
        return None
    return validate_skill_id(candidate)


def inspect_version_dir(
    version_dir: Path,
    *,
    source_kind: SourceKind,
    scope_id: str | None,
    skill_id_hint: str | None,
) -> DiscoveredPackage | PackageLoadError:
    """Load one version directory: envelope, tree, manifest; bounded failures."""
    errors: list[str] = []
    try:
        envelope = read_envelope(version_dir)
    except EnvelopeError as exc:
        return PackageLoadError(version_dir, skill_id_hint, (str(exc),))
    skill_id = str(envelope.get("skill_id") or skill_id_hint or "")
    try:
        validate_skill_id(skill_id)
    except ValueError as exc:
        return PackageLoadError(version_dir, skill_id, (f"invalid skill_id: {exc}",))
    package_root = version_dir / "package"
    if not package_root.is_dir():
        return PackageLoadError(version_dir, skill_id, ("package directory is missing",))
    try:
        tree = build_canonical_tree(package_root)
        verify_envelope_against_tree(envelope, tree)
    except PackageTreeError as exc:
        return PackageLoadError(version_dir, skill_id, (f"package tree invalid: {exc}",))
    except EnvelopeError as exc:
        return PackageLoadError(version_dir, skill_id, (f"envelope drift: {exc}",))
    try:
        manifest = load_manifest(package_root)
    except ManifestError as exc:
        return PackageLoadError(version_dir, skill_id, (f"manifest invalid: {exc}",))
    envelope_skill_id = str(envelope.get("skill_id") or "")
    if skill_id_hint and envelope_skill_id and envelope_skill_id != skill_id_hint:
        errors.append(f"version directory name {skill_id_hint!r} does not match envelope skill_id")
    if errors:
        return PackageLoadError(version_dir, skill_id, tuple(errors))
    return DiscoveredPackage(
        version_dir=version_dir,
        package_root=package_root,
        skill_id=skill_id,
        source_kind=source_kind,
        scope_id=scope_id,
        envelope=envelope,
        tree=tree,
        manifest=manifest,
    )


def scan_source_root(
    root: Path,
    *,
    source_kind: SourceKind,
    scope_id: str | None = None,
) -> DiscoveryResult:
    """Scan one managed source root without writing anything."""
    packages: list[DiscoveredPackage] = []
    failures: list[PackageLoadError] = []
    if not root.is_dir():
        return DiscoveryResult((), ())
    for skill_dir in sorted(root.iterdir()):
        if not skill_dir.is_dir() or skill_dir.name in RESERVED_PATH_NAMES:
            continue
        if collides(skill_dir.name, "package") or collides(skill_dir.name, "catalog"):
            continue
        for version_dir in sorted(skill_dir.iterdir()):
            if not version_dir.is_dir():
                continue
            loaded = inspect_version_dir(
                version_dir,
                source_kind=source_kind,
                scope_id=scope_id,
                skill_id_hint=skill_dir.name,
            )
            if isinstance(loaded, PackageLoadError):
                failures.append(loaded)
            else:
                packages.append(loaded)
    return DiscoveryResult(tuple(packages), tuple(failures))


def to_catalog_version(package: DiscoveredPackage) -> SkillVersion:
    return SkillVersion(
        version_id=package.version_id,
        skill_id=package.skill_id,
        display_version=package.display_version,
        tree_digest=package.envelope["tree_digest"],
        file_count=package.envelope["file_count"],
        total_bytes=package.envelope["total_bytes"],
        source_kind=package.source_kind,
        scope_id=package.scope_id,
        provenance=f"{package.source_kind.value}:{package.version_dir.parent.name}/{package.version_dir.name}",
        evidence_refs=tuple(package.envelope.get("evidence_refs", ())),
        effective_trust=TrustLevel(package.envelope["effective_trust"]),
        created_at=datetime.fromtimestamp(
            int(package.envelope["installed_at_unix"]), tz=__import__("datetime").timezone.utc
        ),
    )


def to_catalog_definition(package: DiscoveredPackage) -> SkillDefinition:
    return SkillDefinition(
        skill_id=package.skill_id,
        name=package.manifest.name or package.version_dir.parent.name,
        source_kind=package.source_kind,
        scope_id=package.scope_id,
    )
