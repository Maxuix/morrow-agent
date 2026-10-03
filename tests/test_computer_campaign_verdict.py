"""Raw collection, fallback variants and weak effects never count as acceptance."""

import runpy
from pathlib import Path
from types import SimpleNamespace

verdict = runpy.run_path(str(Path(__file__).parents[1] / "evals/computer_use/campaign_verdict.py"))[
    "campaign_verdict"
]


def result(case="coordinate_click", **changes):
    return dict(
        case=case,
        mode="hybrid",
        fixture_identity_unchanged=True,
        sdk_input_entries=1,
        image_hashes_match=True,
        provider_calls=[dict(name="computer_action", action="click", coordinate_target=True)],
        independent_effect={"counter_delta": 1},
        **changes,
    )


def test_coordinate_fallback_cannot_pass_by_incrementing_counter():
    r = result()
    assert verdict(r)["status"] == "passed"
    r["provider_calls"][0].update(coordinate_target=False, element_target=True)
    assert verdict(r)["status"] == "failed"


def test_double_click_needs_native_click_counts_not_business_counter():
    r = result("double_click")
    r["provider_calls"][0].update(coordinate_target=False, element_target=True, count=2)
    assert verdict(r)["status"] == "failed"
    r["independent_effect"]["mouse_click_counts"] = [1, 2]
    assert verdict(r)["status"] == "passed"


def test_key_requires_exact_live_field_and_requested_key():
    r = result("press_key")
    r["provider_calls"][0].update(
        action="press_key", coordinate_target=False, element_target=True, key="q"
    )
    r["independent_effect"] = dict(
        exact_live_insert=True,
        correct_field=True,
        key_down_characters=["q"],
        key_down_fields=["fixture-text"],
        key_up_events=1,
    )
    assert verdict(r)["status"] == "passed"
    r["provider_calls"][0]["key"] = "z"
    assert verdict(r)["status"] == "failed"


def test_missing_image_or_no_action_is_not_passed():
    r = result()
    r["image_hashes_match"] = False
    assert verdict(r)["status"] == "failed"
    r["provider_calls"] = []
    assert verdict(r)["status"] == "blocked"


def test_preflight_unsupported_scroll_is_distinct_from_missing_action():
    r = result("scroll")
    r["sdk_input_entries"] = 0
    r["provider_calls"][0].update(action="scroll", coordinate_target=False, element_target=True)
    r["tool_diagnostics"] = [
        {"envelope": {"error": {"details": [{"reason": "unsupported_scroll_target"}]}}}
    ]
    assert verdict(r)["status"] == "unsupported"


def test_commit_needs_committed_seed_and_enter_variant():
    r = result("commit")
    r["provider_calls"][0].update(
        action="press_key", coordinate_target=False, element_target=True, key="Enter"
    )
    r["independent_effect"] = {"committed_seed": True}
    assert verdict(r)["status"] == "passed"
    r["independent_effect"]["committed_seed"] = False
    assert verdict(r)["status"] == "failed"


def test_scroll_needs_independent_wheel_in_real_region():
    r = result("coordinate_scroll")
    r["provider_calls"][0].update(action="scroll", direction="down", amount=3)
    r["independent_effect"] = {"scroll_delta": 60, "wheel_inside_scroll_region": True}
    assert verdict(r)["status"] == "passed"
    r["independent_effect"]["wheel_inside_scroll_region"] = False
    assert verdict(r)["status"] == "failed"


def test_evidence_projection_accepts_semantic_sdk_read_without_frame(monkeypatch):
    directory = Path(__file__).parents[1] / "evals/computer_use"
    monkeypatch.syspath_prepend(str(directory))
    project = runpy.run_path(str(directory / "live_provider.py"))["sdk_frame_metadata"]
    state = SimpleNamespace(
        window_bounds=None,
        screenshot_width=None,
        screenshot_height=None,
        screenshot_scale=None,
    )
    assert project(state)["window_bounds"] is None
    state.window_bounds = SimpleNamespace(x=100, y=120, width=520, height=512)
    state.screenshot_width, state.screenshot_height, state.screenshot_scale = 640, 630, 2
    assert project(state)["window_bounds"]["x"] == 100
    assert project(state)["width"] == 640
