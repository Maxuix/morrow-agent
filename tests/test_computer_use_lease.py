"""Real OS lock semantics using temporary files, with no desktop or network."""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

from morrow.adapters.computer_use.lease import FileDesktopLease
from morrow.core.computer_use import ComputerUseContractError


@pytest.mark.skipif(sys.platform == "win32", reason="first release uses POSIX file locks")
def test_independent_owners_and_processes_share_one_lease(tmp_path):
    root = tmp_path / "desktop"
    first, second = FileDesktopLease(root=root), FileDesktopLease(root=root)
    first.acquire()
    with pytest.raises(ComputerUseContractError, match="desktop_busy"):
        second.acquire()
    code = """
import sys
from pathlib import Path
from morrow.adapters.computer_use.lease import FileDesktopLease
from morrow.core.computer_use import ComputerUseContractError
lease = FileDesktopLease(root=Path(sys.argv[1]))
try:
    lease.acquire()
except ComputerUseContractError as exc:
    print(str(exc))
else:
    print('acquired')
    lease.release()
"""
    child = subprocess.run(
        [sys.executable, "-c", code, str(root)], capture_output=True, text=True, timeout=10
    )
    assert child.returncode == 0
    assert child.stdout.strip() == "desktop_busy"
    inode = (root / "lease").stat().st_ino
    first.release()
    second.acquire()
    assert (root / "lease").stat().st_ino == inode
    second.release()
    second.release()
    child = subprocess.run(
        [sys.executable, "-c", code, str(root)], capture_output=True, text=True, timeout=10
    )
    assert child.returncode == 0
    assert child.stdout.strip() == "acquired"


@pytest.mark.parametrize("unsafe", ["directory_mode", "directory_link", "file_link", "hardlink"])
def test_unsafe_lock_paths_are_refused(tmp_path, unsafe):
    root = tmp_path / "desktop"
    other = tmp_path / "other"
    other.mkdir(mode=0o700)
    if unsafe == "directory_link":
        root.symlink_to(other, target_is_directory=True)
    else:
        root.mkdir(mode=0o700)
        if unsafe == "directory_mode":
            root.chmod(0o755)
        elif unsafe == "file_link":
            (root / "lease").symlink_to(other / "lease")
        else:
            target = other / "lease"
            target.touch(mode=0o600)
            os.link(target, root / "lease")
    with pytest.raises(ComputerUseContractError, match="unsafe_desktop_lock"):
        FileDesktopLease(root=root).acquire()


def test_process_death_releases_lock_without_persisting_authority(tmp_path):
    root = tmp_path / "desktop"
    code = """
import os, sys
from pathlib import Path
from morrow.adapters.computer_use.lease import FileDesktopLease
lease = FileDesktopLease(root=Path(sys.argv[1]))
lease.acquire()
os._exit(0)
"""
    result = subprocess.run([sys.executable, "-c", code, str(root)], timeout=10)
    assert result.returncode == 0
    lease = FileDesktopLease(root=root)
    lease.acquire()
    assert (root / "lease").read_bytes() == b""
    lease.release()
