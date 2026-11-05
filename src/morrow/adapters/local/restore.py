"""No-clobber publication for file restoration; detached originals are retained.

The caller persists the names and intent BEFORE calling this adapter. A retained
capture also preserves writes through descriptors opened by an external editor.
No recovery path here overwrites a directory entry or unlinks captured bytes.
"""

from __future__ import annotations

import hashlib
import os
import stat
from pathlib import Path

from morrow.adapters.local.filesystem import FileSystemAdapter, FileSystemMutationError


def identity(path: Path) -> tuple[int, int]:
    value = path.stat(follow_symlinks=False)
    if not stat.S_ISDIR(value.st_mode):
        raise FileSystemMutationError("source_conflict", "目录身份已变化")
    return value.st_dev, value.st_ino


class RestorePublisher(FileSystemAdapter):
    @classmethod
    def supported(cls) -> bool:
        try:
            cls._require_confined_mutation_support()
            cls._rename_primitive()
        except FileSystemMutationError:
            return False
        return True

    def publish(
        self,
        root: Path,
        path: str,
        *,
        expected: bytes | None,
        expected_mode: int | None,
        desired: bytes | None,
        desired_mode: int | None,
        capture_name: str,
        temporary_name: str,
        root_identity: tuple[int, int],
        parent_identity: tuple[int, int],
        expected_identity: tuple[int, int] | None = None,
    ) -> None:
        self._require_confined_mutation_support()
        self._rename_primitive()  # Reject unsupported platforms before any effect.
        for name in (capture_name, temporary_name):
            if not name.startswith(".morrow-restore-") or "/" in name or "\\" in name:
                raise FileSystemMutationError("source_conflict", "保全路径无效")
        if (
            not path
            or path.startswith("/")
            or any(part in {"", ".", ".."} for part in path.split("/"))
        ):
            raise FileSystemMutationError("source_conflict", "恢复路径无效")
        target = root / path
        if identity(root) != root_identity or identity(target.parent) != parent_identity:
            raise FileSystemMutationError("source_conflict", "工作区或父目录身份已变化")
        parent = self._open_directory_chain(root, target.parent)
        source_fd = None
        effect = False
        try:
            opened_parent = os.fstat(parent)
            if (opened_parent.st_dev, opened_parent.st_ino) != parent_identity:
                raise FileSystemMutationError("source_conflict", "父目录身份已变化")
            self._ensure_capture_absent(parent, capture_name)
            self._ensure_capture_absent(parent, temporary_name)
            if expected is None:
                self._ensure_capture_absent(parent, target.name)
            else:
                source_fd, metadata, raw = self._open_regular_at(
                    parent, target.name, max_bytes=8 * 1024 * 1024
                )
                if (
                    expected_identity is not None
                    and (metadata.st_dev, metadata.st_ino) != expected_identity
                ):
                    raise FileSystemMutationError("source_conflict", "文件身份在预览后已变化")
                if (
                    metadata.st_nlink != 1
                    or raw != expected
                    or stat.S_IMODE(metadata.st_mode) != expected_mode
                ):
                    raise FileSystemMutationError(
                        "source_conflict", "当前文件有后续修改或不支持的链接"
                    )
                self._assert_entry_identity(parent, target.name, metadata)
                self._capture_and_verify(
                    parent,
                    source_name=target.name,
                    capture_name=capture_name,
                    held_fd=source_fd,
                    expected_sha256=hashlib.sha256(expected).hexdigest(),
                    max_bytes=8 * 1024 * 1024,
                )
                effect = True
                self._fsync_required(parent)
            # Reopen the visible chain; a renamed parent must never redirect publication.
            if identity(root) != root_identity or identity(target.parent) != parent_identity:
                raise FileSystemMutationError("source_conflict", "发布前目录身份已变化")
            check = self._open_directory_chain(root, target.parent)
            try:
                if (os.fstat(check).st_dev, os.fstat(check).st_ino) != parent_identity:
                    raise FileSystemMutationError("source_conflict", "发布前父目录身份已变化")
            finally:
                os.close(check)
            if desired is not None:
                fd = os.open(
                    temporary_name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=parent
                )
                try:
                    os.fchmod(fd, desired_mode if desired_mode is not None else 0o644)
                    view = memoryview(desired)
                    while view:
                        written = os.write(fd, view)
                        if written <= 0:
                            raise OSError("write made no progress")
                        view = view[written:]
                    os.fsync(fd)
                finally:
                    os.close(fd)
                self._rename_no_replace(parent, temporary_name, parent, target.name)
                effect = True
            else:
                self._ensure_capture_absent(parent, target.name)
            self._fsync_required(parent)
            if expected is not None:
                held, raw = self._read_fd_stably(source_fd, max_bytes=8 * 1024 * 1024)
                if raw != expected or stat.S_IMODE(held.st_mode) != expected_mode:
                    raise FileSystemMutationError("outcome_unknown", "外部写入已保全，需核对")
            if desired is not None:
                actual = self._read_file_at(parent, target.name, max_bytes=8 * 1024 * 1024)
                if actual.raw != desired or actual.mode != desired_mode:
                    raise FileSystemMutationError("outcome_unknown", "发布后的文件已变化")
        except (OSError, FileSystemMutationError) as exc:
            effect = effect or getattr(exc, "effect_applied", False)
            raise FileSystemMutationError(
                "outcome_unknown" if effect else "source_conflict",
                "恢复未完成，请核对当前文件及保全位置",
                effect_applied=effect,
                staging_present=self._entry_present(parent, capture_name),
            ) from exc
        finally:
            if source_fd is not None:
                os.close(source_fd)
            os.close(parent)
