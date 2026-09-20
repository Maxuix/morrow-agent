"""Ownership contract: every claimed existing file must really exist at C0.

This keeps tests/fixtures/parallel_contracts/ownership.json honest as the tree evolves:
when a listed file moves or is renamed, this test fails and the coordinator
updates the contract instead of lanes silently losing their owner map.
"""

from __future__ import annotations

import json
from pathlib import Path

CONTRACTS_DIR = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "parallel_contracts"


def _ownership() -> dict:
    return json.loads((CONTRACTS_DIR / "ownership.json").read_text(encoding="utf-8"))


def test_every_claimed_existing_file_exists():
    root = Path(__file__).resolve().parents[2]
    missing = []
    for lane, block in _ownership()["owners"].items():
        for relative in block["existing_files"]:
            if not (root / relative).exists():
                missing.append(f"{lane}: {relative}")
    assert missing == [], f"ownership.json lists missing files: {missing}"


def test_no_file_has_two_owners():
    seen: dict[str, str] = {}
    duplicated = []
    for lane, block in _ownership()["owners"].items():
        for relative in block["existing_files"]:
            if relative in seen and seen[relative] != lane:
                duplicated.append(f"{relative}: {seen[relative]} vs {lane}")
            seen[relative] = lane
    assert duplicated == [], f"files with conflicting owners: {duplicated}"


def test_lane_worktree_and_branch_plan_is_complete():
    lanes = _ownership()["lanes"]
    assert set(lanes) == {"A", "B", "C", "D"}
    for name, lane in lanes.items():
        assert lane["worktree"].startswith("/"), name
        assert lane["initial_branch"], name
        assert lane["final_branch"], name
        assert lane["subplan_order"], name
