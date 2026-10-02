"""Independent text fixture proof and non-upgrading component acceptance gate."""

import json
import runpy

import pytest

from morrow.core.computer_use import ComputerUseContractError


@pytest.fixture
def module():
    return runpy.run_path("evals/computer_use/native_text.py")


def test_text_oracle_keeps_secure_bytes_out_and_rejects_invalid_state(module, tmp_path):
    counter = runpy.run_path("evals/computer_use/native_counter.py")["counter_oracle"]
    path = tmp_path / "state.json"
    state = {
        "schemaVersion": 1,
        "instanceId": "894E9406-4F2E-45F9-B735-69DCF87232A1",
        "pid": 10,
        "window": {"number": 20},
        "count": 2,
        "text": "controlled 中文🧭",
        "secureFieldPopulated": True,
        "scrollOffset": 200,
    }
    path.write_text(json.dumps(state))
    value = module["text_oracle"](path, counter)
    assert value["text"] == state["text"] and value["secure"] is True
    state["secureFieldPopulated"] = "private value"
    path.write_text(json.dumps(state))
    with pytest.raises(ComputerUseContractError, match="fixture_text_state_invalid"):
        module["text_oracle"](path, counter)


@pytest.mark.parametrize("changed", ["count", "instance", "window_id"])
def test_concurrent_identity_or_counter_change_refuses_success(module, changed):
    validate = runpy.run_path("evals/computer_use/native_counter.py")["validate_counter_identity"]
    before = {
        "pid": 10,
        "window_id": 20,
        "instance": "same",
        "count": 2,
        "text": "before",
        "secure": True,
        "scroll": 200,
    }
    after = {**before, "text": "before new 中文🧭", changed: "different"}
    with pytest.raises(ComputerUseContractError):
        module["independent_insert"](before, after, "new 中文🧭", validate)


@pytest.mark.parametrize("status", ["unknown", "not_started", "completed"])
def test_independent_effect_never_upgrades_unknown_or_permits_retry(module, status):
    result = {
        "action": {"status": status, "error_code": None, "delivery": "background"},
        "sdk_input_entries": 1,
        "sdk_fresh_snapshot": True,
        "fresh_observation": True,
        "independent_insert": True,
        "secure_population_unchanged": True,
    }
    assert module["input_gate_passed"](result) is (status == "completed")
    result["sdk_input_entries"] = 2
    assert module["input_gate_passed"](result) is False


def test_stale_snapshot_or_missing_fixture_effect_is_not_success(module):
    result = {
        "action": {"status": "completed", "error_code": None, "delivery": "background"},
        "sdk_input_entries": 1,
        "sdk_fresh_snapshot": True,
        "fresh_observation": True,
        "independent_insert": True,
        "secure_population_unchanged": True,
    }
    for key in [
        "sdk_fresh_snapshot",
        "fresh_observation",
        "independent_insert",
        "secure_population_unchanged",
    ]:
        assert module["input_gate_passed"]({**result, key: False}) is False


def test_keyboard_modes_are_fixed_and_do_not_admit_arbitrary_tools(module):
    ref = "celem_controlled"
    assert module["keyboard_action"]("press_key", ref, "ignored").key == "z"
    assert module["keyboard_action"]("hotkey", ref, "ignored").keys == ("shift", "x")
    assert module["keyboard_action"]("type_text", ref, "中文🧭").text == "中文🧭"
    with pytest.raises(ComputerUseContractError, match="fixture_action_invalid"):
        module["keyboard_action"]("clipboard", ref, "ignored")
    with pytest.raises(ComputerUseContractError, match="fixture_action_invalid"):
        module["keyboard_action"]("press_key", ref, "ignored", marker_set="arbitrary")


def test_secure_input_oracle_uses_population_boolean_without_reading_secret_bytes(module):
    before = {"secure": False, "text": "ordinary", "scroll": 0}
    after = {"secure": True, "text": "ordinary", "scroll": 0}

    def validate(*args, **kwargs):
        pass

    assert module["independent_secure_input"](before, after, validate)
    assert not module["independent_secure_input"](before, {**after, "text": "changed"}, validate)
    assert not module["independent_secure_input"](after, after, validate)
