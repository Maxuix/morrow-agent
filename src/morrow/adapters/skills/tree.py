"""Canonical package trees: regular files only, one safe read per file.

TOCTOU rule: the digest, size and executable mode come from the same opened
file descriptor the bytes were read from; no stat-then-reread by path.
"""

from __future__ import annotations

import os
import stat
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from morrow.core.domain import canonical_json_bytes, sha256_digest

MAX_PACKAGE_FILES = 256
MAX_PACKAGE_TOTAL_BYTES = 8 * 1024 * 1024
MAX_PACKAGE_FILE_BYTES = 1 * 1024 * 1024
MAX_RELATIVE_PATH_CHARS = 512

TREE_SCHEMA = "skill-tree-v1"


class PackageTreeError(ValueError):
    """A package violates the canonical tree rules."""


@dataclass(frozen=True, slots=True)
class CanonicalFileEntry:
    relative_path: str
    exec_mode: bool
    size: int
    sha256: str

    def canonical(self) -> dict:
        return {
            "path": self.relative_path,
            "kind": "file",
            "exec": self.exec_mode,
            "size": self.size,
            "sha256": self.sha256,
        }


@dataclass(frozen=True, slots=True)
class CanonicalPackageTree:
    entries: tuple[CanonicalFileEntry, ...]
    tree_digest: str
    total_bytes: int

    @property
    def file_count(self) -> int:
        return len(self.entries)

    def file_digest(self, relative_path: str) -> str | None:
        for entry in self.entries:
            if entry.relative_path == relative_path:
                return entry.sha256
        return None


def _normalized_relative_path(root: Path, path: Path) -> str:
    rel = path.relative_to(root).as_posix()
    parts = [part for part in rel.split("/") if part not in ("", ".")]
    if ".." in parts or not parts:
        raise PackageTreeError("package paths must stay inside the package root")
    if parts[0] in ("package", "catalog.json") or "managed-version.json" in parts:
        raise PackageTreeError("package paths must not reuse Morrow reserved names")
    if len(rel) > MAX_RELATIVE_PATH_CHARS:
        raise PackageTreeError("package path exceeds the bounded length")
    normalized = unicodedata.normalize("NFC", "/".join(parts))
    if normalized != rel:
        raise PackageTreeError("package paths must already be NFC-normalized")
    return normalized


def _read_file_bytes(path: Path) -> tuple[bytes, int, bool]:
    """One safe open: fstat and bytes from the same descriptor."""
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise PackageTreeError("package files must be regular files")
        if info.st_size > MAX_PACKAGE_FILE_BYTES:
            raise PackageTreeError("package file exceeds the per-file byte budget")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(fd, 1 << 16)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_PACKAGE_FILE_BYTES:
                raise PackageTreeError("package file exceeds the per-file byte budget")
            chunks.append(chunk)
        raw = b"".join(chunks)
        if total != info.st_size:
            raise PackageTreeError("package file changed while being read")
        try:
            raw.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise PackageTreeError("package files must be valid UTF-8") from exc
        return raw, info.st_size, bool(info.st_mode & 0o111)
    finally:
        os.close(fd)


def _tree_payload(entries: tuple[CanonicalFileEntry, ...]) -> str:
    return (
        TREE_SCHEMA
        + "\n"
        + "\n".join(
            "|".join(
                (
                    entry.relative_path,
                    "file",
                    "1" if entry.exec_mode else "0",
                    str(entry.size),
                    entry.sha256,
                )
            )
            for entry in sorted(entries, key=lambda entry: entry.relative_path)
        )
    )


def _reject_normalization_collisions(paths: list[str]) -> None:
    """Reject case/Unicode-normalization collisions between package paths.

    Kept as a pure function because the host filesystem may be
    case-insensitive (macOS/Windows) and would mask colliding names.
    """
    seen: set[str] = set()
    for relative in paths:
        folded = unicodedata.normalize("NFC", relative).casefold()
        if folded in seen:
            raise PackageTreeError(f"package contains a case/Unicode-colliding path: {relative}")
        seen.add(folded)


def build_canonical_tree(package_root: Path) -> CanonicalPackageTree:
    """Build the canonical tree for one immutable package root."""
    seen: set[str] = set()
    entries: list[CanonicalFileEntry] = []
    total_bytes = 0
    for current, directories, files in os.walk(package_root):
        directories.sort()
        files.sort()
        current_dir = Path(current)
        _reject_normalization_collisions(
            [
                _normalized_relative_path(package_root, current_dir / name)
                for name in (*directories, *files)
            ]
        )
        for name in directories:
            path = current_dir / name
            info = os.lstat(path)
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
                raise PackageTreeError(
                    f"package directory entries must be real directories: {name}"
                )
        for name in files:
            path = current_dir / name
            relative = _normalized_relative_path(package_root, path)
            if relative in seen:
                raise PackageTreeError(f"duplicate package path: {relative}")
            seen.add(relative)
            try:
                info = os.lstat(path)
            except OSError as exc:
                raise PackageTreeError(f"cannot inspect package path {relative}") from exc
            if stat.S_ISLNK(info.st_mode):
                raise PackageTreeError(f"package symlinks are rejected: {relative}")
            if not stat.S_ISREG(info.st_mode):
                raise PackageTreeError(
                    f"package non-regular entries are rejected: {relative} ({stat.S_IFMT(info.st_mode)})"
                )
            raw, size, exec_mode = _read_file_bytes(path)
            total_bytes += size
            if total_bytes > MAX_PACKAGE_TOTAL_BYTES:
                raise PackageTreeError("package exceeds the total byte budget")
            entries.append(
                CanonicalFileEntry(
                    relative_path=relative,
                    exec_mode=exec_mode,
                    size=size,
                    sha256=sha256_digest(raw),
                )
            )
            if len(entries) > MAX_PACKAGE_FILES:
                raise PackageTreeError("package exceeds the file count budget")
    return CanonicalPackageTree(
        entries=tuple(entries),
        tree_digest=sha256_digest(canonical_json_bytes(_tree_payload(tuple(entries)))),
        total_bytes=total_bytes,
    )
