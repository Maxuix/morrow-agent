"""Content fingerprints for reproducible benchmark runs (never store credentials)."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any


def sha256_file(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_tree(path: Path) -> str | None:
    if not path.is_dir():
        return None
    digest = hashlib.sha256()
    for file in sorted(item for item in path.rglob("*") if item.is_file()):
        if ".git" in file.relative_to(path).parts:
            continue
        digest.update(file.relative_to(path).as_posix().encode())
        digest.update(b"\0")
        digest.update(bytes.fromhex(sha256_file(file) or ""))
    return digest.hexdigest()


def git_fingerprint(path: Path) -> dict[str, str | None]:
    if not path.is_dir():
        return {"commit": None, "dirty_patch_sha256": None}

    def git(*args: str) -> bytes | None:
        result = subprocess.run(["git", "-C", str(path), *args], capture_output=True, check=False)
        return result.stdout if result.returncode == 0 else None

    top = git("rev-parse", "--show-toplevel")
    if top is None or Path(top.decode().strip()).resolve() != path.resolve():
        return {"commit": None, "dirty_patch_sha256": None}
    commit = git("rev-parse", "HEAD")
    patch = git("diff", "HEAD", "--binary", "--no-ext-diff")
    untracked = git("ls-files", "--others", "--exclude-standard", "-z")
    if patch is None or untracked is None:
        dirty_digest = None
    else:
        digest = hashlib.sha256(patch)
        for relative in sorted(item for item in untracked.split(b"\0") if item):
            file = path / relative.decode("utf-8", errors="surrogateescape")
            digest.update(relative)
            digest.update(b"\0")
            digest.update(bytes.fromhex(sha256_file(file) or ""))
        dirty_digest = digest.hexdigest() if patch or untracked else None
    return {
        "commit": commit.decode().strip() if commit else None,
        "dirty_patch_sha256": dirty_digest,
    }


def run_fingerprint(bench_dir: Path, *, settings: dict[str, Any]) -> dict[str, Any]:
    assets = bench_dir / "assets"
    wheel = next(iter(sorted(assets.glob("morrow_agent-*.whl"))), None)
    python_asset = next(
        iter(sorted(assets.glob("cpython-3.12*-x86_64-unknown-linux-gnu-install_only.tar.gz"))),
        None,
    )
    repo = bench_dir.parent.parent
    return {
        "schema_version": 1,
        "source": git_fingerprint(repo),
        "wheel": {
            "name": wheel.name if wheel else None,
            "sha256": sha256_file(wheel) if wheel else None,
        },
        "assets": {
            "python_sha256": sha256_file(python_asset) if python_asset else None,
            "uv_sha256": sha256_file(assets / "uv-x86_64-unknown-linux-gnu"),
            "uvx_sha256": sha256_file(assets / "uvx-x86_64-unknown-linux-gnu"),
            "wheelhouse_sha256": sha256_tree(assets / "wheelhouse"),
        },
        "dependency_lock_sha256": sha256_file(repo / "uv.lock"),
        "harbor": git_fingerprint(bench_dir / "vendor" / "harbor"),
        "settings": settings,
    }


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, delete=False
    ) as out:
        temporary = Path(out.name)
        try:
            out.write(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
            out.flush()
            os.fsync(out.fileno())
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
