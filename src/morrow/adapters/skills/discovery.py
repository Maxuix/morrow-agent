"""Discovery of managed Skill packages across configured read-only roots.

Layout per source kind:

    <root>/<skill-id>/<skv-id>/package/...
    <root>/<skill-id>/<skv-id>/managed-version.json

Discovery never writes; projection belongs to the application catalog.
"""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from morrow.core.skills.catalog import (
    SkillDefinition,
    SkillVersion,
)
from morrow.core.skills.identity import (
    RESERVED_PATH_NAMES,
    collides,
    validate_skill_id,
    validate_skv_id,
)
from morrow.core.skills.trust import (
    ManifestDocument,
    SourceKind,
    TrustEvidence,
    effective_trust,
)

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


def _inspect_version_dir(
    version_dir: Path,
    *,
    source_kind: SourceKind,
    scope_id: str | None,
    skill_id_hint: str | None,
) -> DiscoveredPackage | PackageLoadError:
    """Load one version directory: envelope, tree, manifest; bounded failures."""
    errors: list[str] = []
    try:
        version_info = os.lstat(version_dir)
        if stat.S_ISLNK(version_info.st_mode) or not stat.S_ISDIR(version_info.st_mode):
            return PackageLoadError(
                version_dir,
                skill_id_hint,
                ("version directory must be a real directory",),
            )
        try:
            validate_skv_id(version_dir.name)
        except ValueError as exc:
            return PackageLoadError(version_dir, skill_id_hint, (f"invalid version_id: {exc}",))
        envelope = read_envelope(version_dir)
    except EnvelopeError as exc:
        return PackageLoadError(version_dir, skill_id_hint, (str(exc),))
    try:
        skill_id = envelope["skill_id"]
        validate_skill_id(skill_id)
        if skill_id_hint is not None:
            validate_skill_id(skill_id_hint)
    except ValueError as exc:
        return PackageLoadError(version_dir, skill_id, (f"invalid skill_id: {exc}",))
    if envelope["version_id"] != version_dir.name:
        return PackageLoadError(
            version_dir,
            skill_id,
            (f"version directory name {version_dir.name!r} does not match envelope version_id",),
        )
    # Older catalog fixtures may omit scope_id; a managed package published by
    # Morrow always carries it. Reject an explicit mismatch without breaking
    # read-only packages whose scope is supplied by the configured root.
    if envelope.get("scope_id") is not None and envelope.get("scope_id") != scope_id:
        return PackageLoadError(
            version_dir,
            skill_id,
            ("managed-version.json scope_id does not match the configured scope",),
        )
    package_root = version_dir / "package"
    try:
        package_info = os.lstat(package_root)
        if stat.S_ISLNK(package_info.st_mode) or not stat.S_ISDIR(package_info.st_mode):
            return PackageLoadError(
                version_dir,
                skill_id,
                ("package directory must be a real directory",),
            )
        manifest_files: dict[str, bytes] = {}
        tree = build_canonical_tree(package_root, content_sink=manifest_files)
        verify_envelope_against_tree(envelope, tree)
    except PackageTreeError as exc:
        return PackageLoadError(version_dir, skill_id, (f"package tree invalid: {exc}",))
    except EnvelopeError as exc:
        return PackageLoadError(version_dir, skill_id, (f"envelope drift: {exc}",))
    try:
        manifest = load_manifest(package_root, file_bytes=manifest_files)
    except ManifestError as exc:
        return PackageLoadError(version_dir, skill_id, (f"manifest invalid: {exc}",))
    if skill_id_hint and envelope["skill_id"] != skill_id_hint:
        errors.append(f"version directory name {skill_id_hint!r} does not match envelope skill_id")
    if envelope["source_kind"] != source_kind.value:
        errors.append("managed-version.json source_kind does not match the configured source root")
    expected_trust = effective_trust(
        TrustEvidence(
            source_kind=source_kind,
            controlled_approval_ref=envelope.get("controlled_approval_ref"),
        )
    )
    if envelope["effective_trust"] != expected_trust.value:
        errors.append("managed-version.json effective_trust does not match local provenance")
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


def inspect_version_dir(
    version_dir: Path,
    *,
    source_kind: SourceKind,
    scope_id: str | None,
    skill_id_hint: str | None,
) -> DiscoveredPackage | PackageLoadError:
    """Load one version and keep every expected package fault bounded."""
    try:
        return _inspect_version_dir(
            version_dir,
            source_kind=source_kind,
            scope_id=scope_id,
            skill_id_hint=skill_id_hint,
        )
    except (KeyError, OSError, OverflowError, TypeError, ValueError) as exc:
        return PackageLoadError(
            version_dir,
            skill_id_hint,
            (f"package validation failed: {type(exc).__name__}",),
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
    try:
        root_info = os.lstat(root)
    except FileNotFoundError:
        return DiscoveryResult((), ())
    except OSError:
        return DiscoveryResult(
            (), (PackageLoadError(root, None, ("source root could not be read",)),)
        )
    if stat.S_ISLNK(root_info.st_mode):
        return DiscoveryResult(
            (), (PackageLoadError(root, None, ("source root symlinks are rejected",)),)
        )
    if not stat.S_ISDIR(root_info.st_mode):
        return DiscoveryResult((), ())
    try:
        skill_dirs = sorted(root.iterdir())
    except OSError:
        return DiscoveryResult(
            (), (PackageLoadError(root, None, ("source root could not be read",)),)
        )
    for skill_dir in skill_dirs:
        try:
            skill_info = os.lstat(skill_dir)
        except OSError:
            failures.append(
                PackageLoadError(skill_dir, None, ("skill directory could not be read",))
            )
            continue
        if stat.S_ISLNK(skill_info.st_mode):
            failures.append(
                PackageLoadError(
                    skill_dir, skill_dir.name, ("skill directory symlinks are rejected",)
                )
            )
            continue
        if not stat.S_ISDIR(skill_info.st_mode) or skill_dir.name in RESERVED_PATH_NAMES:
            continue
        if collides(skill_dir.name, "package") or collides(skill_dir.name, "catalog"):
            continue
        try:
            version_dirs = sorted(skill_dir.iterdir())
        except OSError:
            failures.append(
                PackageLoadError(skill_dir, skill_dir.name, ("skill directory could not be read",))
            )
            continue
        for version_dir in version_dirs:
            try:
                version_info = os.lstat(version_dir)
            except OSError:
                failures.append(
                    PackageLoadError(
                        version_dir, skill_dir.name, ("version directory could not be read",)
                    )
                )
                continue
            if stat.S_ISLNK(version_info.st_mode):
                failures.append(
                    PackageLoadError(
                        version_dir, skill_dir.name, ("version directory symlinks are rejected",)
                    )
                )
                continue
            if not stat.S_ISDIR(version_info.st_mode):
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
    trust = _package_trust(package)
    return SkillVersion(
        version_id=package.version_id,
        skill_id=package.skill_id,
        display_version=package.display_version,
        tree_digest=package.tree.tree_digest,
        file_count=package.tree.file_count,
        total_bytes=package.tree.total_bytes,
        source_kind=package.source_kind,
        scope_id=package.scope_id,
        provenance=f"{package.source_kind.value}:{package.version_dir.parent.name}/{package.version_dir.name}",
        evidence_refs=tuple(package.envelope.get("evidence_refs", ())),
        effective_trust=trust,
        created_at=datetime.fromtimestamp(int(package.envelope["installed_at_unix"]), tz=UTC),
    )


def to_catalog_definition(package: DiscoveredPackage) -> SkillDefinition:
    return SkillDefinition(
        skill_id=package.skill_id,
        name=package.manifest.name or package.version_dir.parent.name,
        description=package.manifest.description,
        source_kind=package.source_kind,
        scope_id=package.scope_id,
        effective_trust=_package_trust(package),
    )


def _package_trust(package: DiscoveredPackage):
    return effective_trust(
        TrustEvidence(
            source_kind=package.source_kind,
            controlled_approval_ref=package.envelope.get("controlled_approval_ref"),
        )
    )
