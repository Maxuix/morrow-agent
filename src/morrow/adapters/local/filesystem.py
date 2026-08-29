"""Small stdlib filesystem adapter with no Provider-facing path authority."""

from __future__ import annotations

import ctypes
import errno
import hashlib
import os
import stat
import sys
import threading
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from morrow.core.local_tools import LocalFileKind

CAPTURE_NAME_PREFIX = ".morrow-capture-"


@dataclass(frozen=True)
class DirectoryItem:
    path: Path
    name: str
    kind: LocalFileKind
    size: int


class FileSystemMutationError(RuntimeError):
    """Bounded adapter error for a confined destructive filesystem operation."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        effect_applied: bool = False,
        staging_present: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.effect_applied = effect_applied
        self.staging_present = staging_present


class FileSystemCapabilityError(FileSystemMutationError):
    """The platform cannot prove the required no-clobber primitive."""


@dataclass(frozen=True)
class ConfinedFileState:
    """Bounded state read from a no-follow file descriptor."""

    raw: bytes
    mode: int
    size: int
    mtime_ns: int


class FileSystemAdapter:
    def __init__(self) -> None:
        self._lock_guard = threading.Lock()
        self._locks: dict[Path, threading.Lock] = {}

    @contextmanager
    def target_lock(self, path: Path):
        with self.paths_lock((path,)):
            yield

    @contextmanager
    def paths_lock(self, paths: tuple[Path, ...]):
        """Acquire every affected path in one deterministic lexical order."""

        keys = tuple(
            sorted(
                {path.absolute() for path in paths},
                key=lambda value: (value.as_posix().casefold(), value.as_posix()),
            )
        )
        with self._lock_guard:
            locks = tuple(self._locks.setdefault(key, threading.Lock()) for key in keys)
        acquired: list[threading.Lock] = []
        try:
            for lock in locks:
                lock.acquire()
                acquired.append(lock)
            yield
        finally:
            for lock in reversed(acquired):
                lock.release()

    def read_bytes(self, path: Path, *, max_bytes: int) -> bytes:
        try:
            metadata = os.stat(path, follow_symlinks=False)
            if not stat.S_ISREG(metadata.st_mode):
                raise ValueError("file is not an admitted regular file")
            if metadata.st_size > max_bytes:
                raise ValueError("file is too large")
            flags = os.O_RDONLY
            no_follow = getattr(os, "O_NOFOLLOW", 0)
            non_blocking = getattr(os, "O_NONBLOCK", 0)
            fd = os.open(path, flags | no_follow | non_blocking)
            try:
                chunks: list[bytes] = []
                total = 0
                while True:
                    chunk = os.read(fd, min(128 * 1024, max_bytes - total + 1))
                    if not chunk:
                        break
                    chunks.append(chunk)
                    total += len(chunk)
                    if total > max_bytes:
                        raise ValueError("file is too large")
                return b"".join(chunks)
            finally:
                os.close(fd)
        except (OSError, ValueError) as exc:
            from morrow.services.files import LocalFileError

            if isinstance(exc, ValueError) and "large" in str(exc):
                raise LocalFileError("file_too_large", "文件超过读取上限") from exc
            raise LocalFileError("read_failed", "文件读取失败") from exc

    def iter_directory(self, path: Path) -> tuple[DirectoryItem, ...]:
        items: list[DirectoryItem] = []
        try:
            with os.scandir(path) as entries:
                for entry in entries:
                    try:
                        metadata = entry.stat(follow_symlinks=False)
                    except OSError:
                        continue
                    mode = metadata.st_mode
                    if stat.S_ISDIR(mode):
                        kind = LocalFileKind.DIRECTORY
                    elif stat.S_ISREG(mode):
                        kind = LocalFileKind.FILE
                    elif stat.S_ISLNK(mode):
                        kind = LocalFileKind.SYMLINK
                    else:
                        kind = LocalFileKind.SPECIAL
                    items.append(
                        DirectoryItem(
                            path=Path(entry.path),
                            name=entry.name,
                            kind=kind,
                            size=metadata.st_size if kind is LocalFileKind.FILE else 0,
                        )
                    )
        except OSError as exc:
            from morrow.services.files import LocalFileError

            raise LocalFileError("list_failed", "目录读取失败") from exc
        return tuple(sorted(items, key=lambda item: (item.name.casefold(), item.name)))

    def atomic_write(
        self,
        path: Path,
        data: bytes,
        *,
        mode: int | None = None,
        workspace_root: Path | None = None,
    ) -> None:
        temporary_name = f".morrow-tmp-{uuid.uuid4().hex}"
        temporary = path.parent / temporary_name
        fd: int | None = None
        parent_fd: int | None = None
        try:
            if workspace_root is None:
                fd = os.open(
                    temporary,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                    0o600,
                )
            else:
                parent_fd = self._open_directory_chain(workspace_root, path.parent)
                fd = os.open(
                    temporary_name,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                    0o600,
                    dir_fd=parent_fd,
                )
            if mode is not None:
                os.fchmod(fd, mode)
            view = memoryview(data)
            while view:
                written = os.write(fd, view)
                if written <= 0:
                    raise OSError("temporary file write made no progress")
                view = view[written:]
            os.fsync(fd)
            os.close(fd)
            fd = None
            if parent_fd is None:
                os.replace(temporary, path)
                self._fsync_directory(path.parent)
            else:
                os.replace(temporary_name, path.name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
                self._fsync_fd(parent_fd)
        except OSError as exc:
            raise RuntimeError("atomic publication failed") from exc
        finally:
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass
            if parent_fd is None:
                try:
                    temporary.unlink()
                except FileNotFoundError:
                    pass
                except OSError:
                    pass
            else:
                try:
                    os.unlink(temporary_name, dir_fd=parent_fd)
                except FileNotFoundError:
                    pass
                except OSError:
                    pass
                try:
                    os.close(parent_fd)
                except OSError:
                    pass

    def unlink_confined(
        self,
        path: Path,
        *,
        workspace_root: Path,
        expected_sha256: str | None = None,
        max_bytes: int = 8 * 1024 * 1024,
        staging_name: str | None = None,
    ) -> None:
        """Delete one regular file after atomically capturing its directory entry.

        A held fd alone does not bind ``unlinkat`` to the fd's inode: the name can
        be replaced after the last name check.  Capture therefore moves the name
        to a private, unpredictable sibling through the same no-replace primitive;
        every destructive effect below addresses only that captured name.
        """

        parent_fd: int | None = None
        source_fd: int | None = None
        try:
            self._require_confined_mutation_support()
            if expected_sha256 is None:
                raise FileSystemMutationError("source_conflict", "源文件版本证据缺失")
            capture_name = self._capture_name(staging_name)
            parent_fd = self._open_directory_chain(workspace_root, path.parent)
            self._ensure_capture_absent(parent_fd, capture_name)
            try:
                metadata = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
            except FileNotFoundError as exc:
                raise FileSystemMutationError("source_conflict", "源文件不存在") from exc
            except OSError as exc:
                raise FileSystemMutationError("publication_failed", "源文件无法检查") from exc
            if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
                raise FileSystemMutationError("not_regular", "源文件不是普通文件")
            source_fd, opened, raw = self._open_regular_at(
                parent_fd, path.name, max_bytes=max_bytes
            )
            if not self._same_file_identity(metadata, opened):
                raise FileSystemMutationError("source_conflict", "源文件目录项已发生变化")
            if hashlib.sha256(raw).hexdigest() != expected_sha256:
                raise FileSystemMutationError("source_conflict", "源文件版本已变化")
            self._assert_entry_identity(parent_fd, path.name, opened)
            captured = self._capture_and_verify(
                parent_fd,
                source_name=path.name,
                capture_name=capture_name,
                held_fd=source_fd,
                expected_sha256=expected_sha256,
                max_bytes=max_bytes,
            )
            try:
                self._fsync_required(parent_fd)
            except (FileSystemMutationError, OSError) as exc:
                raise FileSystemMutationError(
                    "outcome_unknown",
                    "源文件已捕获但捕获结果无法持久化",
                    effect_applied=True,
                    staging_present=True,
                ) from exc
            try:
                self._assert_entry_identity(parent_fd, capture_name, captured)
            except FileSystemMutationError as exc:
                raise FileSystemMutationError(
                    "outcome_unknown",
                    "捕获条目在删除前已发生变化",
                    effect_applied=True,
                    staging_present=self._entry_present(parent_fd, capture_name),
                ) from exc
            try:
                os.unlink(capture_name, dir_fd=parent_fd)
            except FileNotFoundError as exc:
                raise FileSystemMutationError(
                    "outcome_unknown",
                    "捕获文件已消失，删除结果无法确认",
                    effect_applied=True,
                    staging_present=False,
                ) from exc
            except OSError as exc:
                raise FileSystemMutationError(
                    "outcome_unknown",
                    "捕获文件删除结果无法确认",
                    effect_applied=True,
                    staging_present=True,
                ) from exc
            try:
                self._fsync_required(parent_fd)
            except (FileSystemMutationError, OSError) as exc:
                raise FileSystemMutationError(
                    "outcome_unknown",
                    "删除已执行但持久化结果无法确认",
                    effect_applied=True,
                    staging_present=self._entry_present(parent_fd, capture_name),
                ) from exc
            try:
                os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
            except FileNotFoundError:
                pass
            except OSError as exc:
                raise FileSystemMutationError(
                    "outcome_unknown",
                    "删除已执行但源结果无法确认",
                    effect_applied=True,
                    staging_present=self._entry_present(parent_fd, capture_name),
                ) from exc
            else:
                raise FileSystemMutationError(
                    "outcome_unknown",
                    "删除已执行但源路径已有新文件",
                    effect_applied=True,
                    staging_present=self._entry_present(parent_fd, capture_name),
                )
            try:
                os.stat(capture_name, dir_fd=parent_fd, follow_symlinks=False)
            except FileNotFoundError:
                return
            except OSError as exc:
                raise FileSystemMutationError(
                    "outcome_unknown",
                    "删除已执行但结果无法确认",
                    effect_applied=True,
                    staging_present=True,
                ) from exc
            raise FileSystemMutationError(
                "outcome_unknown",
                "删除已执行但结果无法确认",
                effect_applied=True,
                staging_present=True,
            )
        except FileSystemMutationError:
            raise
        except OSError as exc:
            raise FileSystemMutationError("publication_failed", "文件删除路径无法打开") from exc
        finally:
            if source_fd is not None:
                try:
                    os.close(source_fd)
                except OSError:
                    pass
            if parent_fd is not None:
                try:
                    os.close(parent_fd)
                except OSError:
                    pass

    def read_confined_file(
        self,
        path: Path,
        *,
        workspace_root: Path,
        max_bytes: int = 8 * 1024 * 1024,
    ) -> ConfinedFileState:
        """Read one regular file through the same no-follow directory-fd boundary."""

        parent_fd: int | None = None
        try:
            self._require_confined_mutation_support()
            parent_fd = self._open_directory_chain(workspace_root, path.parent)
            try:
                return self._read_file_at(parent_fd, path.name, max_bytes=max_bytes)
            except FileNotFoundError as exc:
                raise FileSystemMutationError("source_conflict", "文件不存在") from exc
        except FileSystemMutationError:
            raise
        except OSError as exc:
            raise FileSystemMutationError("source_conflict", "文件路径无法打开") from exc
        finally:
            if parent_fd is not None:
                try:
                    os.close(parent_fd)
                except OSError:
                    pass

    def move_no_replace(
        self,
        source: Path,
        destination: Path,
        *,
        workspace_root: Path,
        expected_sha256: str | None = None,
        max_bytes: int = 8 * 1024 * 1024,
        staging_name: str | None = None,
    ) -> ConfinedFileState:
        """Move one regular file through an atomic captured-entry handoff.

        The source name is never used for the publishing effect.  It is first
        captured under a no-replace rename, verified against the held fd, and then
        the captured name is published with the same no-replace primitive.
        """

        source_parent_fd: int | None = None
        destination_parent_fd: int | None = None
        source_fd: int | None = None
        try:
            self._require_confined_mutation_support()
            if expected_sha256 is None:
                raise FileSystemMutationError("source_conflict", "源文件版本证据缺失")
            capture_name = self._capture_name(staging_name)
            source_parent_fd = self._open_directory_chain(workspace_root, source.parent)
            if source.parent.absolute() == destination.parent.absolute():
                destination_parent_fd = source_parent_fd
            else:
                destination_parent_fd = self._open_directory_chain(
                    workspace_root, destination.parent
                )
            self._ensure_capture_absent(source_parent_fd, capture_name)
            try:
                metadata = os.stat(source.name, dir_fd=source_parent_fd, follow_symlinks=False)
            except FileNotFoundError as exc:
                raise FileSystemMutationError("source_conflict", "源文件不存在") from exc
            except OSError as exc:
                raise FileSystemMutationError("publication_failed", "源文件无法检查") from exc
            if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
                raise FileSystemMutationError("not_regular", "源文件不是普通文件")
            source_fd, opened, raw = self._open_regular_at(
                source_parent_fd, source.name, max_bytes=max_bytes
            )
            if not self._same_file_identity(metadata, opened):
                raise FileSystemMutationError("source_conflict", "源文件目录项已发生变化")
            if hashlib.sha256(raw).hexdigest() != expected_sha256:
                raise FileSystemMutationError("source_conflict", "源文件版本已变化")
            self._assert_entry_identity(source_parent_fd, source.name, opened)
            captured = self._capture_and_verify(
                source_parent_fd,
                source_name=source.name,
                capture_name=capture_name,
                held_fd=source_fd,
                expected_sha256=expected_sha256,
                max_bytes=max_bytes,
            )
            try:
                self._fsync_required(source_parent_fd)
            except (FileSystemMutationError, OSError) as exc:
                raise FileSystemMutationError(
                    "outcome_unknown",
                    "源文件已捕获但捕获结果无法持久化",
                    effect_applied=True,
                    staging_present=True,
                ) from exc
            try:
                self._assert_entry_identity(source_parent_fd, capture_name, captured)
            except FileSystemMutationError as exc:
                raise FileSystemMutationError(
                    "outcome_unknown",
                    "捕获条目在移动前已发生变化",
                    effect_applied=True,
                    staging_present=self._entry_present(source_parent_fd, capture_name),
                ) from exc
            try:
                self._rename_no_replace(
                    source_parent_fd,
                    capture_name,
                    destination_parent_fd,
                    destination.name,
                )
            except FileSystemMutationError as exc:
                if exc.code in {
                    "destination_exists",
                    "source_conflict",
                    "unsupported_capability",
                    "cross_device",
                }:
                    self._restore_captured_entry(
                        source_parent_fd,
                        source_name=source.name,
                        capture_name=capture_name,
                        expected=captured,
                    )
                    raise exc
                if self._entry_present(source_parent_fd, capture_name):
                    self._restore_captured_entry(
                        source_parent_fd,
                        source_name=source.name,
                        capture_name=capture_name,
                        expected=captured,
                    )
                raise FileSystemMutationError(
                    "outcome_unknown",
                    "原子移动失败且结果无法确认",
                    effect_applied=True,
                    staging_present=self._entry_present(source_parent_fd, capture_name),
                ) from exc
            except OSError as exc:
                if exc.errno in {errno.EEXIST, errno.ENOTEMPTY}:
                    self._restore_captured_entry(
                        source_parent_fd,
                        source_name=source.name,
                        capture_name=capture_name,
                        expected=captured,
                    )
                    raise FileSystemMutationError("destination_exists", "目标路径已经存在") from exc
                if exc.errno in {
                    errno.EINVAL,
                    errno.ENOSYS,
                    getattr(errno, "ENOTSUP", errno.EOPNOTSUPP),
                    errno.EOPNOTSUPP,
                }:
                    self._restore_captured_entry(
                        source_parent_fd,
                        source_name=source.name,
                        capture_name=capture_name,
                        expected=captured,
                    )
                    raise FileSystemCapabilityError(
                        "unsupported_capability", "平台不支持可证明的原子无覆盖移动"
                    ) from exc
                if exc.errno == errno.EXDEV:
                    self._restore_captured_entry(
                        source_parent_fd,
                        source_name=source.name,
                        capture_name=capture_name,
                        expected=captured,
                    )
                    raise FileSystemMutationError("cross_device", "跨设备移动不受支持") from exc
                if exc.errno in {errno.ENOENT, errno.ELOOP, errno.ENOTDIR}:
                    if self._entry_present(source_parent_fd, capture_name):
                        self._restore_captured_entry(
                            source_parent_fd,
                            source_name=source.name,
                            capture_name=capture_name,
                            expected=captured,
                        )
                        raise FileSystemMutationError(
                            "source_conflict", "移动路径已发生变化"
                        ) from exc
                    raise FileSystemMutationError(
                        "outcome_unknown",
                        "移动路径已变化且结果无法确认",
                        effect_applied=True,
                        staging_present=False,
                    ) from exc
                if self._entry_present(source_parent_fd, capture_name):
                    self._restore_captured_entry(
                        source_parent_fd,
                        source_name=source.name,
                        capture_name=capture_name,
                        expected=captured,
                    )
                raise FileSystemMutationError(
                    "outcome_unknown",
                    "原子移动失败且结果无法确认",
                    effect_applied=True,
                    staging_present=self._entry_present(source_parent_fd, capture_name),
                ) from exc
            try:
                self._fsync_required(source_parent_fd)
                if destination_parent_fd != source_parent_fd:
                    self._fsync_required(destination_parent_fd)
            except (FileSystemMutationError, OSError) as exc:
                raise FileSystemMutationError(
                    "outcome_unknown",
                    "移动已执行但持久化结果无法确认",
                    effect_applied=True,
                    staging_present=self._entry_present(source_parent_fd, capture_name),
                ) from exc
            try:
                os.stat(source.name, dir_fd=source_parent_fd, follow_symlinks=False)
            except FileNotFoundError:
                pass
            except OSError as exc:
                raise FileSystemMutationError(
                    "outcome_unknown",
                    "移动已执行但源结果无法确认",
                    effect_applied=True,
                    staging_present=self._entry_present(source_parent_fd, capture_name),
                ) from exc
            else:
                raise FileSystemMutationError(
                    "outcome_unknown",
                    "移动已执行但源结果无法确认",
                    effect_applied=True,
                    staging_present=self._entry_present(source_parent_fd, capture_name),
                )
            if self._entry_present(source_parent_fd, capture_name):
                raise FileSystemMutationError(
                    "outcome_unknown",
                    "移动已执行但捕获路径仍存在",
                    effect_applied=True,
                    staging_present=True,
                )
            destination_fd: int | None = None
            try:
                destination_fd, published_metadata, published_raw = self._open_regular_at(
                    destination_parent_fd, destination.name, max_bytes=max_bytes
                )
            except FileSystemMutationError as exc:
                raise FileSystemMutationError(
                    "outcome_unknown",
                    "移动已执行但目标结果无法确认",
                    effect_applied=True,
                    staging_present=False,
                ) from exc
            try:
                try:
                    self._assert_entry_identity(
                        destination_parent_fd, destination.name, published_metadata
                    )
                except FileSystemMutationError as exc:
                    raise FileSystemMutationError(
                        "outcome_unknown",
                        "移动已执行但目标目录项无法确认",
                        effect_applied=True,
                        staging_present=False,
                    ) from exc
                if (
                    not self._same_captured_identity(captured, published_metadata)
                    or hashlib.sha256(published_raw).hexdigest() != expected_sha256
                    or published_metadata.st_size != captured.st_size
                    or stat.S_IMODE(published_metadata.st_mode) != stat.S_IMODE(captured.st_mode)
                ):
                    raise FileSystemMutationError(
                        "outcome_unknown",
                        "移动已执行但目标结果无法确认",
                        effect_applied=True,
                        staging_present=False,
                    )
                return ConfinedFileState(
                    raw=published_raw,
                    mode=stat.S_IMODE(published_metadata.st_mode),
                    size=published_metadata.st_size,
                    mtime_ns=published_metadata.st_mtime_ns,
                )
            finally:
                if destination_fd is not None:
                    try:
                        os.close(destination_fd)
                    except OSError:
                        pass
        except FileSystemMutationError:
            raise
        except OSError as exc:
            raise FileSystemMutationError("publication_failed", "移动路径无法打开") from exc
        finally:
            if source_fd is not None:
                try:
                    os.close(source_fd)
                except OSError:
                    pass
            if destination_parent_fd is not None and destination_parent_fd != source_parent_fd:
                try:
                    os.close(destination_parent_fd)
                except OSError:
                    pass
            if source_parent_fd is not None:
                try:
                    os.close(source_parent_fd)
                except OSError:
                    pass

    @staticmethod
    def atomic_no_replace_supported() -> bool:
        try:
            FileSystemAdapter._require_confined_mutation_support()
            FileSystemAdapter._rename_primitive()
        except FileSystemMutationError:
            return False
        return True

    @staticmethod
    def _require_confined_mutation_support() -> None:
        if (
            not getattr(os, "O_NOFOLLOW", 0)
            or not getattr(os, "O_DIRECTORY", 0)
            or not getattr(os, "O_NONBLOCK", 0)
        ):
            raise FileSystemCapabilityError(
                "unsupported_capability", "平台不支持受限目录 fd 文件操作"
            )

    @staticmethod
    def _capture_name(value: str | None) -> str:
        if value is not None and not isinstance(value, str):
            raise FileSystemMutationError("source_conflict", "内部捕获路径无效")
        name = value if value is not None else f"{CAPTURE_NAME_PREFIX}{uuid.uuid4().hex}"
        suffix = name[len(CAPTURE_NAME_PREFIX) :]
        if (
            not name.startswith(CAPTURE_NAME_PREFIX)
            or len(suffix) != 32
            or any(character not in "0123456789abcdef" for character in suffix)
            or "/" in name
            or "\\" in name
            or "\x00" in name
        ):
            raise FileSystemMutationError("source_conflict", "内部捕获路径无效")
        return name

    @staticmethod
    def _ensure_capture_absent(parent_fd: int, capture_name: str) -> None:
        try:
            os.stat(capture_name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            return
        except OSError as exc:
            raise FileSystemMutationError("source_conflict", "内部捕获路径无法确认") from exc
        raise FileSystemMutationError("source_conflict", "内部捕获路径已经存在")

    @staticmethod
    def _entry_present(parent_fd: int, name: str) -> bool:
        try:
            os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            return False
        except OSError:
            # An unobservable entry is conservatively treated as present so a
            # recovery report cannot omit a possible captured user file.
            return True
        return True

    def _capture_and_verify(
        self,
        parent_fd: int,
        *,
        source_name: str,
        capture_name: str,
        held_fd: int,
        expected_sha256: str,
        max_bytes: int,
    ) -> os.stat_result:
        """Atomically detach the source name, then bind the captured entry to its fd."""

        try:
            self._rename_no_replace(parent_fd, source_name, parent_fd, capture_name)
        except FileSystemMutationError:
            raise
        except OSError as exc:
            if exc.errno in {errno.EEXIST, errno.ENOTEMPTY}:
                raise FileSystemMutationError("source_conflict", "内部捕获路径已经存在") from exc
            if exc.errno in {
                errno.EINVAL,
                errno.ENOSYS,
                getattr(errno, "ENOTSUP", errno.EOPNOTSUPP),
                errno.EOPNOTSUPP,
            }:
                raise FileSystemCapabilityError(
                    "unsupported_capability", "平台不支持可证明的原子无覆盖捕获"
                ) from exc
            if exc.errno in {errno.ENOENT, errno.ELOOP, errno.ENOTDIR}:
                raise FileSystemMutationError("source_conflict", "源文件目录项已发生变化") from exc
            raise FileSystemMutationError("publication_failed", "原子捕获失败") from exc

        try:
            captured = os.stat(capture_name, dir_fd=parent_fd, follow_symlinks=False)
        except OSError as exc:
            raise FileSystemMutationError(
                "outcome_unknown",
                "源文件已捕获但捕获条目无法验证",
                effect_applied=True,
                staging_present=self._entry_present(parent_fd, capture_name),
            ) from exc
        try:
            held_after, held_raw = self._read_fd_stably(held_fd, max_bytes=max_bytes)
        except FileSystemMutationError as exc:
            self._restore_captured_entry(
                parent_fd,
                source_name=source_name,
                capture_name=capture_name,
                expected=captured,
            )
            raise FileSystemMutationError("source_conflict", "源文件在捕获期间已发生变化") from exc
        if (
            not self._same_file_identity(held_after, captured)
            or hashlib.sha256(held_raw).hexdigest() != expected_sha256
        ):
            self._restore_captured_entry(
                parent_fd,
                source_name=source_name,
                capture_name=capture_name,
                expected=captured,
            )
            raise FileSystemMutationError("source_conflict", "源文件目录项已发生变化")
        try:
            self._assert_entry_identity(parent_fd, capture_name, captured)
        except FileSystemMutationError as exc:
            raise FileSystemMutationError(
                "outcome_unknown",
                "捕获条目在验证期间已发生变化",
                effect_applied=True,
                staging_present=self._entry_present(parent_fd, capture_name),
            ) from exc
        return captured

    def _restore_captured_entry(
        self,
        parent_fd: int,
        *,
        source_name: str,
        capture_name: str,
        expected: os.stat_result,
    ) -> None:
        """Restore a captured entry without overwriting a third-party source."""

        try:
            self._assert_entry_identity(parent_fd, capture_name, expected)
            self._rename_no_replace(parent_fd, capture_name, parent_fd, source_name)
        except FileSystemMutationError as exc:
            raise FileSystemMutationError(
                "outcome_unknown",
                "捕获文件无法安全恢复",
                effect_applied=True,
                staging_present=self._entry_present(parent_fd, capture_name),
            ) from exc
        except OSError as exc:
            raise FileSystemMutationError(
                "outcome_unknown",
                "捕获文件无法安全恢复",
                effect_applied=True,
                staging_present=self._entry_present(parent_fd, capture_name),
            ) from exc
        try:
            self._fsync_required(parent_fd)
            restored = os.stat(source_name, dir_fd=parent_fd, follow_symlinks=False)
        except (FileSystemMutationError, OSError) as exc:
            raise FileSystemMutationError(
                "outcome_unknown",
                "捕获文件恢复结果无法确认",
                effect_applied=True,
                staging_present=self._entry_present(parent_fd, capture_name),
            ) from exc
        if not self._same_captured_identity(expected, restored):
            raise FileSystemMutationError(
                "outcome_unknown",
                "恢复后的源文件无法验证",
                effect_applied=True,
                staging_present=self._entry_present(parent_fd, capture_name),
            )
        try:
            os.stat(capture_name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            return
        except OSError as exc:
            raise FileSystemMutationError(
                "outcome_unknown",
                "捕获文件恢复结果无法确认",
                effect_applied=True,
                staging_present=True,
            ) from exc
        raise FileSystemMutationError(
            "outcome_unknown",
            "捕获文件恢复后仍存在内部路径",
            effect_applied=True,
            staging_present=True,
        )

    @staticmethod
    def _read_fd_stably(fd: int, *, max_bytes: int) -> tuple[os.stat_result, bytes]:
        """Read a held fd twice without changing ownership or following a path."""

        try:
            before = os.fstat(fd)
            if not stat.S_ISREG(before.st_mode):
                raise FileSystemMutationError("not_regular", "源文件不是普通文件")
            os.lseek(fd, 0, os.SEEK_SET)
            raw = FileSystemAdapter._read_fd(fd, max_bytes=max_bytes)
            after = os.fstat(fd)
            if (
                not FileSystemAdapter._same_file_identity(before, after)
                or len(raw) != after.st_size
            ):
                raise FileSystemMutationError("source_conflict", "源文件在读取期间已变化")
            os.lseek(fd, 0, os.SEEK_SET)
            second = FileSystemAdapter._read_fd(fd, max_bytes=max_bytes)
            verified = os.fstat(fd)
            if (
                raw != second
                or not FileSystemAdapter._same_file_identity(after, verified)
                or len(second) != verified.st_size
            ):
                raise FileSystemMutationError("source_conflict", "源文件版本无法稳定读取")
            return verified, second
        except FileSystemMutationError:
            raise
        except OSError as exc:
            raise FileSystemMutationError("source_conflict", "源文件无法读取") from exc

    @staticmethod
    def _open_regular_at(
        parent_fd: int, name: str, *, max_bytes: int
    ) -> tuple[int, os.stat_result, bytes]:
        no_follow = getattr(os, "O_NOFOLLOW", 0)
        non_blocking = getattr(os, "O_NONBLOCK", 0)
        fd = os.open(name, os.O_RDONLY | no_follow | non_blocking, dir_fd=parent_fd)
        try:
            before = os.fstat(fd)
            if not stat.S_ISREG(before.st_mode):
                raise FileSystemMutationError("not_regular", "源文件不是普通文件")
            raw = FileSystemAdapter._read_fd(fd, max_bytes=max_bytes)
            after = os.fstat(fd)
            if (
                not FileSystemAdapter._same_file_identity(before, after)
                or len(raw) != after.st_size
            ):
                raise FileSystemMutationError("source_conflict", "源文件在读取期间已变化")
            os.lseek(fd, 0, os.SEEK_SET)
            second = FileSystemAdapter._read_fd(fd, max_bytes=max_bytes)
            verified = os.fstat(fd)
            if (
                raw != second
                or not FileSystemAdapter._same_file_identity(after, verified)
                or len(second) != verified.st_size
            ):
                raise FileSystemMutationError("source_conflict", "源文件版本无法稳定读取")
            return fd, verified, second
        except FileSystemMutationError:
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass
            raise
        except OSError as exc:
            try:
                os.close(fd)
            except OSError:
                pass
            raise FileSystemMutationError("source_conflict", "源文件无法读取") from exc

    @staticmethod
    def _same_file_identity(first: os.stat_result, second: os.stat_result) -> bool:
        return (
            first.st_dev,
            first.st_ino,
            stat.S_IFMT(first.st_mode),
            stat.S_IMODE(first.st_mode),
            first.st_size,
            first.st_mtime_ns,
            first.st_ctime_ns,
        ) == (
            second.st_dev,
            second.st_ino,
            stat.S_IFMT(second.st_mode),
            stat.S_IMODE(second.st_mode),
            second.st_size,
            second.st_mtime_ns,
            second.st_ctime_ns,
        )

    @staticmethod
    def _same_captured_identity(first: os.stat_result, second: os.stat_result) -> bool:
        """Compare an inode across rename operations without treating rename ctime as drift."""

        return (
            first.st_dev,
            first.st_ino,
            stat.S_IFMT(first.st_mode),
            stat.S_IMODE(first.st_mode),
            first.st_size,
            first.st_mtime_ns,
        ) == (
            second.st_dev,
            second.st_ino,
            stat.S_IFMT(second.st_mode),
            stat.S_IMODE(second.st_mode),
            second.st_size,
            second.st_mtime_ns,
        )

    @staticmethod
    def _assert_entry_identity(parent_fd: int, name: str, expected: os.stat_result) -> None:
        try:
            current = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError as exc:
            raise FileSystemMutationError("source_conflict", "源文件目录项已消失") from exc
        except OSError as exc:
            raise FileSystemMutationError("source_conflict", "源文件目录项无法检查") from exc
        if not FileSystemAdapter._same_file_identity(expected, current):
            raise FileSystemMutationError("source_conflict", "源文件目录项已发生变化")

    @staticmethod
    def _read_bytes_at(parent_fd: int, name: str, *, max_bytes: int) -> bytes:
        fd, _metadata, raw = FileSystemAdapter._open_regular_at(
            parent_fd, name, max_bytes=max_bytes
        )
        try:
            return raw
        finally:
            try:
                os.close(fd)
            except OSError:
                pass

    @staticmethod
    def _read_file_at(parent_fd: int, name: str, *, max_bytes: int) -> ConfinedFileState:
        fd, metadata, raw = FileSystemAdapter._open_regular_at(parent_fd, name, max_bytes=max_bytes)
        try:
            return ConfinedFileState(
                raw=raw,
                mode=stat.S_IMODE(metadata.st_mode),
                size=metadata.st_size,
                mtime_ns=metadata.st_mtime_ns,
            )
        finally:
            try:
                os.close(fd)
            except OSError:
                pass

    @staticmethod
    def _read_fd(fd: int, *, max_bytes: int) -> bytes:
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(fd, min(128 * 1024, max_bytes - total + 1))
            if not chunk:
                return b"".join(chunks)
            chunks.append(chunk)
            total += len(chunk)
            if total > max_bytes:
                raise FileSystemMutationError("file_too_large", "文件超过读取上限")

    @staticmethod
    def _rename_no_replace(
        source_parent_fd: int,
        source_name: str,
        destination_parent_fd: int,
        destination_name: str,
    ) -> None:
        primitive = FileSystemAdapter._rename_primitive()
        primitive(
            source_parent_fd,
            os.fsencode(source_name),
            destination_parent_fd,
            os.fsencode(destination_name),
        )

    @staticmethod
    def _rename_primitive():
        try:
            libc = ctypes.CDLL(None, use_errno=True)
        except OSError as exc:
            raise FileSystemCapabilityError(
                "unsupported_capability", "平台不支持可证明的原子无覆盖移动"
            ) from exc
        if sys.platform == "darwin":
            function = getattr(libc, "renameatx_np", None)
            if function is None:
                raise FileSystemCapabilityError(
                    "unsupported_capability", "平台不支持可证明的原子无覆盖移动"
                )
            function.argtypes = [
                ctypes.c_int,
                ctypes.c_char_p,
                ctypes.c_int,
                ctypes.c_char_p,
                ctypes.c_uint,
            ]
            function.restype = ctypes.c_int

            def renameatx_np(source_fd, source_name, destination_fd, destination_name):
                result = function(
                    source_fd,
                    source_name,
                    destination_fd,
                    destination_name,
                    0x00000004,
                )
                if result != 0:
                    error_number = ctypes.get_errno()
                    raise OSError(error_number, os.strerror(error_number))

            return renameatx_np
        if sys.platform.startswith("linux"):
            function = getattr(libc, "renameat2", None)
            if function is None:
                raise FileSystemCapabilityError(
                    "unsupported_capability", "平台不支持可证明的原子无覆盖移动"
                )
            function.argtypes = [
                ctypes.c_int,
                ctypes.c_char_p,
                ctypes.c_int,
                ctypes.c_char_p,
                ctypes.c_uint,
            ]
            function.restype = ctypes.c_int

            def renameat2(source_fd, source_name, destination_fd, destination_name):
                result = function(
                    source_fd,
                    source_name,
                    destination_fd,
                    destination_name,
                    0x00000001,
                )
                if result != 0:
                    error_number = ctypes.get_errno()
                    raise OSError(error_number, os.strerror(error_number))

            return renameat2
        raise FileSystemCapabilityError(
            "unsupported_capability", "平台不支持可证明的原子无覆盖移动"
        )

    @staticmethod
    def _open_directory_chain(root: Path, target_parent: Path) -> int:
        try:
            relative = target_parent.relative_to(root)
        except ValueError as exc:
            raise OSError("target parent is outside workspace root") from exc
        directory_flag = getattr(os, "O_DIRECTORY", 0)
        no_follow = getattr(os, "O_NOFOLLOW", 0)
        current_fd = os.open(root, os.O_RDONLY | directory_flag | no_follow)
        try:
            for part in relative.parts:
                next_fd = os.open(
                    part,
                    os.O_RDONLY | directory_flag | no_follow,
                    dir_fd=current_fd,
                )
                os.close(current_fd)
                current_fd = next_fd
            return current_fd
        except OSError:
            os.close(current_fd)
            raise

    @staticmethod
    def _fsync_directory(path: Path) -> None:
        try:
            fd = os.open(path, os.O_RDONLY)
        except OSError:
            return
        try:
            try:
                os.fsync(fd)
            except OSError:
                pass
        finally:
            os.close(fd)

    @staticmethod
    def _fsync_fd(fd: int) -> None:
        try:
            os.fsync(fd)
        except OSError:
            pass

    @staticmethod
    def _fsync_required(fd: int) -> None:
        try:
            os.fsync(fd)
        except OSError as exc:
            raise FileSystemMutationError("publication_failed", "文件系统同步失败") from exc
