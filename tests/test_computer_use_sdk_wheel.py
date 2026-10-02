"""Packaging refusals protect existing files before source or build execution."""

import runpy

import pytest


@pytest.fixture
def module():
    return runpy.run_path("evals/computer_use/build_sdk_wheel.py")


def test_existing_output_is_preserved_before_any_build(module, tmp_path):
    output = tmp_path / "output"
    output.mkdir()
    preserved = output / "user.txt"
    preserved.write_text("keep")
    with pytest.raises(ValueError, match="sdk_wheel_output_not_empty"):
        module["build"](tmp_path / "missing", output, tmp_path / "target")
    assert preserved.read_text() == "keep"


def test_overlapping_target_is_rejected_before_source_creation(module, tmp_path, monkeypatch):
    monkeypatch.setattr(module["platform"], "system", lambda: "Darwin")
    monkeypatch.setattr(module["platform"], "machine", lambda: "arm64")
    output = tmp_path / "output"
    with pytest.raises(ValueError, match="sdk_wheel_target_overlap"):
        module["build"](tmp_path / "missing", output, output / "target")
    assert not output.exists()


def test_unsupported_host_does_not_create_source(module, tmp_path, monkeypatch):
    monkeypatch.setattr(module["platform"], "system", lambda: "Linux")
    output = tmp_path / "output"
    with pytest.raises(ValueError, match="sdk_wheel_host_unsupported"):
        module["build"](tmp_path / "missing", output, tmp_path / "target")
    assert not output.exists()
