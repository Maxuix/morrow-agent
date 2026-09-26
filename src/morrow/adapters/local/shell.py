"""Shell pinned once for the process lifetime.

Host and sandbox command strings run as ``<path> -c <script>``. ``/bin/bash`` is
preferred when it reports ``BASH_VERSION``; otherwise the first POSIX ``sh`` is
pinned and the tool description must not claim Bash.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

ShellFamily = Literal["bash", "posix_sh"]

_BASH_CANDIDATES = ("/bin/bash", "/usr/bin/bash")
_SH_CANDIDATES = ("/bin/sh", "/usr/bin/sh")
_VERSION_SCRIPT = 'printf %s "${BASH_VERSION-}"'
_PROBE_ENV = {"PATH": "/usr/bin:/bin", "LC_ALL": "C"}


@dataclass(frozen=True)
class PinnedShell:
    """Absolute shell selected at startup, with the version captured then."""

    path: str
    family: ShellFamily
    version: str

    @property
    def available(self) -> bool:
        return bool(self.path)


def shell_invocation(shell: PinnedShell, script: str) -> tuple[str, str, str]:
    """Return ``(path, -c, script)``. Raises LookupError when no shell is pinned."""

    if not shell.path:
        raise LookupError(shell.family)
    return (shell.path, "-c", script)


_lock = threading.Lock()
_cached: PinnedShell | None = None


def pinned_shell() -> PinnedShell:
    """Return the process-wide shell, probing at most once."""

    global _cached
    if _cached is not None:
        return _cached
    with _lock:
        if _cached is None:
            _cached = probe_shell()
        return _cached


def probe_shell(
    *,
    exists: Callable[[str], bool] | None = None,
    which: Callable[[str], str | None] | None = None,
    run: Callable[[str, str], str] | None = None,
) -> PinnedShell:
    """Select ``/bin/bash`` or the first POSIX sh without consulting the command text."""

    executable = _is_executable if exists is None else exists
    lookup = shutil.which if which is None else which
    invoke = _bash_version if run is None else run
    for path in (*_BASH_CANDIDATES, _which_absolute(lookup, "bash")):
        if not path or not executable(path):
            continue
        version = invoke(path, _VERSION_SCRIPT)
        if version:
            return PinnedShell(path=path, family="bash", version=version)
    for path in (*_SH_CANDIDATES, _which_absolute(lookup, "sh")):
        if path and executable(path):
            reported = invoke(path, _VERSION_SCRIPT)
            version = f"posix (bash {reported} compatibility)" if reported else "posix"
            return PinnedShell(path=path, family="posix_sh", version=version)
    return PinnedShell(path="", family="posix_sh", version="")


def pinned_shell_of(adapter: object) -> PinnedShell:
    """Read the shell already pinned on a host adapter or its sandbox inner process."""

    direct = getattr(adapter, "shell", None)
    if isinstance(direct, PinnedShell):
        return direct
    inner = getattr(adapter, "process", None)
    nested = getattr(inner, "shell", None)
    if isinstance(nested, PinnedShell):
        return nested
    return pinned_shell()


def _which_absolute(which: Callable[[str], str | None], name: str) -> str:
    found = which(name)
    if isinstance(found, str) and os.path.isabs(found):
        return found
    return ""


def _is_executable(path: str) -> bool:
    return os.path.isfile(path) and os.access(path, os.X_OK)


def _bash_version(path: str, script: str) -> str:
    try:
        completed = subprocess.run(
            [path, "-c", script],
            check=False,
            capture_output=True,
            text=True,
            timeout=2,
            env=_PROBE_ENV,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if completed.returncode != 0 or not completed.stdout:
        return ""
    return _bounded_version(completed.stdout.splitlines()[0])


def _bounded_version(text: str) -> str:
    cleaned = "".join(ch for ch in text if ch.isprintable() and ch not in "\r\n\t")
    return cleaned[:80]
