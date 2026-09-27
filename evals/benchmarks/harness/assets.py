"""Prove that the offline wheel was built from the current Morrow source tree."""

from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
from pathlib import Path
from zipfile import ZipFile

from harness.fingerprint import git_fingerprint, sha256_file, sha256_tree, write_json


def wheel_source_mismatches(wheel: Path, source_root: Path) -> list[str]:
    source = {
        path.relative_to(source_root).as_posix(): path.read_bytes()
        for path in (source_root / "morrow").rglob("*")
        if path.is_file() and (path.suffix == ".py" or "gui_static" in path.parts)
    }
    with ZipFile(wheel) as archive:
        packaged = {
            name: archive.read(name)
            for name in archive.namelist()
            if name.startswith("morrow/")
            and (name.endswith(".py") or name.startswith("morrow/gui_static/"))
        }
    return sorted(
        name for name in source.keys() | packaged.keys() if source.get(name) != packaged.get(name)
    )


def build_manifest(repo: Path, assets: Path, wheel: Path) -> dict:
    mismatches = wheel_source_mismatches(wheel, repo / "src")
    if mismatches:
        raise ValueError(f"wheel differs from source in {len(mismatches)} Python files")
    python_assets = list(assets.glob("cpython-3.12*-x86_64-unknown-linux-gnu-install_only.tar.gz"))
    if len(python_assets) != 1:
        raise ValueError("expected exactly one CPython asset")
    compat_image = os.environ.get("MORROW_BENCH_COMPAT_IMAGE", "debian:11")
    inspected = (
        subprocess.run(
            ["docker", "image", "inspect", compat_image, "--format", "{{.Id}}"],
            capture_output=True,
            text=True,
            check=False,
        )
        if shutil.which("docker")
        else None
    )
    return {
        "schema_version": 1,
        "source_commit": git_fingerprint(repo)["commit"],
        "source_tree_sha256": sha256_tree(repo / "src" / "morrow"),
        "gui_static_sha256": sha256_tree(repo / "src" / "morrow" / "gui_static"),
        "wheel_name": wheel.name,
        "wheel_sha256": sha256_file(wheel),
        "python_sha256": sha256_file(python_assets[0]),
        "uv_sha256": sha256_file(assets / "uv-x86_64-unknown-linux-gnu"),
        "uvx_sha256": sha256_file(assets / "uvx-x86_64-unknown-linux-gnu"),
        "wheelhouse_sha256": sha256_tree(assets / "wheelhouse"),
        "dependency_lock_sha256": sha256_file(repo / "uv.lock"),
        "host_architecture": platform.machine(),
        "compat_image": compat_image,
        "compat_image_id": inspected.stdout.strip()
        if inspected and inspected.returncode == 0
        else None,
    }


def verify_bundle(repo: Path, assets: Path) -> dict:
    path = assets / "asset-manifest.json"
    if not path.is_file():
        raise ValueError("asset build manifest is missing; rebuild the wheel")
    stored = json.loads(path.read_text(encoding="utf-8"))
    wheels = list(assets.glob("morrow_agent-*.whl"))
    if len(wheels) != 1:
        raise ValueError("expected exactly one Morrow wheel")
    actual = build_manifest(repo, assets, wheels[0])
    if stored != actual:
        raise ValueError("asset build manifest differs from current source or assets")
    return actual


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("write", "verify"))
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--assets", type=Path, required=True)
    parser.add_argument("--wheel", type=Path)
    args = parser.parse_args()
    if args.action == "write":
        if args.wheel is None:
            parser.error("--wheel required for write")
        write_json(
            args.assets / "asset-manifest.json", build_manifest(args.repo, args.assets, args.wheel)
        )
    else:
        verify_bundle(args.repo, args.assets)
    print("asset provenance verified")


if __name__ == "__main__":
    main()
