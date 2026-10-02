"""Counter gate exercises the real typed adapter with a fake SDK, never native input."""

import base64
import io
import json
import runpy
from types import SimpleNamespace

import pytest
from PIL import Image

from morrow.adapters.computer_use.owner import ComputerDriverOwner
from morrow.adapters.computer_use.process_identity import ProcessBirth
from morrow.core.computer_use import ComputerUseContractError, ComputerUseDelivery
from test_computer_use_driver import _Native, _sdk
from test_computer_use_lifecycle import _Lease


@pytest.mark.parametrize(
    "case", ["passed", "concurrent_change", "unknown", "unverifiable", "stale_snapshot"]
)
async def test_one_increment_has_independent_oracle_and_never_retries(tmp_path, monkeypatch, case):
    module = runpy.run_path("evals/computer_use/native_counter.py")
    inspect = module["increment_once"]
    values = {"pid": 4242, "window_id": 9001, "instance": "fixture", "count": 1, "sha256": "before"}

    class Native(_Native):
        reads = 0

        async def list_apps(self, request):
            result = await super().list_apps(request)
            result.apps[0].bundle_id = module["FIXTURE_BUNDLE_ID"]
            return result

        async def get_window_state(self, request):
            result = await super().get_window_state(request)
            self.reads += 1
            result.elements = result.elements[:1]
            result.elements[0].label = "Increment"
            result.elements_complete = False
            result.truncated = result.degraded = False
            result.total_element_count = result.returned_element_count = 1
            result.snapshot_id = "fixed" if case == "stale_snapshot" else f"snapshot-{self.reads}"
            image = io.BytesIO()
            Image.new("RGB", (20, 10), "red").save(image, format="PNG")
            result.images[0].data_base64 = base64.b64encode(image.getvalue()).decode()
            if case == "concurrent_change" and self.reads == 1:
                values["count"] += 1
            return result

        async def click(self, request):
            values["count"] += 1
            values["sha256"] = "after"
            result = await super().click(request)
            if case == "unknown":
                raise RuntimeError("secret native diagnostic")
            if case == "unverifiable":
                result.effect = SimpleNamespace(name="UNVERIFIABLE")
            return result

        async def shutdown(self):
            self.calls.append(("shutdown", None))

    native = Native()
    monkeypatch.setitem(inspect.__globals__, "counter_oracle", lambda _: dict(values))
    monkeypatch.setitem(inspect.__globals__, "collect_host_probe", lambda: object())
    monkeypatch.setitem(
        inspect.__globals__,
        "diagnose_host",
        lambda *_args, **_kwargs: SimpleNamespace(reason="driver_not_activated"),
    )
    monkeypatch.setitem(inspect.__globals__, "load_sdk", _sdk)
    monkeypatch.setitem(inspect.__globals__, "construct_run_session", lambda *_: native)

    def owner(sdk, ids, clock, **kwargs):
        return ComputerDriverOwner(
            sdk,
            ids,
            clock,
            driver_factory=lambda _: native,
            lease=_Lease(),
            process_reader=lambda _: ProcessBirth(1, 0),
            **kwargs,
        )

    monkeypatch.setitem(inspect.__globals__, "ComputerDriverOwner", owner)
    result = await inspect(tmp_path / "unused", delivery=ComputerUseDelivery.BACKGROUND)
    if case == "concurrent_change":
        assert result["sdk_click_entries"] == 0
        assert result["reason"] == "fixture_counter_changed"
    else:
        assert result["sdk_click_entries"] == 1
        assert result["before_count"] == 1 and result["after_count"] == 2
        assert result["independent_increment"] is True
        assert result["status"] == ("passed" if case == "passed" else "failed")
        assert result["sdk_fresh_snapshot"] is (case != "stale_snapshot")
        if case in {"unknown", "unverifiable"}:
            assert result["action"]["status"] == "unknown"
        if case == "unverifiable":
            assert result["action"]["error_code"] == "unverified_action"
    assert sum(name == "click" for name, _ in native.calls) == result["sdk_click_entries"]
    assert ("shutdown", None) in native.calls
    assert "secret" not in json.dumps(result)


def test_counter_oracle_validates_identity_without_exporting_input(tmp_path):
    module = runpy.run_path("evals/computer_use/native_counter.py")
    path = tmp_path / "state.json"
    state = {
        "schemaVersion": 1,
        "instanceId": "894e9406-4f2e-45f9-b735-69dcf87232a1",
        "pid": 2,
        "window": {"number": 3},
        "count": 4,
        "text": "private input",
    }
    path.write_text(json.dumps(state))
    before = module["counter_oracle"](path)
    assert before["count"] == 4 and "private" not in json.dumps(before)
    for field in ("pid", "count"):
        changed = dict(state, **{field: True})
        path.write_text(json.dumps(changed))
        with pytest.raises(ComputerUseContractError, match="fixture_state_invalid"):
            module["counter_oracle"](path)
    with pytest.raises(ComputerUseContractError, match="fixture_instance_changed"):
        module["validate_counter_identity"](before, dict(before, instance="new"), unchanged=False)
