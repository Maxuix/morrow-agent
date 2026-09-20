"""Human directory selection, separate from model file/tool authorization."""

from __future__ import annotations

import os
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path

from morrow.services.workspace import WorkspaceError, WorkspaceService


class DirectoryService:
    """Bounded browsing inside explicitly configured server selection roots.

    Resolve aliases first, then open every canonical component without following
    links. A replacement symlink between validation and use fails closed.
    """

    def __init__(self, roots: tuple[Path, ...]):
        self.roots = tuple(dict.fromkeys(WorkspaceService.normalize_path(p) for p in roots))

    def validate(self, path: str | Path) -> Path:
        try:
            canonical = WorkspaceService.normalize_path(Path(path))
        except (OSError, ValueError, RuntimeError):
            raise WorkspaceError("Directory is unavailable") from None
        if not any(canonical.is_relative_to(root) for root in self.roots):
            raise WorkspaceError("Directory is outside the server selection roots")
        return canonical

    @contextmanager
    def opened(self, path: str | Path):
        canonical = self.validate(path)
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        fd = os.open(canonical.anchor, flags)
        try:
            for part in canonical.parts[1:]:
                child = os.open(part, flags, dir_fd=fd)
                os.close(fd)
                fd = child
            yield canonical, fd
        except OSError:
            raise WorkspaceError("Directory changed or is inaccessible; select it again") from None
        finally:
            os.close(fd)

    def browse(self, path: str | None = None, *, limit: int = 200) -> dict:
        if not 1 <= limit <= 200:
            raise WorkspaceError("Directory limit must be between 1 and 200")
        if path is None:
            return {"roots": [str(root) for root in self.roots]}
        with self.opened(path) as (canonical, fd):
            children = []
            truncated = False
            with os.scandir(fd) as entries:
                for entry in entries:
                    # Aliases may be entered explicitly and validated, but the
                    # picker never follows an untrusted child link while listing.
                    if not entry.is_dir(follow_symlinks=False):
                        continue
                    if len(children) == limit:
                        truncated = True
                        break
                    children.append({"name": entry.name, "path": str(canonical / entry.name)})
            parent = canonical.parent
            return {
                "path": str(canonical),
                "parent": str(parent)
                if any(parent.is_relative_to(root) for root in self.roots)
                else None,
                "items": sorted(children, key=lambda item: item["name"].casefold()),
                "truncated": truncated,
            }

    def create(self, parent: str, name: str) -> Path:
        if not name or name in {".", ".."} or any(c in name for c in "/\\\x00"):
            raise WorkspaceError("A single folder name is required")
        if len(name.encode("utf-8")) > 255:
            raise WorkspaceError("Folder name is too long")
        with self.opened(parent) as (canonical, fd):
            try:
                os.mkdir(name, mode=0o755, dir_fd=fd)
            except FileExistsError:
                raise WorkspaceError("Folder already exists; use Open existing folder") from None
            return canonical / name

    def resolve_workspace(
        self, service: WorkspaceService, path: str, *, include_removed: bool = False
    ):
        with self.opened(path) as (canonical, _):
            resolution = service.resolve(canonical, include_removed=include_removed)
            target = resolution.identity or resolution.candidate
            self.validate(target.path)
            return resolution

    def initialize_git(self, path: str) -> None:
        """Only a separate human management action may initialize a repository."""
        with self.opened(path) as (_, fd):
            if ".git" in os.listdir(fd):
                raise WorkspaceError("Git metadata already exists")
            try:
                result = subprocess.run(
                    [
                        sys.executable,
                        "-c",
                        "import os,sys; os.fchdir(int(sys.argv[1])); "
                        "os.execve('/usr/bin/git', ['git','init','--quiet'], os.environ)",
                        str(fd),
                    ],
                    pass_fds=(fd,),
                    env={
                        "PATH": os.defpath,
                        "GIT_CONFIG_NOSYSTEM": "1",
                        "GIT_CONFIG_GLOBAL": os.devnull,
                    },
                    capture_output=True,
                    timeout=10,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired):
                raise WorkspaceError("Git initialization failed") from None
            if result.returncode:
                raise WorkspaceError("Git initialization failed")
