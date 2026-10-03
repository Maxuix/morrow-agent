"""SDK-agnostic computer-use contracts. These tests never construct a driver."""

from __future__ import annotations

import importlib.abc
import sys
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from morrow.adapters.computer_use import DRIVER_CONSTRUCTION_COUNT, preflight, sdk_spec_present
from morrow.core.computer_admission import admit_discover, admit_execute, admit_observe
from morrow.core.computer_use import (
    OBSERVATION_TOOL_EXECUTION_PREFIX,
    TRUSTED_COMPUTER_USE_AUTHORITY,
    AxElement,
    ComputerUseAppIdentity,
    ComputerUseContractError,
    ComputerUseDelivery,
    ComputerUseImageShare,
    ComputerUseOperation,
    ComputerUseScope,
    ComputerUseWindowBoundary,
    ComputerUseWindowIdentity,
    CoordinateFrame,
    DiscoverRequest,
    ExecuteRequest,
    Observation,
    ObserveWindowRequest,
    TargetRef,
    TransientCapture,
    decode_computer_use_scope,
    images_allowed,
    map_image_point,
    parse_computer_action,
    preflight_computer_use,
    prepare_execute_request,
    reject_scope_expansion,
    restrict_scope,
    target_authority_key,
)
from morrow.core.domain import sha256_digest
from morrow.core.execution import PRODUCTION_TOOL_NAMES, TOOL_EXECUTION_ID_PREFIX
from morrow.core.models import SECRET_NEEDLES
from morrow.core.permissions import COMPUTER_USE_HOST_WARNING
from morrow.core.runtime_policy import (
    ComputerUseMode,
    ComputerUseSettings,
    RuntimePolicyOverrides,
)
from morrow.runtime.policy import load_runtime_policy, resolve_computer_use_settings

NOW = datetime(2026, 1, 1, tzinfo=UTC)
DIGEST = sha256_digest("frame")


def _scope(**overrides) -> ComputerUseScope:
    values = {
        "generation": 1,
        "workspace_id": "ws_1",
        "task_run_id": "task_1",
        "agent_run_id": "arun_1",
        "apps": (
            ComputerUseAppIdentity(bundle_id="com.example.Notes"),
            ComputerUseAppIdentity(bundle_id="com.example.Calendar"),
        ),
        "window_boundary": ComputerUseWindowBoundary.WINDOW,
        "operations": (ComputerUseOperation.ACTION, ComputerUseOperation.OBSERVE),
        "delivery": ComputerUseDelivery.FOREGROUND,
        "image_share": ComputerUseImageShare.NONE,
    }
    values.update(overrides)
    return decode_computer_use_scope({"schema_version": 1, **values})


def _selected_windows(*identities: tuple[str, str]) -> tuple[ComputerUseWindowIdentity, ...]:
    windows = tuple(
        ComputerUseWindowIdentity(
            app=ComputerUseAppIdentity(bundle_id=bundle_id),
            window_identity=window_identity,
        )
        for bundle_id, window_identity in identities
    )
    return tuple(sorted(windows, key=lambda item: item.window_identity))


def _selected_scope(**overrides) -> ComputerUseScope:
    windows = overrides.pop(
        "windows",
        _selected_windows(
            ("com.example.Calendar", "cwin_cal"),
            ("com.example.Notes", "cwin_1"),
        ),
    )
    return _scope(schema_version=2, windows=windows, **overrides)


def _target(**overrides) -> TargetRef:
    values = {
        "target_ref": "ctarget_1",
        "agent_run_id": "arun_1",
        "generation": 1,
        "app": ComputerUseAppIdentity(bundle_id="com.example.Notes"),
        "process_identity": "cproc_1",
        "window_identity": "cwin_1",
        "display_label": "Notes",
    }
    values.update(overrides)
    return TargetRef(**values)


def _frame(**overrides) -> CoordinateFrame:
    values = {
        "width": 100,
        "height": 80,
        "scale_x": 2.0,
        "scale_y": 2.0,
        "crop_width": 100,
        "crop_height": 80,
    }
    values.update(overrides)
    return CoordinateFrame(**values)


def _observation(**overrides) -> Observation:
    target = _target()
    values = {
        "observation_id": "cobs_1",
        "target_ref": target.target_ref,
        "agent_run_id": target.agent_run_id,
        "generation": target.generation,
        "bundle_id": target.app.bundle_id,
        "process_identity": target.process_identity,
        "window_identity": target.window_identity,
        "capture_digest": DIGEST,
        "captured_at": NOW,
        "frame": _frame(),
        "elements": (
            AxElement(element_ref="celem_1", depth=1, role="button", label="Save"),
            AxElement(element_ref="celem_2", depth=1, role="axsecuretextfield"),
        ),
    }
    values.update(overrides)
    return Observation(**values)


def _execute(action: dict, **overrides) -> ExecuteRequest:
    values = {
        "authority": TRUSTED_COMPUTER_USE_AUTHORITY,
        "scope": _selected_scope(),
        "target": _target(),
        "observation": _observation(),
        "action": parse_computer_action(action),
        "delivery": ComputerUseDelivery.FOREGROUND,
    }
    values.update(overrides)
    return ExecuteRequest(**values)


def test_preflight_stays_unavailable_and_settings_only_lower_budgets():
    assert preflight_computer_use().reason == "disabled"
    assert preflight_computer_use(ComputerUseSettings(enabled=True)).reason == "sdk_missing"
    activated = preflight_computer_use(ComputerUseSettings(enabled=True), spec_present=True)
    assert activated.status == "unavailable"
    assert activated.reason == "driver_not_activated"
    assert resolve_computer_use_settings(None).enabled is False
    assert RuntimePolicyOverrides().computer_use is None
    reloaded = RuntimePolicyOverrides.model_validate(
        RuntimePolicyOverrides().model_dump(mode="json")
    )
    assert reloaded.computer_use is None
    lowered = ComputerUseSettings(enabled=True, max_operations=50, max_call_seconds=10)
    assert lowered.max_operations == 50
    for overrides in (
        {"max_operations": 101},
        {"max_run_seconds": 601},
        {"max_call_seconds": 61},
        {"max_observation_bytes": 64 * 1024 * 1024 + 1},
        {"image_long_edge_px": 1921},
    ):
        with pytest.raises(ValidationError):
            ComputerUseSettings(**overrides)
    load_runtime_policy()
    enabled = preflight(ComputerUseSettings(enabled=True))
    assert enabled.status == "unavailable"
    assert enabled.reason == ("driver_not_activated" if sdk_spec_present() else "sdk_missing")
    assert DRIVER_CONSTRUCTION_COUNT == 0
    assert not any(needle in COMPUTER_USE_HOST_WARNING.casefold() for needle in SECRET_NEEDLES)


def test_scope_canonicalizes_and_refuses_expansion():
    scope = _scope()
    assert tuple(item.bundle_id for item in scope.apps) == (
        "com.example.Calendar",
        "com.example.Notes",
    )
    assert scope.operations == (ComputerUseOperation.OBSERVE, ComputerUseOperation.ACTION)
    reloaded = decode_computer_use_scope(scope.model_dump(mode="json"))
    assert reloaded == scope
    narrowed = restrict_scope(scope, operations=(ComputerUseOperation.OBSERVE,))
    assert narrowed.operations == (ComputerUseOperation.OBSERVE,)
    with pytest.raises(ComputerUseContractError) as expanded:
        restrict_scope(
            narrowed, operations=(ComputerUseOperation.OBSERVE, ComputerUseOperation.ACTION)
        )
    assert expanded.value.code == "scope_expansion"
    with pytest.raises(ComputerUseContractError):
        reject_scope_expansion(scope, scope.model_copy(update={"generation": 2}))
    with pytest.raises(ValidationError):
        _scope(
            window_boundary=ComputerUseWindowBoundary.EXPLICIT_DESKTOP_DIAGNOSTIC,
            operations=(ComputerUseOperation.ACTION,),
        )
    with pytest.raises(ValidationError):
        ComputerUseAppIdentity(bundle_id="com.example.*")
    assert images_allowed(ComputerUseSettings(), scope) is False
    shared = _scope(image_share=ComputerUseImageShare.CONTROLLED_WINDOW)
    assert images_allowed(ComputerUseSettings(enabled=True, mode=ComputerUseMode.HYBRID), shared)
    assert not images_allowed(
        ComputerUseSettings(enabled=True, mode=ComputerUseMode.SEMANTIC), shared
    )


def test_selected_window_replacement_and_version_downgrade_are_expansion():
    notes = "com.example.Notes"
    calendar = "com.example.Calendar"
    current = _selected_scope(
        windows=_selected_windows((notes, "cwin_1"), (notes, "cwin_2"), (calendar, "cwin_cal"))
    )
    replaced = decode_computer_use_scope(
        {
            **current.model_dump(mode="json"),
            "windows": [
                {"app": {"bundle_id": notes}, "window_identity": "cwin_9"},
                {"app": {"bundle_id": calendar}, "window_identity": "cwin_cal"},
            ],
        }
    )
    with pytest.raises(ComputerUseContractError) as replacement:
        reject_scope_expansion(current, replaced)
    assert replacement.value.code == "scope_expansion"
    downgraded_payload = current.model_dump(mode="json")
    downgraded_payload["schema_version"] = 1
    downgraded_payload.pop("windows", None)
    downgraded = decode_computer_use_scope(downgraded_payload)
    with pytest.raises(ComputerUseContractError) as downgrade:
        reject_scope_expansion(current, downgraded)
    assert downgrade.value.code == "scope_expansion"
    subset = decode_computer_use_scope(
        {
            **current.model_dump(mode="json"),
            "windows": [
                {"app": {"bundle_id": notes}, "window_identity": "cwin_1"},
                {"app": {"bundle_id": calendar}, "window_identity": "cwin_cal"},
            ],
        }
    )
    reject_scope_expansion(current, subset)
    legacy = _scope()
    promoted = decode_computer_use_scope(
        {
            **legacy.model_dump(mode="json"),
            "schema_version": 2,
            "windows": [
                {"app": {"bundle_id": notes}, "window_identity": "cwin_1"},
                {"app": {"bundle_id": calendar}, "window_identity": "cwin_cal"},
            ],
        }
    )
    reject_scope_expansion(legacy, promoted)
    reject_scope_expansion(current, current)


@pytest.mark.parametrize("dimensions", [(2560, 1440), (1440, 2560), (7680, 4320)])
def test_image_coordinate_frames_keep_image_dimension_limits(dimensions):
    width, height = dimensions
    with pytest.raises(ValidationError, match="image_bounds"):
        _frame(width=width, height=height, crop_width=width, crop_height=height)


def test_targets_observations_and_actions_reject_before_the_device():
    target = _target()
    labeled = target.model_copy(update={"display_label": "Renamed"})
    assert target_authority_key(target) == target_authority_key(labeled)
    assert _target(display_label="stored password").display_label == "stored password"
    capture = TransientCapture(content=b"secret-bytes-marker", mime="image/png", width=2, height=2)
    assert "secret-bytes-marker" not in repr(capture)
    assert "secret-bytes-marker" not in str(capture)
    assert map_image_point(_frame(), 10, 4) == (5.0, 2.0)
    scope = _selected_scope()
    with pytest.raises(ComputerUseContractError) as forged_app:
        admit_discover(
            DiscoverRequest(
                authority=TRUSTED_COMPUTER_USE_AUTHORITY,
                scope=scope,
                run_session_id="crun_1",
                bundle_id="com.example.Other",
            )
        )
    assert forged_app.value.code == "app_not_granted"
    granted_pair = _selected_scope(
        windows=_selected_windows(
            ("com.example.Notes", "cwin_1"),
            ("com.example.Notes", "cwin_2"),
            ("com.example.Calendar", "cwin_cal"),
        )
    )
    with pytest.raises(ComputerUseContractError) as wrong_window:
        admit_execute(
            _execute(
                {"type": "click", "element_ref": "celem_1"},
                target=_target(window_identity="cwin_2"),
                scope=granted_pair,
            )
        )
    assert wrong_window.value.code == "stale_observation"
    with pytest.raises(ComputerUseContractError) as other_run:
        admit_observe(
            ObserveWindowRequest(
                authority=TRUSTED_COMPUTER_USE_AUTHORITY,
                scope=scope,
                target=_target(agent_run_id="arun_2"),
                delivery=ComputerUseDelivery.FOREGROUND,
            ),
            settings=ComputerUseSettings(),
        )
    assert other_run.value.code == "subject_mismatch"
    with pytest.raises(ComputerUseContractError) as screenshot:
        admit_observe(
            ObserveWindowRequest(
                authority="screenshot",
                scope=scope,
                target=target,
                delivery=ComputerUseDelivery.FOREGROUND,
            ),
            settings=ComputerUseSettings(),
        )
    assert screenshot.value.code == "untrusted_authority"
    shared = _selected_scope(image_share=ComputerUseImageShare.CONTROLLED_WINDOW)
    with pytest.raises(ComputerUseContractError) as semantic:
        admit_observe(
            ObserveWindowRequest(
                authority=TRUSTED_COMPUTER_USE_AUTHORITY,
                scope=shared,
                target=target,
                delivery=ComputerUseDelivery.FOREGROUND,
                include_image=True,
            ),
            settings=ComputerUseSettings(enabled=True, mode=ComputerUseMode.SEMANTIC),
        )
    assert semantic.value.code == "images_not_allowed"
    rejections = (
        ({"type": "click", "element_ref": "celem_1", "x": 1, "y": 1}, "rejected_action"),
        ({"type": "click", "sdk_tool": "click", "element_ref": "celem_1"}, "rejected_action"),
        (
            {"type": "click", "actions": [{"type": "click"}], "element_ref": "celem_1"},
            "rejected_action",
        ),
        ({"type": "hotkey", "keys": ["ctrl", "alt", "shift", "meta", "a"]}, "rejected_action"),
        ({"type": "scroll", "direction": "down", "amount": 2001}, "rejected_action"),
        ({"type": "type_text", "text": "x" * 4097}, "rejected_action"),
    )
    for payload, code in rejections:
        with pytest.raises(ComputerUseContractError) as error:
            parse_computer_action(payload)
        assert error.value.code == code
        assert "password" not in str(error.value)
    with pytest.raises(ComputerUseContractError) as bounds:
        admit_execute(_execute({"type": "click", "x": 100, "y": 1}))
    assert bounds.value.code == "out_of_bounds"
    missing_scale = _observation(frame=CoordinateFrame(width=100, height=80))
    with pytest.raises(ComputerUseContractError) as unknown:
        admit_execute(_execute({"type": "click", "x": 1, "y": 1}, observation=missing_scale))
    assert unknown.value.code == "unknown_scale"
    prepared = prepare_execute_request(
        _execute({"type": "type_text", "text": "my password value", "element_ref": "celem_2"})
    )
    assert prepared.action.text == "my password value"
    with pytest.raises(ComputerUseContractError) as stale_ref:
        prepare_execute_request(
            _execute(
                {"type": "click", "element_ref": "celem_9"},
                observation=_observation(elements=()),
            )
        )
    assert stale_ref.value.code == "unknown_element"


def test_default_startup_does_not_construct_driver_with_computer_contracts_available(tmp_path):
    assert OBSERVATION_TOOL_EXECUTION_PREFIX == TOOL_EXECUTION_ID_PREFIX
    assert "computer_observe" in PRODUCTION_TOOL_NAMES
    assert "computer_action" in PRODUCTION_TOOL_NAMES

    class RejectCua(importlib.abc.MetaPathFinder):
        def find_spec(self, fullname, path, target=None):
            if fullname == "cua_driver" or fullname.startswith("cua_driver."):
                raise RuntimeError("native driver import")
            return None

    finder = RejectCua()
    sys.meta_path.insert(0, finder)
    try:
        import morrow.adapters.computer_use as adapter
        import morrow.core.computer_use as contracts
        from morrow.adapters.credentials.keyring import MemoryCredentialStore
        from morrow.bootstrap import build_application
        from morrow.interfaces.cli import app as cli_app

        assert contracts.preflight_computer_use().reason == "disabled"
        assert adapter.preflight().reason == "disabled"
        constructions = adapter.DRIVER_CONSTRUCTION_COUNT
        assert CliRunner().invoke(cli_app, ["--help"]).exit_code == 0
        build_application(state_root=tmp_path / "state", credentials=MemoryCredentialStore())
        assert adapter.DRIVER_CONSTRUCTION_COUNT == constructions
    finally:
        sys.meta_path.remove(finder)
