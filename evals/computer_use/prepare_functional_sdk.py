"""Apply the functional SDK patch to an archive of the exact upstream source.

No network, native execution, wheel installation, or changes to the input checkout.
The output must be empty. The retired content-filter patch is never used.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import tarfile
import tempfile
from pathlib import Path

BASE = "bf6c76786d938070f4ecf1e44004752f69f518b8"
VENDOR = Path(__file__).resolve().parents[2] / "vendor/cua-driver-functional"


def prepare(repository: Path, output: Path) -> dict:
    manifest = json.loads((VENDOR / "manifest.json").read_text())
    patch = VENDOR / "functional.patch"
    patch_sha = hashlib.sha256(patch.read_bytes()).hexdigest()
    environment = {**os.environ, "GIT_NO_LAZY_FETCH": "1", "GIT_TERMINAL_PROMPT": "0"}
    if manifest["upstream_commit"] != BASE or manifest["patch_sha256"] != patch_sha:
        raise ValueError("sdk_source_manifest_invalid")
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise ValueError("sdk_source_output_not_empty")
    if (
        subprocess.check_output(
            ["git", "-C", str(repository), "cat-file", "-t", BASE], text=True, env=environment
        ).strip()
        != "commit"
    ):
        raise ValueError("sdk_source_commit_missing")
    output.mkdir(parents=True, exist_ok=True)
    for subtree in ("libs/cua-driver/rust", "libs/cua-driver/python"):
        with tempfile.TemporaryFile() as archive:
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(repository),
                    "archive",
                    "--format=tar",
                    "--prefix=" + subtree + "/",
                    BASE + ":" + subtree,
                ],
                stdout=archive,
                check=True,
                env=environment,
            )
            archive.seek(0)
            with tarfile.open(fileobj=archive) as source:
                source.extractall(output, filter="data")
    (output / "LICENSE.md").write_bytes(
        subprocess.check_output(
            ["git", "-C", str(repository), "show", BASE + ":LICENSE.md"], env=environment
        )
    )
    subprocess.run(["git", "apply", "--check", str(patch)], cwd=output, check=True)
    subprocess.run(["git", "apply", str(patch)], cwd=output, check=True)
    files = {}
    for line in patch.read_text().splitlines():
        if line.startswith("+++ b/"):
            name = line.removeprefix("+++ b/")
            files[name] = hashlib.sha256((output / name).read_bytes()).hexdigest()
    evidence = {
        **manifest,
        "source_prepared": True,
        "patched_files": files,
        "native_executed": False,
        "release_built": False,
    }
    (output / "morrow-functional-source.json").write_text(json.dumps(evidence, indent=2) + "\n")
    return evidence


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sdk-repository", type=Path, required=True)
    parser.add_argument("--output-directory", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(prepare(args.sdk_repository.resolve(), args.output_directory.resolve())))


if __name__ == "__main__":
    main()
