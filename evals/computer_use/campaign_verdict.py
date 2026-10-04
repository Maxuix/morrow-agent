"""Strict fixture verdicts; device completion and task effects remain independent."""

from __future__ import annotations

import math


def wheel_inside_region(region, wheels):
    if not isinstance(region, dict) or not wheels:
        return False
    values = [region.get(k) for k in ("x", "y", "width", "height")]
    if any(type(v) not in (int, float) or not math.isfinite(v) for v in values):
        return False
    x, y, width, height = values
    if width <= 0 or height <= 0:
        return False
    for event in wheels:
        px, py = event.get("windowX"), event.get("windowY")
        if (
            event.get("positionKnown") is False
            or type(px) not in (int, float)
            or type(py) not in (int, float)
            or not math.isfinite(px)
            or not math.isfinite(py)
            or not (x <= px < x + width and y <= py < y + height)
        ):
            return False
    return True


def fixture_snapshot_evidence(before, after):
    """A changed counter in an unhealthy or stale export cannot establish an effect."""
    revisions = [s.get("revision") for s in (before, after)]
    healthy = all(
        s.get("schemaVersion") == 4 and s.get("exportHealthy") is True for s in (before, after)
    )
    valid = all(type(r) is int and r > 0 for r in revisions)
    for state in (before, after):
        valid = valid and all(
            type(state.get(k)) is int and state[k] >= 0
            for k in ("count", "buttonActionCallbacks", "rightMouseEvents")
        )
        offset = state.get("scrollOffset")
        valid = valid and type(offset) in (int, float) and math.isfinite(offset)
        valid = valid and all(
            isinstance(state.get(k), str) for k in ("text", "liveText", "liveSecureText")
        )
    return {
        "healthy": healthy,
        "fields_valid": bool(valid),
        "revision_before": revisions[0],
        "revision_after": revisions[1],
        "revision_advanced": bool(valid and revisions[1] > revisions[0]),
    }


def recoverable_stale_attempt(call, diagnostics):
    call_id = call.get("call_id")
    matching = [d for d in diagnostics if call_id and d.get("call_id") == call_id]
    if not matching:
        return False
    for diagnostic in matching:
        envelope, result = diagnostic.get("envelope", {}), diagnostic.get("result", {})
        outcome = result.get("outcome", {})
        error = envelope.get("error", {})
        refused = (
            envelope.get("ok") is False
            and error.get("code") == "preflight_failed"
            and error.get("details") == [{"reason": "stale_observation"}]
        )
        not_started = (
            outcome.get("status") == "not_started"
            and outcome.get("error_code") == "stale_observation"
        )
        if outcome.get("status") in {"unknown", "completed"} or not (refused or not_started):
            return False
    return True


def final_action_outcome(call, diagnostics):
    """Match the final call, accepting repeated identical history projections."""
    call_id = call.get("call_id")
    matching = [d for d in diagnostics if call_id and d.get("call_id") == call_id]
    if not matching:
        return None
    outcome = matching[0].get("result", {}).get("outcome")
    if not isinstance(outcome, dict) or outcome.get("status") not in {"unknown", "completed"}:
        return None
    if any(
        d.get("envelope", {}).get("ok") is not True or d.get("result", {}).get("outcome") != outcome
        for d in matching
    ):
        return None
    return outcome


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
    actual = actions[-1] if actions else None
    recovered = 0

    def verdict(status, reason):
        return {
            "status": status,
            "verdict_reason": reason,
            "expected_variant": expected.get(case),
            "actual_variant": actual,
            "action_attempts": len(actions),
            "sdk_input_entries": result.get("sdk_input_entries"),
            "recovered_not_started": recovered,
            "native_completion": [o.get("status") for o in result.get("action_outcomes", ())],
        }

    if not result.get("fixture_identity_unchanged") or result.get("provider_failures"):
        return verdict("failed", "identity_or_provider_failed")
    snapshot_required = result.get("fixture_snapshot_required") or "fixture_snapshot" in result
    snapshot = result.get("fixture_snapshot", {})
    if snapshot_required:
        before, after = snapshot.get("revision_before"), snapshot.get("revision_after")
        if not (
            snapshot.get("healthy") is True
            and snapshot.get("fields_valid") is True
            and type(before) is int
            and type(after) is int
            and 0 < before <= after
        ):
            return verdict("failed", "fixture_snapshot_invalid")
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
        call_ids = [a.get("call_id") for a in actions]
        observations = [a.get("observation_id") for a in actions]
        if (
            case == "denied"
            or not all(call_ids)
            or len(set(call_ids)) != len(actions)
            or not all(observations)
            or len(set(observations)) != len(actions)
            or not all(
                recoverable_stale_attempt(a, result.get("tool_diagnostics", ()))
                for a in actions[:-1]
            )
        ):
            return verdict("failed", "action_count")
        recovered = len(actions) - 1
    kind, target, button, count = expected[case]
    if any(
        a.get("action") != kind
        or not a.get(target + "_target")
        or a.get("element_target") == a.get("coordinate_target")
        or (button is not None and a.get("button", "left") != button)
        or (count is not None and a.get("count", 1) != count)
        for a in actions
    ):
        return verdict("failed", "wrong_variant")
    conditions = {
        "postcondition_exists": "element_exists",
        "postcondition_attribute": "attribute_equals",
        "postcondition_text": "text_appears",
    }
    if case in conditions and actual.get("postcondition") != conditions[case]:
        return verdict("failed", "wrong_postcondition")
    if case == "denied":
        call_id = actual.get("call_id")
        decisions = result.get("approval_decisions", ())
        diagnostics = [d for d in result.get("tool_diagnostics", ()) if d.get("call_id") == call_id]
        good = (
            result.get("sdk_input_entries") == 0
            and result.get("approval_count") == 1
            and effects.get("unchanged") is True
            and bool(call_id)
            and len(decisions) == 1
            and decisions[0].get("call_id") == call_id
            and decisions[0].get("approved") is False
            and bool(diagnostics)
            and all(
                d.get("envelope", {}).get("ok") is False
                and d.get("envelope", {}).get("error", {}).get("code") == "approval_rejected"
                for d in diagnostics
            )
        )
        return verdict("passed" if good else "failed", "denial_effect")
    native_entries = result.get("sdk_input_entries")
    if type(native_entries) is not int or native_entries < 0:
        return verdict("failed", "native_entry_count_invalid")
    if native_entries > 1:
        return verdict("failed", "native_entry_count")
    if native_entries == 0:
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
    # An unexecuted attempt has no new effect to export. Only an entered action
    # needs a fresh snapshot before its independent effects can be credited.
    if snapshot_required and (
        snapshot.get("revision_advanced") is not True
        or snapshot["revision_after"] <= snapshot["revision_before"]
    ):
        return verdict("failed", "fixture_snapshot_invalid")
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
    if case in conditions:
        # History includes unexecuted stale attempts and deduplicates equal
        # outcomes. Only the final call's linked result proves its predicate.
        outcome = final_action_outcome(actual, result.get("tool_diagnostics", ()))
        if outcome is None:
            return verdict("failed", "final_action_outcome_invalid")
        if case == "postcondition_attribute" and outcome.get("postcondition") == "not_checked":
            if not good:
                return verdict("failed", "independent_effect")
            return verdict("unsupported", "sdk_exact_attribute_readback_unavailable")
        good = (
            good
            and actual.get("postcondition") == conditions[case]
            and outcome.get("postcondition") == "passed"
        )
    return verdict("passed" if good else "failed", "independent_effect")
