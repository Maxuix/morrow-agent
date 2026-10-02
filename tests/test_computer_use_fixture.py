"""The independent acceptance oracle cannot leak secure input or lose updates."""

from __future__ import annotations

import importlib.util
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

fixture_path = Path(__file__).parents[1] / "evals/computer_use/browser_fixture.py"
spec = importlib.util.spec_from_file_location("morrow_browser_fixture", fixture_path)
assert spec and spec.loader
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)
FixtureInputError, FixtureState = fixture.FixtureInputError, fixture.FixtureState


def test_fixture_exports_real_unicode_counts_and_scroll_without_secure_bytes(tmp_path):
    state = FixtureState(tmp_path)
    initial = state.read()
    state.apply({"kind": "increment"})
    state.apply({"kind": "text", "value": "Morrow 中文 🧭"})
    state.apply({"kind": "scroll", "value": 176.5})
    state.apply({"kind": "secure", "value": True})
    actual = json.loads((tmp_path / "state.json").read_bytes())
    assert actual == state.read()
    assert actual["instance_id"] == initial["instance_id"]
    assert actual["revision"] == 4
    assert actual["count"] == 1
    assert actual["text"] == "Morrow 中文 🧭"
    assert actual["scroll_offset"] == 176.5
    assert actual["secure_field_populated"] is True
    assert (tmp_path / "state.json").stat().st_mode & 0o777 == 0o600
    assert tmp_path.stat().st_mode & 0o777 == 0o700


@pytest.mark.parametrize(
    "event",
    [
        {"kind": "secure", "value": "synthetic-password-never-export"},
        {"kind": "secure", "value": True, "password": "synthetic-password-never-export"},
        {"kind": "increment", "value": 1},
        {"kind": "text", "value": "界" * 683},
        {"kind": "scroll", "value": True},
        {"kind": "scroll", "value": float("inf")},
        {"kind": "scroll", "value": float("nan")},
        {"kind": "scroll", "value": 10**500},
        {"kind": "scroll", "value": -1},
        {"kind": "unknown"},
        [],
    ],
)
def test_invalid_fixture_events_leave_the_oracle_unchanged(tmp_path, event):
    state = FixtureState(tmp_path)
    before = (tmp_path / "state.json").read_bytes()
    with pytest.raises(FixtureInputError, match="^invalid_event$"):
        state.apply(event)
    assert (tmp_path / "state.json").read_bytes() == before
    assert state.read()["revision"] == 0


def test_concurrent_fixture_events_have_one_durable_revision_per_effect(tmp_path):
    state = FixtureState(tmp_path)
    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(lambda _: state.apply({"kind": "increment"}), range(24)))
    assert sorted(result["revision"] for result in results) == list(range(1, 25))
    assert state.read()["count"] == 24
    assert json.loads((tmp_path / "state.json").read_bytes())["count"] == 24


def test_failed_fixture_export_does_not_report_a_completed_effect(tmp_path, monkeypatch):
    state = FixtureState(tmp_path)
    before = (tmp_path / "state.json").read_bytes()

    def fail_replace(*args):
        raise OSError("synthetic-publication-failure")

    monkeypatch.setattr(fixture.os, "replace", fail_replace)
    with pytest.raises(OSError):
        state.apply({"kind": "increment"})
    assert (tmp_path / "state.json").read_bytes() == before
    assert state.read()["count"] == 0
    assert list(tmp_path.iterdir()) == [tmp_path / "state.json"]
