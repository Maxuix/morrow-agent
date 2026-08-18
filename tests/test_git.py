from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from morrow.adapters.local.git import GitAdapterError, GitInspectionAdapter
from morrow.core.local_tools import GitRepositoryState
from morrow.services.files import WorkspaceFileService, WorkspacePathResolver
from morrow.services.git import GitInspectionService, GitServiceError

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is unavailable")


def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        text=True,
        env={
            "PATH": os.environ.get("PATH", os.defpath),
            "HOME": str(root),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_TERMINAL_PROMPT": "0",
        },
    )


def _service(root: Path) -> GitInspectionService:
    return GitInspectionService(WorkspaceFileService(WorkspacePathResolver(root)))


def _repository(root: Path) -> None:
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "test@example.invalid")
    _git(root, "config", "user.name", "Morrow Test")


def test_git_status_and_diff_are_bounded_read_only_and_protect_content(tmp_path):
    root = tmp_path / "repo"
    _repository(root)
    (root / "main.py").write_text("print('before')\n", encoding="utf-8")
    (root / ".env").write_text("SECRET=initial\n", encoding="utf-8")
    (root / "auth.py").write_text("KEY = None\n", encoding="utf-8")
    _git(root, "add", "main.py", ".env", "auth.py")
    _git(root, "commit", "-qm", "initial")
    (root / "main.py").write_text("print('after')\n", encoding="utf-8")
    (root / ".env").write_text("SECRET=changed\n", encoding="utf-8")
    (root / "auth.py").write_text("-----BEGIN RSA PRIVATE KEY-----\nrsa-secret\n", encoding="utf-8")
    (root / "new.txt").write_text("new\n", encoding="utf-8")

    service = _service(root)
    status = service.status()
    assert status.repository is True
    assert status.repository_state is GitRepositoryState.DIRTY
    assert status.branch or status.detached
    paths = {entry.path for entry in status.entries}
    assert {"main.py", ".env", "new.txt"} <= paths
    assert next(entry for entry in status.entries if entry.path == ".env").protected is True

    diff = service.diff()
    assert "print('after')" in diff.diff
    assert "SECRET=changed" not in diff.diff
    assert "RSA PRIVATE KEY" not in diff.diff
    assert "rsa-secret" not in diff.diff
    assert "[protected diff omitted]" in diff.diff
    assert ".env" in {item.path for item in diff.protected_paths}
    assert diff.truncated is False
    assert not (root / ".git" / "index.lock").exists()

    (root / "main.py").write_text("print('staged')\n", encoding="utf-8")
    _git(root, "add", "main.py")
    staged = service.diff(staged=True, paths=("main.py",))
    assert staged.staged is True
    assert "print('staged')" in staged.diff
    assert staged.protected_paths == ()


def test_git_non_repository_is_a_normal_bounded_result(tmp_path):
    root = tmp_path / "plain"
    root.mkdir()
    result = _service(root).status()
    assert result.repository is False
    assert result.repository_state is GitRepositoryState.NOT_REPOSITORY
    assert result.entries == ()
    assert _service(root).diff().repository is False


def test_external_git_metadata_is_rejected_without_read_grant(tmp_path):
    root = tmp_path / "workspace"
    external = tmp_path / "external-git"
    root.mkdir()
    _git(root.parent, "init", "--separate-git-dir", str(external), str(root))
    with pytest.raises(GitServiceError) as error:
        _service(root).status()
    assert error.value.code == "external_git_metadata"


def test_git_disables_external_diff_and_bounds_adapter_output(tmp_path):
    root = tmp_path / "repo"
    _repository(root)
    (root / "main.txt").write_text("before\n", encoding="utf-8")
    _git(root, "add", "main.txt")
    _git(root, "commit", "-qm", "initial")
    marker = tmp_path / "external-diff-ran"
    script = tmp_path / "external-diff.sh"
    script.write_text(f"#!/bin/sh\ntouch {marker}\n", encoding="utf-8")
    script.chmod(0o700)
    _git(root, "config", "diff.external", str(script))
    (root / "main.txt").write_text("after\n", encoding="utf-8")

    diff = _service(root).diff()
    assert "after" in diff.diff
    assert not marker.exists()

    noisy = tmp_path / "noisy.py"
    noisy.write_text(
        "#!/usr/bin/env python3\nimport sys\nsys.stdout.write('x' * 100000)\n",
        encoding="utf-8",
    )
    noisy.chmod(0o700)
    adapter = GitInspectionAdapter(executable=str(noisy), max_output_bytes=1024)
    output = adapter.run(tmp_path, ("ignored",))
    assert output.truncated is True
    assert len(output.stdout) == 1024


def test_git_adapter_timeout_is_typed(tmp_path):
    sleeper = tmp_path / "sleeper.py"
    sleeper.write_text(
        "#!/usr/bin/env python3\nimport time\ntime.sleep(5)\n",
        encoding="utf-8",
    )
    sleeper.chmod(0o700)
    adapter = GitInspectionAdapter(executable=sys.executable)
    with pytest.raises(GitAdapterError) as error:
        adapter.run(tmp_path, (str(sleeper),), timeout_seconds=0.05)
    assert error.value.code == "git_timeout"
