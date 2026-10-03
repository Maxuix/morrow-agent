"""Strict fixture verdicts; device completion and task effects remain independent."""

from __future__ import annotations


def campaign_verdict(result: dict) -> dict:
    case = result["case"]
    actions = [c for c in result.get("provider_calls", ()) if c.get("name") == "computer_action"]
    effects = result.get("independent_effect", {})
    expected = {
        "double_click": ("click", "element", "left", 2),
        "right_click": ("click", "element", "right", 1),
        "coordinate_click": ("click", "coordinate", "left", 1),
        "coordinate_scroll": ("scroll", "coordinate", None, None),
        "click": ("click", "element", "left", 1),
        "denied": ("click", "element", "left", 1),
        "postcondition_exists": ("click", "element", "left", 1),
        "postcondition_attribute": ("click", "element", "left", 1),
        "postcondition_text": ("type_text", "element", None, None),
        "secure": ("type_text", "element", None, None),
        "type_text": ("type_text", "element", None, None),
        "press_key": ("press_key", "element", None, None),
        "commit": ("press_key", "element", None, None),
        "hotkey": ("hotkey", "element", None, None),
        "scroll": ("scroll", "element", None, None),
    }
    actual = actions[0] if len(actions) == 1 else None

    def verdict(status, reason):
        return {
            "status": status,
            "verdict_reason": reason,
            "expected_variant": expected.get(case),
            "actual_variant": actual,
        }

    if not result.get("fixture_identity_unchanged") or result.get("provider_failures"):
        return verdict("failed", "identity_or_provider_failed")
    if case == "observe":
        good = not actions and bool(result.get("observation_meta"))
        if result.get("mode") == "hybrid":
            good = (
                good
                and result.get("visual_count", 0) > 0
                and result.get("image_hashes_match") is True
            )
        return verdict("passed" if good else "blocked", "observation_evidence")
    if not actions:
        return verdict("blocked", "action_not_attempted")
    if len(actions) != 1:
        return verdict("failed", "action_count")
    kind, target, button, count = expected[case]
    if (
        actual.get("action") != kind
        or not actual.get(target + "_target")
        or actual.get("element_target") == actual.get("coordinate_target")
        or (button is not None and actual.get("button", "left") != button)
        or (count is not None and actual.get("count", 1) != count)
    ):
        return verdict("failed", "wrong_variant")
    if case == "denied":
        good = (
            result.get("sdk_input_entries") == 0
            and result.get("approval_count") == 1
            and effects.get("unchanged")
        )
        return verdict("passed" if good else "failed", "denial_effect")
    if result.get("sdk_input_entries") != 1:
        outcomes = result.get("action_outcomes", ())
        codes = {o.get("error_code") for o in outcomes}
        for diagnostic in result.get("tool_diagnostics", ()):
            error = diagnostic.get("envelope", {}).get("error", {})
            codes.add(error.get("code"))
            codes.update(d.get("reason") for d in error.get("details", ()) if isinstance(d, dict))
        unsupported = bool(
            codes
            & {
                "unsupported_attribute",
                "unsupported_scroll_target",
                "unsupported_double_click_delivery",
                "unsupported_foreground_scroll_delivery",
            }
        )
        return verdict("unsupported" if unsupported else "blocked", "native_not_entered")
    if result.get("mode") == "hybrid" and result.get("image_hashes_match") is not True:
        return verdict("failed", "image_hash_mismatch")
    good = False
    if case in {"click", "coordinate_click", "postcondition_exists", "postcondition_attribute"}:
        good = effects.get("counter_delta") == 1
    elif case == "double_click":
        good = effects.get("mouse_click_counts") == [1, 2]
    elif case == "right_click":
        good = effects.get("right_mouse_delta") == 1
    elif case in {"type_text", "postcondition_text", "press_key", "hotkey", "secure"}:
        good = effects.get("exact_live_insert") is True and effects.get("correct_field") is True
        if case == "press_key":
            good = (
                good
                and actual.get("key") == "q"
                and effects.get("key_down_characters") == ["q"]
                and effects.get("key_down_fields") == ["fixture-text"]
                and effects.get("key_up_events") == 1
            )
        if case == "hotkey":
            good = (
                good
                and actual.get("keys") == ["shift", "y"]
                and effects.get("key_down_characters") == ["Y"]
                and effects.get("key_down_fields") == ["fixture-text"]
                and effects.get("key_up_events") == 1
            )
    elif case == "commit":
        good = (
            effects.get("committed_seed") is True and str(actual.get("key")).casefold() == "enter"
        )
    elif case in {"scroll", "coordinate_scroll"}:
        good = (
            effects.get("scroll_delta", 0) > 0
            and effects.get("wheel_inside_scroll_region") is True
            and actual.get("direction") == "down"
            and actual.get("amount") == 3
        )
    if case == "postcondition_attribute" and any(
        o.get("postcondition") == "not_checked" for o in result.get("action_outcomes", ())
    ):
        return verdict("unsupported", "sdk_exact_attribute_readback_unavailable")
    conditions = {
        "postcondition_exists": "element_exists",
        "postcondition_attribute": "attribute_equals",
        "postcondition_text": "text_appears",
    }
    if case in conditions:
        good = (
            good
            and actual.get("postcondition") == conditions[case]
            and any(o.get("postcondition") == "passed" for o in result.get("action_outcomes", ()))
        )
    return verdict("passed" if good else "failed", "independent_effect")
