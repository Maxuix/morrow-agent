"""Kernel process birth stamps; numeric pids alone are reusable identities."""

from __future__ import annotations

import ctypes
import sys
from dataclasses import dataclass

from morrow.core.computer_use import ComputerUseContractError


@dataclass(frozen=True, slots=True, repr=False)
class ProcessBirth:
    seconds: int
    microseconds: int

    def __post_init__(self) -> None:
        if (
            isinstance(self.seconds, bool)
            or not isinstance(self.seconds, int)
            or self.seconds < 1
            or isinstance(self.microseconds, bool)
            or not isinstance(self.microseconds, int)
            or not 0 <= self.microseconds < 1_000_000
        ):
            raise ComputerUseContractError("target_identity_unavailable")

    def __repr__(self) -> str:
        return "ProcessBirth()"


class _ProcBsdInfo(ctypes.Structure):
    """Darwin proc_info.h PROC_PIDTBSDINFO layout, including kernel start time."""

    _fields_ = [
        (name, ctypes.c_uint32)
        for name in (
            "flags",
            "status",
            "xstatus",
            "pid",
            "ppid",
            "uid",
            "gid",
            "ruid",
            "rgid",
            "svuid",
            "svgid",
            "reserved",
        )
    ] + [
        ("comm", ctypes.c_char * 16),
        ("name", ctypes.c_char * 32),
        ("nfiles", ctypes.c_uint32),
        ("pgid", ctypes.c_uint32),
        ("job_count", ctypes.c_uint32),
        ("tty_device", ctypes.c_uint32),
        ("tty_pgid", ctypes.c_uint32),
        ("nice", ctypes.c_int32),
        ("start_seconds", ctypes.c_uint64),
        ("start_microseconds", ctypes.c_uint64),
    ]


def _query_process(pid: int, info: _ProcBsdInfo) -> int:
    # Resolve the OS library only on explicit desktop admission, never module import.
    library = ctypes.CDLL("/usr/lib/libproc.dylib")
    query = library.proc_pidinfo
    query.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_uint64, ctypes.c_void_p, ctypes.c_int]
    query.restype = ctypes.c_int
    return query(pid, 3, 0, ctypes.byref(info), ctypes.sizeof(info))


def read_process_birth(pid: int) -> ProcessBirth:
    if sys.platform != "darwin":
        raise ComputerUseContractError("unsupported_os")
    if isinstance(pid, bool) or not isinstance(pid, int) or not 0 < pid <= 2**31 - 1:
        raise ComputerUseContractError("target_identity_unavailable")
    info = _ProcBsdInfo()
    try:
        size = _query_process(pid, info)
    except Exception:
        raise ComputerUseContractError("target_identity_unavailable") from None
    if size != ctypes.sizeof(info) or info.pid != pid:
        raise ComputerUseContractError("target_identity_unavailable")
    return ProcessBirth(info.start_seconds, info.start_microseconds)
