"""Raw collection, fallback variants and weak effects never count as acceptance."""

import runpy
from pathlib import Path
from types import SimpleNamespace

verdict = runpy.run_path(str(Path(__file__).parents[1] / "evals/computer_use/campaign_verdict.py"))[
    "campaign_verdict"
]


def recovering_result():
    r = result()
    final = dict(r["provider_calls"][0], call_id="fresh", observation_id="cobs_fresh")
    stale = dict(final, call_id="stale", observation_id="cobs_stale")
    r["provider_calls"] = [stale, final]
    r["tool_diagnostics"] = [
        {
            "call_id": "stale",
            "result": {"outcome": {"status": "not_started", "error_code": "stale_observation"}},
        }
    ]
    r["action_outcomes"] = [
        {"status": "not_started", "error_code": "stale_observation"},
        {"status": "unknown"},
    ]
    return r


def test_proven_stale_recovery_counts_attempts_separately_from_native_effect():
    r = recovering_result()
    v = verdict(r)
    assert v["status"] == "passed"
    assert v["action_attempts"] == 2 and v["sdk_input_entries"] == 1
    assert v["recovered_not_started"] == 1
    assert v["native_completion"] == ["not_started", "unknown"]
    r["tool_diagnostics"][0] = {
        "call_id": "stale",
        "envelope": {
            "ok": False,
            "error": {"code": "preflight_failed", "details": [{"reason": "stale_observation"}]},
        },
    }
    assert verdict(r)["status"] == "passed"


def test_unknown_completed_uncorrelated_or_reused_observation_cannot_recover():
    for completion in ("unknown", "completed", None):
        r = recovering_result()
        r["tool_diagnostics"][0]["result"]["outcome"]["status"] = completion
        assert verdict(r)["verdict_reason"] == "action_count"
        assert verdict(r)["recovered_not_started"] == 0
    for mutation in ("missing", "other_call", "old_observation", "two_native", "wrong_variant"):
        r = recovering_result()
        if mutation == "missing":
            r["tool_diagnostics"] = []
        elif mutation == "other_call":
            r["tool_diagnostics"][0]["call_id"] = "other"
        elif mutation == "old_observation":
            r["provider_calls"][1]["observation_id"] = "cobs_stale"
        elif mutation == "two_native":
            r["sdk_input_entries"] = 2
        else:
            r["provider_calls"][0]["count"] = 2
        assert verdict(r)["status"] != "passed"


def test_export_health_revision_and_unknown_coordinates_are_independent():
    module = runpy.run_path("evals/computer_use/campaign_verdict.py")
    region = dict(x=1, y=2, width=10, height=10)
    assert module["wheel_inside_region"](region, [{"windowX": 2, "windowY": 3}])
    for coordinate in (None, "NaN", float("nan"), float("inf"), True):
        assert not module["wheel_inside_region"](region, [{"windowX": coordinate, "windowY": 3}])
    before = dict(
        schemaVersion=4,
        exportHealthy=True,
        revision=1,
        count=0,
        buttonActionCallbacks=0,
        rightMouseEvents=0,
        scrollOffset=0,
        text="",
        liveText="",
        liveSecureText="",
    )
    after = dict(before, revision=2, count=1)
    snapshot = module["fixture_snapshot_evidence"]
    r = result()
    r["fixture_snapshot_required"] = True
    assert verdict(r)["verdict_reason"] == "fixture_snapshot_invalid"
    r["fixture_snapshot"] = snapshot(before, after)
    assert verdict(r)["status"] == "passed"
    for changed in (
        dict(after, revision=1),
        dict(after, exportHealthy=False),
        dict(after, count=True),
        dict(after, scrollOffset=float("nan")),
    ):
        r["fixture_snapshot"] = snapshot(before, changed)
        assert verdict(r)["verdict_reason"] == "fixture_snapshot_invalid"


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


def attribute_result(postcondition="attribute_equals", native_entries=1):
    r = result("postcondition_attribute")
    r["provider_calls"][0].update(
        coordinate_target=False, element_target=True, postcondition=postcondition
    )
    r["sdk_input_entries"] = native_entries
    r["action_outcomes"] = [{"postcondition": "not_checked"}]
    return r


def test_attribute_unavailable_requires_the_requested_postcondition():
    assert verdict(attribute_result())["status"] == "unsupported"
    for postcondition in (None, "element_exists", "text_appears"):
        r = attribute_result(postcondition)
        assert verdict(r)["status"] == "failed"
        assert verdict(r)["verdict_reason"] == "wrong_postcondition"
        r["sdk_input_entries"] = 0
        r["action_outcomes"][0]["error_code"] = "unsupported_attribute"
        assert verdict(r)["status"] == "failed"


def denied_result():
    r = result("denied")
    r["provider_calls"][0].update(
        call_id="denied-click", coordinate_target=False, element_target=True
    )
    r.update(
        sdk_input_entries=0,
        approval_count=1,
        approval_decisions=[{"call_id": "denied-click", "approved": False}],
        independent_effect={"unchanged": True},
        tool_diagnostics=[
            {
                "call_id": "denied-click",
                "envelope": {"ok": False, "error": {"code": "approval_rejected"}},
            }
        ],
    )
    return r


def test_denial_needs_explicit_false_decision_and_matching_rejection():
    r = denied_result()
    assert verdict(r)["status"] == "passed"
    for code in ("approval_unavailable", "approval_preview_failed", "permission_denied", None):
        r = denied_result()
        r["tool_diagnostics"][0]["envelope"]["error"]["code"] = code
        assert verdict(r)["status"] == "failed"
    for approved in (True, None, 0):
        r = denied_result()
        r["approval_decisions"][0]["approved"] = approved
        assert verdict(r)["status"] == "failed"
    for key in ("approval_decisions", "tool_diagnostics"):
        r = denied_result()
        r[key] = []
        assert verdict(r)["status"] == "failed"
        r = denied_result()
        r[key][0]["call_id"] = "another-action"
        assert verdict(r)["status"] == "failed"


def test_denial_cannot_hide_native_entry_or_fixture_change():
    r = denied_result()
    r["sdk_input_entries"] = 1
    assert verdict(r)["status"] == "failed"
    r = denied_result()
    r["independent_effect"]["unchanged"] = False
    assert verdict(r)["status"] == "failed"


def test_unavailable_attribute_does_not_hide_failed_click_effect():
    r = attribute_result()
    r["independent_effect"]["counter_delta"] = 0
    assert verdict(r)["status"] == "failed"


async def test_collector_links_action_and_tool_error_without_network(monkeypatch):
    import json

    from morrow.adapters.models import openai_compatible
    from morrow.core.models import AssistantMessage, FunctionToolCall, ModelRef, ToolMessage
    from morrow.testing import ScriptedModelProvider

    directory = Path(__file__).parents[1] / "evals/computer_use"
    monkeypatch.syspath_prepend(str(directory))
    module = runpy.run_path(str(directory / "live_provider.py"))
    fake = ScriptedModelProvider(
        [
            AssistantMessage(
                tool_calls=(
                    FunctionToolCall(
                        id="current-action",
                        name="computer_action",
                        arguments=json.dumps(
                            {
                                "action": {
                                    "type": "click",
                                    "element_ref": "celem_1",
                                    "postcondition": {
                                        "type": "attribute_equals",
                                        "element_ref": "celem_1",
                                        "attribute": "enabled",
                                        "value": "true",
                                    },
                                },
                            }
                        ),
                    ),
                )
            ),
        ]
    )
    monkeypatch.setattr(openai_compatible, "OpenAICompatibleProvider", lambda *a, **k: fake)
    provider = module["NativeProvider"]("synthetic")
    message = ToolMessage(
        tool_call_id="current-action",
        content=json.dumps(
            {
                "ok": False,
                "error": {"code": "approval_rejected"},
            }
        ),
    )
    async for _ in provider.stream(ModelRef(provider_id="fixture", model_id="fixture"), [message]):
        pass
    assert provider.calls[0]["call_id"] == "current-action"
    assert provider.calls[0]["postcondition"] == "attribute_equals"
    assert provider.tool_diagnostics[0] == {
        "call_id": "current-action",
        "envelope": {"ok": False, "error": {"code": "approval_rejected"}},
        "result": {},
    }
