"""Nonblocking OS lease shared by every workspace and CLI for the local user.

The first macOS release conservatively serializes all login desktops of one uid.
The file is never unlinked: replacing it would let two processes lock different
inodes. Native authority and observations are never restored from lock contents.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Protocol

from morrow.core.computer_use import ComputerUseContractError


class DesktopLease(Protocol):
    def acquire(self) -> None: ...

    def release(self) -> None: ...


class FileDesktopLease:
    def __init__(self, *, root: Path | None = None) -> None:
        self._root = root
        self._fd: int | None = None
        self._pid: int | None = None

    @property
    def held(self) -> bool:
        return self._fd is not None and self._pid == os.getpid()

    def __repr__(self) -> str:
        return f"FileDesktopLease(held={self.held})"

    def acquire(self) -> None:
        if self._fd is not None:
            raise ComputerUseContractError("desktop_busy")
        if not hasattr(os, "getuid") or not hasattr(os, "O_NOFOLLOW"):
            raise ComputerUseContractError("unsupported_os")
        import fcntl

        uid = os.getuid()
        root = self._root or Path("/tmp") / f"morrow-desktop-{uid}"
        directory_fd = fd = None
        try:
            try:
                root.mkdir(mode=0o700)
            except FileExistsError:
                pass
            directory_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            directory = os.fstat(directory_fd)
            if directory.st_uid != uid or stat.S_IMODE(directory.st_mode) != 0o700:
                raise ComputerUseContractError("unsafe_desktop_lock")
            fd = os.open(
                "lease", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600, dir_fd=directory_fd
            )
            info = os.fstat(fd)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != uid
                or stat.S_IMODE(info.st_mode) != 0o600
                or info.st_nlink != 1
            ):
                raise ComputerUseContractError("unsafe_desktop_lock")
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ComputerUseContractError("desktop_busy") from None
            self._fd, self._pid = fd, os.getpid()
            fd = None
        except ComputerUseContractError:
            raise
        except OSError:
            raise ComputerUseContractError("unsafe_desktop_lock") from None
        finally:
            if fd is not None:
                os.close(fd)
            if directory_fd is not None:
                os.close(directory_fd)

    def release(self) -> None:
        if self._fd is None:
            return
        fd, pid = self._fd, self._pid
        self._fd = self._pid = None
        # A forked child must not unlock its parent's shared open description.
        try:
            if pid == os.getpid():
                import fcntl

                fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)
