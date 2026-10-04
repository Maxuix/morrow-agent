"""Build the prepared functional SDK on macOS arm64 with an installed Rust toolchain.

Uses Cargo.lock and the upstream Python bindings; does not download a binary SDK.
The caller owns toolchain/dependency provisioning. No wheel is installed here.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import tempfile
import tomllib
from pathlib import Path

from prepare_functional_sdk import VENDOR


def build(source: Path, output: Path, *, offline: bool = False) -> dict:
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        raise ValueError("functional_sdk_build_requires_macos_arm64")
    manifest = json.loads((VENDOR / "manifest.json").read_text())
    proof = json.loads((source / "morrow-functional-source.json").read_text())
    if any(proof.get(k) != manifest[k] for k in ("upstream_commit", "patch_sha256")):
        raise ValueError("sdk_source_manifest_invalid")
    for name, expected in proof["patched_files"].items():
        if hashlib.sha256((source / name).read_bytes()).hexdigest() != expected:
            raise ValueError("sdk_prepared_source_changed")
    rust = source / "libs/cua-driver/rust"
    python = source / "libs/cua-driver/python"
    version = manifest["distribution_version"]
    if tomllib.loads((python / "pyproject.toml").read_text())["project"]["version"] != version:
        raise ValueError("sdk_distribution_version_mismatch")
    environment = {
        **os.environ,
        "MACOSX_DEPLOYMENT_TARGET": "14.0",
        # Stripped proc-macro dylibs can have a misaligned LINKEDIT on this toolchain.
        "CARGO_PROFILE_RELEASE_STRIP": "none",
    }
    command = [
        "cargo",
        "build",
        "-p",
        "cua-driver-sdk",
        "-p",
        "cua-driver",
        "--release",
        "--locked",
    ]
    if offline:
        command.append("--offline")
    subprocess.run(command, cwd=rust, env=environment, check=True)
    target = Path(environment.get("CARGO_TARGET_DIR", str(rust / "target"))) / "release"
    payloads = {
        "bin/cua-driver": target / "cua-driver",
        "libcua_driver_sdk.dylib": target / "libcua_driver_sdk.dylib",
    }
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="morrow-functional-wheel-") as directory:
        package = Path(directory) / "python"
        shutil.copytree(python, package)
        for name, binary in payloads.items():
            destination = package / "src/cua_driver" / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(binary, destination)
            subprocess.run(["codesign", "--verify", str(destination)], check=True)
        subprocess.run(
            ["uv", "build", "--wheel", str(package), "--out-dir", str(output)],
            env={**environment, "CUA_DRIVER_WHEEL_TAG": "py3-none-macosx_14_0_arm64"},
            check=True,
        )
    wheel = output / f"cua_driver-{version}-py3-none-macosx_14_0_arm64.whl"
    evidence = {
        **manifest,
        "release_built": True,
        "wheel": wheel.name,
        "wheel_sha256": hashlib.sha256(wheel.read_bytes()).hexdigest(),
        "payload_sha256": {
            name: hashlib.sha256(p.read_bytes()).hexdigest() for name, p in payloads.items()
        },
        "rustc": subprocess.check_output(["rustc", "--version"], text=True).strip(),
        "cargo": subprocess.check_output(["cargo", "--version"], text=True).strip(),
        "native_acceptance": "separate evidence required",
    }
    (output / "build-provenance.json").write_text(json.dumps(evidence, indent=2) + "\n")
    return evidence


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-directory", type=Path, required=True)
    parser.add_argument("--output-directory", type=Path, required=True)
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args()
    print(
        json.dumps(
            build(
                args.source_directory.resolve(),
                args.output_directory.resolve(),
                offline=args.offline,
            )
        )
    )


if __name__ == "__main__":
    main()
