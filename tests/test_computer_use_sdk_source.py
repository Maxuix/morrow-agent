"""Source preparation refuses tampered provenance and preserves existing output."""

import json
import runpy
from pathlib import Path

import pytest


def test_source_preparation_never_overwrites_existing_files(tmp_path):
    module = runpy.run_path("evals/computer_use/prepare_sdk_source.py")
    output = tmp_path / "existing"
    output.mkdir()
    preserved = output / "user.txt"
    preserved.write_text("keep this file")
    with pytest.raises(ValueError, match="sdk_source_output_not_empty"):
        module["prepare"](Path("/missing-sdk-repository"), output)
    assert preserved.read_text() == "keep this file"


def test_patch_hash_mismatch_fails_before_git_or_output_creation(tmp_path):
    module = runpy.run_path("evals/computer_use/prepare_sdk_source.py")
    vendor = tmp_path / "vendor/cua-driver-security"
    vendor.mkdir(parents=True)
    (vendor / "security.patch").write_text("unexpected source")
    (vendor / "manifest.json").write_text(
        json.dumps({"upstream_commit": module["BASE"], "patch_sha256": "0" * 64})
    )
    module["prepare"].__globals__["__file__"] = str(
        tmp_path / "evals/computer_use/prepare_sdk_source.py"
    )
    output = tmp_path / "new-output"
    with pytest.raises(ValueError, match="sdk_source_manifest_invalid"):
        module["prepare"](Path("/missing-sdk-repository"), output)
    assert not output.exists()
