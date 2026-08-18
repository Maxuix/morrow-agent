"""Small stdlib filesystem adapter with no Provider-facing path authority."""

from __future__ import annotations

import os
import stat
import threading
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from morrow.core.local_tools import LocalFileKind


@dataclass(frozen=True)
class DirectoryItem:
    path: Path
    name: str
    kind: LocalFileKind
    size: int


class FileSystemAdapter:
    def __init__(self) -> None:
        self._lock_guard = threading.Lock()
        self._locks: dict[Path, threading.Lock] = {}

    @contextmanager
    def target_lock(self, path: Path):
        key = path.absolute()
        with self._lock_guard:
            lock = self._locks.setdefault(key, threading.Lock())
        lock.acquire()
        try:
            yield
        finally:
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
            fd = os.open(path, flags | no_follow)
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
