"""Bounded capture and verification helpers for managed Skill backup state."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from morrow.adapters.skills.envelope import (
    ENVELOPE_NAME,
    read_envelope,
    verify_envelope_against_tree,
)
from morrow.adapters.skills.tree import PackageTreeError, build_canonical_tree
from morrow.core.backup import BackupV2FileEntry, BackupV2FileKind, BackupV2SkillEntry
from morrow.core.skills.catalog import SkillVersion
from morrow.core.skills.trust import SourceKind


class SkillBackupError(ValueError):
    """A referenced managed Skill cannot be copied or verified safely."""


@dataclass(frozen=True, slots=True)
class SkillBackupCapture:
    files: tuple[BackupV2FileEntry, ...]
    skills: tuple[BackupV2SkillEntry, ...]
    version_ids: frozenset[str]


def referenced_version_ids(executor, *, pinned_version_ids: tuple[str, ...] = ()) -> frozenset[str]:
    """Return only versions named by durable Skill evidence.

    The query covers the current durable Skill evidence tables.
    """

    version_ids = set(pinned_version_ids)
    references = (
        ("agent_run_skill_selections", "version_id"),
        ("agent_run_skill_contexts", "version_id"),
        ("skill_drafts", "accepted_version_id"),
        ("skill_usage", "version_id"),
    )
    for table, column in references:
        if executor.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
        ):
            rows = executor.execute(
                f"SELECT {column} FROM {table} WHERE {column} IS NOT NULL LIMIT 4097"
            )
            version_ids.update(str(row[0]) for row in rows if row[0])
    if executor.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?",
        ("skill_catalog_operations",),
    ):
        rows = executor.execute(
            "SELECT version_id FROM skill_catalog_operations "
            "WHERE version_id IS NOT NULL AND operation IN ('rollback', 'pin', 'remove') "
            "LIMIT 4097"
        )
        version_ids.update(str(row[0]) for row in rows if row[0])
    return frozenset(version_ids)


def capture_referenced_skills(
    data_root: Path,
    versions: tuple[SkillVersion, ...],
    *,
    referenced_ids: frozenset[str],
    bound_skill_keys: frozenset[tuple[str | None, str]],
    target_root: Path,
) -> SkillBackupCapture:
    """Capture referenced imported/generated versions into ``target_root``."""

    by_id = {version.version_id: version for version in versions}
    selected = set(referenced_ids)
    # Managed versions are part of the local recovery authority, even when no
    # current binding points at an older version. Keep rollback and catalog
    # rows recoverable after restore.
    selected.update(
        version.version_id
        for version in versions
        if version.source_kind in {SourceKind.IMPORTED, SourceKind.GENERATED}
    )
    # An enabled unpinned binding references the deterministic newest managed
    # version; retain this explicit selection for catalogs that contain a
    # version row outside the managed roots.
    for scope_id, skill_id in bound_skill_keys:
        candidates = [
            version
            for version in versions
            if version.scope_id == scope_id
            and version.skill_id == skill_id
            and version.source_kind in {SourceKind.IMPORTED, SourceKind.GENERATED}
        ]
        if candidates:
            candidates.sort(
                key=lambda item: (
                    item.created_at.timestamp() if isinstance(item.created_at, datetime) else 0,
                    item.version_id,
                ),
                reverse=True,
            )
            selected.add(candidates[0].version_id)

    captures: list[BackupV2FileEntry] = []
    manifests: list[BackupV2SkillEntry] = []
    captured_ids: set[str] = set()
    for version_id in sorted(selected):
        version = by_id.get(version_id)
        if version is None:
            raise SkillBackupError("referenced Skill version is missing from the catalog")
        if version.source_kind not in {SourceKind.IMPORTED, SourceKind.GENERATED}:
            # Builtin and user-authored versions point at external/read-only
            # roots. Keep their catalog rows in SQLite but exclude their bytes.
            continue
        source = _version_path(data_root, version)
        _assert_safe_chain(data_root, source)
        package = source / "package"
        target = target_root / _relative_version_root(version)
        try:
            tree_contents: dict[str, bytes] = {}
            tree = build_canonical_tree(package, all_content_sink=tree_contents)
            envelope = read_envelope(source)
            verify_envelope_against_tree(envelope, tree)
        except (OSError, ValueError, PackageTreeError) as exc:
            raise SkillBackupError("referenced Skill package is missing or drifted") from exc
        if (
            envelope.get("version_id") != version.version_id
            or envelope.get("skill_id") != version.skill_id
            or envelope.get("source_kind") != version.source_kind.value
            or envelope.get("scope_id") != version.scope_id
            or envelope.get("tree_digest") != version.tree_digest
            or envelope.get("effective_trust") != version.effective_trust.value
        ):
            raise SkillBackupError("Skill catalog and package envelope disagree")
        envelope_bytes = _envelope_bytes(envelope)
        _write_bytes(target / ENVELOPE_NAME, envelope_bytes)
        captures.append(
            _file_entry(
                target_root,
                target / ENVELOPE_NAME,
                BackupV2FileKind.SKILL_PACKAGE,
            )
        )
        file_paths: list[str] = []
        for entry in tree.entries:
            relative = target / "package" / entry.relative_path
            _write_bytes(relative, tree_contents[entry.relative_path])
            captures.append(_file_entry(target_root, relative, BackupV2FileKind.SKILL_PACKAGE))
            file_paths.append(_relative(target_root, relative))
        manifests.append(
            BackupV2SkillEntry(
                skill_id=version.skill_id,
                version_id=version.version_id,
                source_kind=version.source_kind.value,
                effective_trust=version.effective_trust.value,
                scope_id=version.scope_id,
                tree_digest=tree.tree_digest,
                envelope_sha256=hashlib.sha256(envelope_bytes).hexdigest(),
                package_path=_relative(target_root, target / "package"),
                file_paths=tuple(file_paths + [_relative(target_root, target / ENVELOPE_NAME)]),
            )
        )
        captured_ids.add(version_id)
    # The tuple sort makes manifest output stable even if SQLite row order changes.
    return SkillBackupCapture(
        files=tuple(sorted(captures, key=lambda item: item.path)),
        skills=tuple(sorted(manifests, key=lambda item: (item.skill_id, item.version_id))),
        version_ids=frozenset(captured_ids),
    )


def verify_skill_capture(
    root: Path, manifest_items: tuple[BackupV2SkillEntry, ...]
) -> tuple[bool, tuple[str, ...]]:
    issues: list[str] = []
    for item in manifest_items:
        package = root / item.package_path
        version_root = package.parent
        envelope_path = version_root / ENVELOPE_NAME
        if not package.is_dir() or package.is_symlink() or not envelope_path.is_file():
            issues.append("skill_package_missing")
            continue
        try:
            contents: dict[str, bytes] = {}
            tree = build_canonical_tree(package, all_content_sink=contents)
            envelope = read_envelope(version_root)
            verify_envelope_against_tree(envelope, tree)
            digest, _size = _hash_file(envelope_path)
            if (
                digest != item.envelope_sha256
                or tree.tree_digest != item.tree_digest
                or envelope.get("version_id") != item.version_id
                or envelope.get("skill_id") != item.skill_id
                or envelope.get("source_kind") != item.source_kind
                or envelope.get("scope_id") != item.scope_id
                or (
                    item.effective_trust != "unknown"
                    and envelope.get("effective_trust") != item.effective_trust
                )
            ):
                issues.append("skill_package_changed")
            expected = set(item.file_paths)
            actual = {
                _relative(root, path)
                for path in version_root.rglob("*")
                if path.is_file() and not path.is_symlink()
            }
            if actual != expected:
                issues.append("skill_package_file_set")
        except (OSError, ValueError, PackageTreeError):
            issues.append("skill_package_invalid")
    return not issues, tuple(dict.fromkeys(issues))


def _version_path(data_root: Path, version: SkillVersion) -> Path:
    return data_root / _relative_version_root(version)


def _assert_safe_chain(root: Path, path: Path) -> None:
    try:
        relative = path.absolute().relative_to(root.absolute())
    except ValueError as exc:
        raise SkillBackupError("managed Skill path escapes the data root") from exc
    current = root.absolute()
    for part in relative.parts:
        current /= part
        info = os.lstat(current)
        if info.st_mode & 0o170000 != 0o040000:
            raise SkillBackupError("managed Skill path contains an unsafe directory")


def _relative_version_root(version: SkillVersion) -> Path:
    if version.source_kind not in {SourceKind.IMPORTED, SourceKind.GENERATED}:
        raise SkillBackupError("only managed Skill versions can be backed up")
    base = (
        Path("skills")
        if version.scope_id is None
        else Path("workspaces") / version.scope_id / "skills"
    )
    return base / version.source_kind.value / version.skill_id / version.version_id


def _write_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.exists() and path.is_symlink():
        raise SkillBackupError("backup destination contains a symlink")
    path.write_bytes(content)
    os.chmod(path, 0o600)


def _file_entry(root: Path, path: Path, kind: BackupV2FileKind) -> BackupV2FileEntry:
    digest, size = _hash_file(path)
    return BackupV2FileEntry(path=_relative(root, path), kind=kind, sha256=digest, byte_size=size)


def _relative(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def _envelope_bytes(payload: dict) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _hash_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


__all__ = [
    "SkillBackupCapture",
    "SkillBackupError",
    "capture_referenced_skills",
    "referenced_version_ids",
    "verify_skill_capture",
]
