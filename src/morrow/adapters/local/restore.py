"""No-clobber publication for file restoration; detached originals are retained.

The caller persists the names and intent BEFORE calling this adapter. A retained
capture also preserves writes through descriptors opened by an external editor.
No recovery path here overwrites a directory entry or unlinks captured bytes.
"""

from __future__ import annotations

import hashlib
import os
import stat
from collections.abc import Callable
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

    @staticmethod
    def _checked_path(root: Path, path: str) -> Path:
        if not path or any(part in {"", ".", ".."} for part in path.split("/")):
            raise FileSystemMutationError("source_conflict", "恢复路径无效")
        return root / path

    def _open_bound_parent(self, root, target, root_identity, parent_identity):
        if identity(root) != root_identity:
            raise FileSystemMutationError("source_conflict", "工作区身份已变化")
        parent = self._open_directory_chain(root, target.parent)
        try:
            opened = os.fstat(parent)
            if (opened.st_dev, opened.st_ino) != parent_identity or identity(root) != root_identity:
                raise FileSystemMutationError("source_conflict", "工作区或父目录身份已变化")
            return parent
        except (OSError, FileSystemMutationError):
            os.close(parent)
            raise

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
        destination_path: str | None = None,
        destination_parent_identity: tuple[int, int] | None = None,
        record_staged: Callable[[tuple[int, int]], None] | None = None,
    ) -> None:
        self._require_confined_mutation_support()
        self._rename_primitive()
        for name in (capture_name, temporary_name):
            if not name.startswith(".morrow-restore-") or "/" in name or "\\" in name:
                raise FileSystemMutationError("source_conflict", "保全路径无效")
        source = self._checked_path(root, path)
        target = source
        if destination_path is not None:
            target = self._checked_path(root, destination_path)
            if source == target or expected is None or desired is None:
                raise FileSystemMutationError("source_conflict", "双路径恢复意图无效")
        else:
            destination_parent_identity = parent_identity

        def verify_parents():
            os.close(self._open_bound_parent(root, source, root_identity, parent_identity))
            if source != target:
                os.close(
                    self._open_bound_parent(
                        root, target, root_identity, destination_parent_identity
                    )
                )

        parent = self._open_bound_parent(root, source, root_identity, parent_identity)
        destination_parent = parent
        source_fd = None
        effect = False
        try:
            if source != target:
                destination_parent = self._open_bound_parent(
                    root, target, root_identity, destination_parent_identity
                )
                self._ensure_capture_absent(destination_parent, target.name)
            self._ensure_capture_absent(parent, capture_name)
            self._ensure_capture_absent(destination_parent, temporary_name)
            if expected is None:
                self._ensure_capture_absent(parent, source.name)
            else:
                source_fd, metadata, raw = self._open_regular_at(
                    parent, source.name, max_bytes=8 * 1024 * 1024
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
                self._assert_entry_identity(parent, source.name, metadata)
                self._capture_and_verify(
                    parent,
                    source_name=source.name,
                    capture_name=capture_name,
                    held_fd=source_fd,
                    expected_sha256=hashlib.sha256(expected).hexdigest(),
                    max_bytes=8 * 1024 * 1024,
                )
                effect = True
                self._fsync_required(parent)
            if desired is not None:
                verify_parents()
                fd = os.open(
                    temporary_name,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                    0o600,
                    dir_fd=destination_parent,
                )
                effect = True  # A retained staging entry is part of the durable intent.
                try:
                    view = memoryview(desired)
                    while view:
                        written = os.write(fd, view)
                        if written <= 0:
                            raise OSError("write made no progress")
                        view = view[written:]
                    os.fchmod(fd, desired_mode if desired_mode is not None else 0o644)
                    os.fsync(fd)
                    staged = os.fstat(fd)
                    if record_staged is not None:
                        self._fsync_required(destination_parent)
                        try:
                            record_staged((staged.st_dev, staged.st_ino))
                        except Exception as exc:
                            raise FileSystemMutationError(
                                "outcome_unknown",
                                "已保全文件，但暂存身份未能持久化",
                                effect_applied=True,
                            ) from exc
                    self._assert_entry_identity(destination_parent, temporary_name, staged)
                    verify_parents()
                    self._rename_no_replace(
                        destination_parent, temporary_name, destination_parent, target.name
                    )
                finally:
                    os.close(fd)
            if source != target or desired is None:
                self._ensure_capture_absent(parent, source.name)
            self._fsync_required(destination_parent)
            verify_parents()
            if expected is not None:
                held, raw = self._read_fd_stably(source_fd, max_bytes=8 * 1024 * 1024)
                if (
                    raw != expected
                    or held.st_nlink != 1
                    or stat.S_IMODE(held.st_mode) != expected_mode
                ):
                    raise FileSystemMutationError("outcome_unknown", "外部写入已保全，需核对")
            if desired is not None:
                actual_fd, actual, raw = self._open_regular_at(
                    destination_parent, target.name, max_bytes=8 * 1024 * 1024
                )
                try:
                    self._assert_entry_identity(destination_parent, target.name, actual)
                    if (
                        (actual.st_dev, actual.st_ino) != (staged.st_dev, staged.st_ino)
                        or actual.st_nlink != 1
                        or raw != desired
                        or stat.S_IMODE(actual.st_mode) != desired_mode
                    ):
                        raise FileSystemMutationError("outcome_unknown", "发布后的文件已变化")
                finally:
                    os.close(actual_fd)
        except (OSError, FileSystemMutationError) as exc:
            effect = effect or getattr(exc, "effect_applied", False)
            raise FileSystemMutationError(
                "outcome_unknown" if effect else "source_conflict",
                "恢复未完成，请核对当前文件及保全位置",
                effect_applied=effect,
                staging_present=self._entry_present(parent, capture_name)
                or self._entry_present(destination_parent, temporary_name),
            ) from exc
        finally:
            if source_fd is not None:
                os.close(source_fd)
            if destination_parent != parent:
                os.close(destination_parent)
            os.close(parent)
