"""Build an offline, locally versioned guarded SDK wheel from verified source.

Requires provisioned Rust 1.97.1, Cargo dependencies, uv and hatchling's build
dependencies. No SDK process, native Driver, installation or publication runs.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import runpy
import shutil
import subprocess
from pathlib import Path

VERSION = "0.30.4+morrow.1"
WHEEL_TAG = "py3-none-macosx_14_0_arm64"


def build_environment(target: Path) -> dict[str, str]:
    environment = dict(os.environ)
    for key in ("RUSTFLAGS", "CARGO_ENCODED_RUSTFLAGS", "RUSTC_WRAPPER", "RUSTC_WORKSPACE_WRAPPER"):
        environment.pop(key, None)
    environment.update(
        MACOSX_DEPLOYMENT_TARGET="14.0",
        # On the acceptance host, stripped proc-macro dylibs fail dlopen with
        # a mis-aligned LINKEDIT string pool. Keep native symbols intact.
        CARGO_PROFILE_RELEASE_STRIP="none",
        CARGO_TARGET_DIR=str(target),
        CUA_DRIVER_WHEEL_TAG=WHEEL_TAG,
    )
    return environment


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build(repository: Path, output: Path, target: Path) -> dict:
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise ValueError("sdk_wheel_output_not_empty")
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        raise ValueError("sdk_wheel_host_unsupported")
    if target == output or output in target.parents or target in output.parents:
        raise ValueError("sdk_wheel_target_overlap")
    prepare = runpy.run_path(str(Path(__file__).with_name("prepare_sdk_source.py")))["prepare"]
    provenance = prepare(repository, output / "source")
    environment = build_environment(target)
    rust = output / "source/libs/cua-driver/rust"
    subprocess.run(
        [
            "cargo",
            "build",
            "-p",
            "cua-driver-sdk",
            "-p",
            "cua-driver",
            "--release",
            "--locked",
            "--offline",
        ],
        cwd=rust,
        env=environment,
        check=True,
    )
    stage = output / "python"
    shutil.copytree(output / "source/libs/cua-driver/python", stage)
    shutil.copyfile(output / "source/LICENSE.md", stage / "LICENSE.md")
    project = stage / "pyproject.toml"
    original = project.read_text()
    if original.count('version = "0.30.4"') != 1:
        raise ValueError("sdk_wheel_upstream_version_invalid")
    project.write_text(original.replace('version = "0.30.4"', f'version = "{VERSION}"'))
    package = stage / "src/cua_driver"
    initializer = package / "__init__.py"
    original = initializer.read_text()
    if original.count('__version__ = "0.30.4"') != 1:
        raise ValueError("sdk_wheel_module_version_invalid")
    initializer.write_text(original.replace('__version__ = "0.30.4"', f'__version__ = "{VERSION}"'))
    (package / "bin").mkdir(exist_ok=True)
    artifacts = {
        "libcua_driver_sdk.dylib": target / "release/libcua_driver_sdk.dylib",
        "bin/cua-driver": target / "release/cua-driver",
    }
    for name, source in artifacts.items():
        shutil.copy2(source, package / name)
    evidence = {
        **provenance,
        "release_built": True,
        "distribution_version": VERSION,
        "wheel_tag": WHEEL_TAG,
        "minimum_macos": "14.0",
        "strip": "none",
        "rust_version": subprocess.check_output(
            ["rustc", "--version"], cwd=rust, env=environment, text=True
        ).strip(),
        "artifacts_sha256": {name: sha256(package / name) for name in artifacts},
        "installed_validation_passed": False,
    }
    (package / "_morrow_security.json").write_text(json.dumps(evidence, indent=2) + "\n")
    subprocess.run(
        ["uv", "build", "--wheel", "--offline", "--out-dir", str(output / "wheels")],
        cwd=stage,
        env=environment,
        check=True,
    )
    wheels = list((output / "wheels").glob("*.whl"))
    if len(wheels) != 1 or not wheels[0].name.endswith(WHEEL_TAG + ".whl"):
        raise ValueError("sdk_wheel_tag_invalid")
    evidence["wheel_sha256"] = sha256(wheels[0])
    evidence["wheel_file"] = wheels[0].name
    (output / "morrow-security-wheel.json").write_text(json.dumps(evidence, indent=2) + "\n")
    return evidence


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sdk-repository", type=Path, required=True)
    parser.add_argument("--output-directory", type=Path, required=True)
    parser.add_argument("--cargo-target-directory", type=Path, required=True)
    args = parser.parse_args()
    print(
        json.dumps(
            build(
                args.sdk_repository.resolve(),
                args.output_directory.resolve(),
                args.cargo_target_directory.resolve(),
            ),
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
