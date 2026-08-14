from __future__ import annotations

from pathlib import Path


def test_stage_1_does_not_prebuild_future_capability_modules():
    source = Path(__file__).parents[1] / "src" / "morrow"
    forbidden_names = {"tools", "memory", "skills", "automation", "scheduler", "loop"}
    actual = {path.name for path in source.rglob("*") if path.is_dir()}
    assert not actual & forbidden_names
