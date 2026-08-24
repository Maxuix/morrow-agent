"""Safe local Skill validation and immutable managed-version publication."""

from __future__ import annotations

import os
import posixpath
import shutil
import stat
import tempfile
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from filelock import FileLock

from morrow.core.domain import validate_prefixed_id
from morrow.core.skills.bindings import SkillValidationReport
from morrow.core.skills.identity import (
    SkillIdentityError,
    skill_id_from_name,
    validate_skill_id,
    validate_skv_id,
)
from morrow.core.skills.trust import ManifestDocument, SourceKind

from .envelope import (
    build_envelope_payload,
    read_envelope,
    verify_envelope_against_tree,
    write_envelope,
)
from .manifest_parser import ManifestError, load_manifest
from .tree import CanonicalPackageTree, PackageTreeError, build_canonical_tree


@dataclass(frozen=True, slots=True)
class PreparedLocalSkill:
    source_path: Path
    skill_id: str
    name: str
    display_version: str | None
    source_kind: SourceKind
    scope_id: str | None
    manifest: ManifestDocument
    tree: CanonicalPackageTree
    contents: dict[str, bytes]
    report: SkillValidationReport


@dataclass(frozen=True, slots=True)
class PublishedSkill:
    version_dir: Path
    package_root: Path
    version_id: str
    skill_id: str
    source_kind: SourceKind
    scope_id: str | None
    envelope: dict


@dataclass(frozen=True, slots=True)
class FrozenSkillFile:
    """Bytes and verification evidence captured by one safe package scan."""

    relative_path: str
    content: bytes
    tree: CanonicalPackageTree
    manifest: ManifestDocument
    envelope: dict


@dataclass(frozen=True, slots=True)
class FrozenSkillPackage:
    """All bytes captured by one verified immutable-version scan."""

    skill_id: str
    version_id: str
    source_kind: SourceKind
    scope_id: str | None
    tree: CanonicalPackageTree
    manifest: ManifestDocument
    envelope: Mapping[str, object]
    contents: Mapping[str, bytes]


class SkillPackageError(ValueError):
    """A local Skill package cannot be validated or published safely."""


def prepare_local_skill(
    source_path: Path,
    *,
    source_kind: SourceKind = SourceKind.IMPORTED,
    scope_id: str | None = None,
) -> PreparedLocalSkill:
    """Capture and validate a local package once, including all file bytes."""

    source_path = source_path.expanduser().absolute()
    try:
        info = os.lstat(source_path)
    except OSError as exc:
        raise SkillPackageError("Skill source could not be inspected") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise SkillPackageError("Skill source must be a real directory")
    if source_kind not in {SourceKind.IMPORTED, SourceKind.GENERATED}:
        raise SkillPackageError("local installation only supports imported or generated Skills")
    if scope_id is not None:
        try:
            scope_id = validate_prefixed_id(scope_id, "ws")
        except ValueError as exc:
            raise SkillPackageError("Skill workspace scope is invalid") from exc

    contents: dict[str, bytes] = {}
    try:
        tree = build_canonical_tree(source_path, all_content_sink=contents)
        manifest = load_manifest(source_path, file_bytes=contents)
    except (ManifestError, PackageTreeError) as exc:
        raise SkillPackageError(str(exc)) from exc
    name = manifest.name or source_path.name
    try:
        skill_id = validate_skill_id(skill_id_from_name(name) or "")
    except SkillIdentityError as exc:
        raise SkillPackageError("Skill name cannot produce a safe skill_id") from exc

    scripts = tuple(
        entry.relative_path
        for entry in tree.entries
        if entry.relative_path == "scripts" or entry.relative_path.startswith("scripts/")
    )
    dependencies = tuple(
        [f"tool:{item}" for item in manifest.required_tools]
        + [f"mcp:{item}" for item in manifest.required_mcp_servers]
    )
    report = SkillValidationReport(
        valid=True,
        skill_id=skill_id,
        name=name,
        display_version=manifest.display_version,
        source_kind=source_kind,
        tree_digest=tree.tree_digest,
        file_count=tree.file_count,
        total_bytes=tree.total_bytes,
        requested_permissions=manifest.requested_permissions,
        scripts=scripts[:64],
        dependencies=dependencies[:32],
    )
    return PreparedLocalSkill(
        source_path=source_path,
        skill_id=skill_id,
        name=name,
        display_version=manifest.display_version,
        source_kind=source_kind,
        scope_id=scope_id,
        manifest=manifest,
        tree=tree,
        contents=contents,
        report=report,
    )


class ManagedSkillPackageStore:
    """Filesystem authority for immutable imported/generated Skill versions."""

    def __init__(self, data_root: Path, *, failure_injector=None) -> None:
        self.data_root = data_root.expanduser()
        self.failure_injector = failure_injector
        self.data_root.mkdir(parents=True, exist_ok=True)
        (self.data_root / "locks").mkdir(parents=True, exist_ok=True)
        self.lock = FileLock(str(self.data_root / "locks" / "skills.lock"), timeout=5)

    def source_root(self, source_kind: SourceKind, scope_id: str | None) -> Path:
        if source_kind not in {SourceKind.IMPORTED, SourceKind.GENERATED}:
            raise SkillPackageError("only imported/generated roots are managed by installation")
        if scope_id is None:
            return self.data_root / "skills" / source_kind.value
        try:
            scope_id = validate_prefixed_id(scope_id, "ws")
        except ValueError as exc:
            raise SkillPackageError("Skill workspace scope is invalid") from exc
        return self.data_root / "workspaces" / scope_id / "skills" / source_kind.value

    def version_path(
        self,
        *,
        skill_id: str,
        version_id: str,
        source_kind: SourceKind,
        scope_id: str | None,
    ) -> Path:
        validate_skill_id(skill_id)
        validate_skv_id(version_id)
        return self.source_root(source_kind, scope_id) / skill_id / version_id

    def publish(
        self,
        prepared: PreparedLocalSkill,
        *,
        version_id: str,
        evidence_refs: tuple[str, ...] = (),
        controlled_approval_ref: str | None = None,
    ) -> PublishedSkill:
        validate_skv_id(version_id)
        target = self.version_path(
            skill_id=prepared.skill_id,
            version_id=version_id,
            source_kind=prepared.source_kind,
            scope_id=prepared.scope_id,
        )
        with self.lock:
            if os.path.lexists(target):
                info = os.lstat(target)
                if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
                    raise SkillPackageError("managed Skill version is not a real directory")
                return self._existing(prepared, target, version_id)
            parent = target.parent
            _ensure_directory(parent)
            temporary = Path(tempfile.mkdtemp(prefix=f".{version_id}.", dir=parent))
            try:
                package_root = temporary / "package"
                package_root.mkdir(mode=0o700)
                for entry in prepared.tree.entries:
                    raw = prepared.contents.get(entry.relative_path)
                    if raw is None:
                        raise SkillPackageError("validated package bytes are incomplete")
                    destination = package_root / entry.relative_path
                    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                    mode = 0o700 if entry.exec_mode else 0o600
                    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
                    fd = os.open(destination, flags, mode)
                    try:
                        written = 0
                        while written < len(raw):
                            written += os.write(fd, raw[written:])
                        os.fsync(fd)
                    finally:
                        os.close(fd)
                    os.chmod(destination, mode)
                payload = build_envelope_payload(
                    version_id=version_id,
                    display_version=prepared.display_version,
                    skill_id=prepared.skill_id,
                    source_kind=prepared.source_kind,
                    scope_id=prepared.scope_id,
                    tree=prepared.tree,
                    evidence_refs=evidence_refs,
                    local_review_ok=True,
                    controlled_approval_ref=controlled_approval_ref,
                    installed_at=_now(),
                )
                write_envelope(temporary, payload)
                verify_envelope_against_tree(payload, build_canonical_tree(package_root))
                if self.failure_injector:
                    self.failure_injector("package_publish")
                os.replace(temporary, target)
                temporary = Path()
                return PublishedSkill(
                    target,
                    target / "package",
                    version_id,
                    prepared.skill_id,
                    prepared.source_kind,
                    prepared.scope_id,
                    payload,
                )
            finally:
                if temporary != Path() and temporary.exists():
                    _remove_owned_tree(temporary)

    def _existing(
        self, prepared: PreparedLocalSkill, target: Path, version_id: str
    ) -> PublishedSkill:
        try:
            envelope = read_envelope(target)
            tree = build_canonical_tree(target / "package")
            verify_envelope_against_tree(envelope, tree)
        except (OSError, ValueError, PackageTreeError) as exc:
            raise SkillPackageError(
                "managed Skill version already exists with different content"
            ) from exc
        if (
            envelope.get("version_id") != version_id
            or envelope.get("skill_id") != prepared.skill_id
            or envelope.get("source_kind") != prepared.source_kind.value
            or envelope.get("scope_id") != prepared.scope_id
            or envelope.get("tree_digest") != prepared.tree.tree_digest
        ):
            raise SkillPackageError("managed Skill version already exists with different content")
        return PublishedSkill(
            target,
            target / "package",
            version_id,
            prepared.skill_id,
            prepared.source_kind,
            prepared.scope_id,
            envelope,
        )

    def remove_version(
        self,
        *,
        skill_id: str,
        version_id: str,
        source_kind: SourceKind,
        scope_id: str | None,
    ) -> None:
        target = self.version_path(
            skill_id=skill_id,
            version_id=version_id,
            source_kind=source_kind,
            scope_id=scope_id,
        )
        with self.lock:
            if not os.path.lexists(target):
                return
            info = os.lstat(target)
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
                raise SkillPackageError("managed Skill version is not a real directory")
            _remove_owned_tree(target)
            skill_parent = target.parent
            try:
                skill_parent.rmdir()
            except OSError:
                pass

    def manifest_for_version(
        self,
        *,
        skill_id: str,
        version_id: str,
        source_kind: SourceKind,
        scope_id: str | None,
    ) -> ManifestDocument:
        target = self.version_path(
            skill_id=skill_id,
            version_id=version_id,
            source_kind=source_kind,
            scope_id=scope_id,
        )
        contents: dict[str, bytes] = {}
        try:
            tree = build_canonical_tree(target / "package", all_content_sink=contents)
            envelope = read_envelope(target)
            verify_envelope_against_tree(envelope, tree)
            if (
                envelope.get("version_id") != version_id
                or envelope.get("skill_id") != skill_id
                or envelope.get("source_kind") != source_kind.value
                or envelope.get("scope_id") != scope_id
            ):
                raise SkillPackageError("managed Skill envelope identity does not match its path")
            return load_manifest(target / "package", file_bytes=contents)
        except (OSError, ValueError, ManifestError, PackageTreeError) as exc:
            raise SkillPackageError("managed Skill manifest is unavailable") from exc

    def read_frozen_file(
        self,
        *,
        skill_id: str,
        version_id: str,
        source_kind: SourceKind,
        scope_id: str | None,
        relative_path: str,
        expected_tree_digest: str | None = None,
    ) -> FrozenSkillFile:
        """Read one file from a verified immutable version in a single scan.

        The canonical tree scan opens each file once and retains the bytes in
        memory.  The requested bytes are returned from that capture, never via
        a later path-based read.  ``expected_tree_digest`` is the AgentRun's
        frozen identity and a mismatch is an unavailable resource, not a
        reason to pick another version.
        """

        normalized = _safe_package_relative_path(relative_path)
        package = self.read_frozen_package(
            skill_id=skill_id,
            version_id=version_id,
            source_kind=source_kind,
            scope_id=scope_id,
            expected_tree_digest=expected_tree_digest,
        )
        raw = package.contents.get(normalized)
        if raw is None or package.tree.file_digest(normalized) is None:
            raise SkillPackageError("requested Skill resource is unavailable")
        return FrozenSkillFile(
            normalized,
            raw,
            package.tree,
            package.manifest,
            dict(package.envelope),
        )

    def read_frozen_package(
        self,
        *,
        skill_id: str,
        version_id: str,
        source_kind: SourceKind,
        scope_id: str | None,
        expected_tree_digest: str | None = None,
    ) -> FrozenSkillPackage:
        """Capture every managed package byte after verifying its envelope and tree."""

        target = self.version_path(
            skill_id=skill_id,
            version_id=version_id,
            source_kind=source_kind,
            scope_id=scope_id,
        )
        contents: dict[str, bytes] = {}
        try:
            tree = build_canonical_tree(target / "package", all_content_sink=contents)
            envelope = read_envelope(target)
            verify_envelope_against_tree(envelope, tree)
            if (
                envelope.get("version_id") != version_id
                or envelope.get("skill_id") != skill_id
                or envelope.get("source_kind") != source_kind.value
                or envelope.get("scope_id") != scope_id
            ):
                raise SkillPackageError("managed Skill envelope identity does not match its path")
            if expected_tree_digest is not None and tree.tree_digest != expected_tree_digest:
                raise SkillPackageError("managed Skill tree drifted from frozen evidence")
            manifest = load_manifest(target / "package", file_bytes=contents)
            return FrozenSkillPackage(
                skill_id=skill_id,
                version_id=version_id,
                source_kind=source_kind,
                scope_id=scope_id,
                tree=tree,
                manifest=manifest,
                envelope=MappingProxyType(dict(envelope)),
                contents=MappingProxyType(dict(contents)),
            )
        except (OSError, ValueError, ManifestError, PackageTreeError) as exc:
            if isinstance(exc, SkillPackageError):
                raise
            raise SkillPackageError("managed Skill resource is unavailable") from exc


def _ensure_directory(path: Path) -> None:
    if path.exists():
        info = os.lstat(path)
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise SkillPackageError("managed Skill root is not a real directory")
        return
    path.mkdir(mode=0o700, parents=True, exist_ok=True)


def _remove_owned_tree(path: Path) -> None:
    resolved = path.absolute()
    if resolved.name == "" or resolved == resolved.parent:
        raise SkillPackageError("refusing to remove an unsafe managed path")
    shutil.rmtree(resolved)


def _now():
    from datetime import UTC, datetime

    return datetime.now(UTC)


__all__ = [
    "FrozenSkillFile",
    "FrozenSkillPackage",
    "ManagedSkillPackageStore",
    "PreparedLocalSkill",
    "PublishedSkill",
    "SkillPackageError",
    "prepare_local_skill",
]


def _safe_package_relative_path(value: str) -> str:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise SkillPackageError("Skill resource path is invalid")
    if "\\" in value or value.startswith("/") or posixpath.isabs(value):
        raise SkillPackageError("Skill resource path must be relative")
    if value != unicodedata.normalize("NFC", value):
        raise SkillPackageError("Skill resource path must be NFC-normalized")
    parts = value.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise SkillPackageError("Skill resource path contains an unsafe segment")
    if len(value) > 512:
        raise SkillPackageError("Skill resource path is too long")
    return value
