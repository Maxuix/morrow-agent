"""Opt-in one guarded keyboard insert into the independent controlled fixture.

Never retries or upgrades unknown outcomes. No raw input/capture/SDK diagnostics
are printed; this is a component gate, not Provider or full product acceptance.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import io
import json
import math
import runpy
from pathlib import Path
from uuid import uuid4

from morrow.adapters.computer_use.images import CaptureMask, prepare_capture
from morrow.adapters.computer_use.owner import ComputerDriverOwner
from morrow.adapters.computer_use.sdk_loader import construct_run_session
from morrow.adapters.state.operational import SystemStoreClock
from morrow.core.computer_use import (
    TRUSTED_COMPUTER_USE_AUTHORITY,
    CloseRunSessionRequest,
    ComputerUseAppIdentity,
    ComputerUseContractError,
    ComputerUseDelivery,
    ComputerUseImageShare,
    ComputerUseOperation,
    ComputerUseScope,
    ComputerUseWindowBoundary,
    DiscoverRequest,
    ExecuteRequest,
    HotkeyAction,
    ObserveWindowRequest,
    OpenRunSessionRequest,
    PressKeyAction,
    TypeTextAction,
)
from morrow.core.runtime_policy import ComputerUseMode, ComputerUseSettings
from morrow.runtime.ids import RandomIdSource

FIXTURE_BUNDLE_ID = "com.morrow.ComputerUseFixture"


def text_oracle(path: Path, counter) -> dict:
    identity = counter(path)
    try:
        with path.open("rb") as stream:
            content = stream.read(64 * 1024 + 1)
        state = json.loads(content)
        text, secure, scroll = state["text"], state["secureFieldPopulated"], state["scrollOffset"]
        if (
            len(content) > 64 * 1024
            or hashlib.sha256(content).hexdigest() != identity["sha256"]
            or not isinstance(text, str)
            or len(text) > 4096
            or type(secure) is not bool
            or type(scroll) not in (int, float)
            or not math.isfinite(scroll)
        ):
            raise ValueError
    except (OSError, ValueError, TypeError, KeyError):
        raise ComputerUseContractError("fixture_text_state_invalid") from None
    return {**identity, "text": text, "secure": secure, "scroll": scroll}


def independent_insert(before: dict, after: dict, text: str, validate_identity) -> bool:
    validate_identity(before, after, unchanged=True)
    return (
        after["text"] != before["text"]
        and text not in before["text"]
        and after["text"].count(text) == 1
        and before["secure"] == after["secure"]
        and before["scroll"] == after["scroll"]
    )


def masked_capture(observed) -> dict:
    from PIL import Image

    if observed.capture is None or observed.image_error is not None:
        raise ComputerUseContractError("fixture_image_unconfirmed")
    masks = tuple(
        CaptureMask(region.left, region.top, region.right, region.bottom)
        for region in observed.sensitive_regions
    )
    if len(masks) != 1:
        raise ComputerUseContractError("fixture_secure_mask_unconfirmed")
    masked = prepare_capture(observed.capture, masks=masks)
    with Image.open(io.BytesIO(masked.content)) as image:
        for mask in masks:
            if image.crop((mask.left, mask.top, mask.right, mask.bottom)).getextrema() != (
                (0, 0),
                (0, 0),
                (0, 0),
            ):
                raise ComputerUseContractError("fixture_mask_invalid")
    return {
        "sha256": hashlib.sha256(masked.content).hexdigest(),
        "mask_count": 1,
        "mask_pixels_verified": True,
    }


def input_gate_passed(result: dict) -> bool:
    action = result.get("action", {})
    return (
        action.get("status") == "completed"
        and action.get("error_code") is None
        and action.get("delivery") == "background"
        and result.get("sdk_input_entries") == 1
        and result.get("sdk_fresh_snapshot") is True
        and result.get("fresh_observation") is True
        and result.get("independent_insert") is True
        and result.get("secure_population_unchanged") is True
    )


def keyboard_marker(kind: str, text: str, marker_set: str = "initial") -> str:
    if marker_set not in ("initial", "release"):
        raise ComputerUseContractError("fixture_action_invalid")
    if kind == "type_text":
        return text
    if kind == "press_key":
        return "z" if marker_set == "initial" else "q"
    if kind == "hotkey":
        return "X" if marker_set == "initial" else "Y"
    raise ComputerUseContractError("fixture_action_invalid")


def keyboard_action(kind: str, element_ref: str, text: str, marker_set: str = "initial"):
    marker = keyboard_marker(kind, text, marker_set)
    if kind == "type_text":
        return TypeTextAction(type="type_text", element_ref=element_ref, text=text)
    if kind == "press_key":
        return PressKeyAction(type="press_key", element_ref=element_ref, key=marker)
    if kind == "hotkey":
        return HotkeyAction(type="hotkey", element_ref=element_ref, keys=("shift", marker.lower()))
    raise ComputerUseContractError("fixture_action_invalid")


async def insert_once(
    path: Path,
    *,
    sdk,
    prototype_sha256: str,
    action_type: str = "type_text",
    keyboard_marker_set: str = "initial",
) -> dict:
    readonly = runpy.run_path(str(Path(__file__).with_name("native_readonly.py")))
    counter = runpy.run_path(str(Path(__file__).with_name("native_counter.py")))
    before = text_oracle(path, counter["counter_oracle"])
    if action_type not in {"type_text", "press_key", "hotkey"}:
        raise ComputerUseContractError("fixture_action_invalid")
    text = keyboard_marker(
        action_type, "Morrow-guard-" + uuid4().hex[:8] + " 中文🧭", keyboard_marker_set
    )
    if text in before["text"]:
        raise ComputerUseContractError("fixture_marker_present")
    result = {
        "status": "failed",
        "phase": "open",
        "prototype": True,
        "prototype_dylib_sha256": prototype_sha256,
        "sdk_input_entries": 0,
        "sdk_security_entries": 0,
        "requested_action": action_type,
        "keyboard_marker_set": keyboard_marker_set,
    }
    settings = ComputerUseSettings(enabled=True, mode=ComputerUseMode.HYBRID)

    class Native(readonly["_DiagnosedSession"]):
        snapshot = None

        async def get_window_state(self, request):
            state = await super().get_window_state(request)
            snapshot = getattr(state, "snapshot_id", None)
            if self.snapshot is not None:
                result["sdk_fresh_snapshot"] = (
                    isinstance(snapshot, str) and snapshot != self.snapshot
                )
            self.snapshot = snapshot if isinstance(snapshot, str) else None
            return state

        async def call_tool(self, name, content):
            if name == "get_element_security":
                result["sdk_security_entries"] += 1
            elif name == action_type and json.loads(content).get("require_non_sensitive") is True:
                result["sdk_input_entries"] += 1
            else:
                raise ComputerUseContractError("fixture_unprotected_input")
            return await self._native.call_tool(name, content)

    owner = ComputerDriverOwner(
        sdk,
        RandomIdSource(),
        SystemStoreClock(),
        native_security=True,
        session_factory=lambda driver, name: Native(
            construct_run_session(sdk, driver, name), result
        ),
    )
    scope = ComputerUseScope(
        generation=1,
        workspace_id="ws_native_text",
        task_run_id="task_native_text",
        agent_run_id="arun_native_text",
        apps=(ComputerUseAppIdentity(bundle_id=FIXTURE_BUNDLE_ID),),
        window_boundary=ComputerUseWindowBoundary.WINDOW,
        operations=(ComputerUseOperation.OBSERVE, ComputerUseOperation.ACTION),
        delivery=ComputerUseDelivery.BACKGROUND,
        image_share=ComputerUseImageShare.CONTROLLED_WINDOW,
    )
    try:
        run = await owner.open_run_session(
            OpenRunSessionRequest(
                authority=TRUSTED_COMPUTER_USE_AUTHORITY,
                agent_run_id=scope.agent_run_id,
                scope=scope,
            )
        )
        session = owner.session_for(run)
        found = await session.discover(
            DiscoverRequest(
                authority=TRUSTED_COMPUTER_USE_AUTHORITY,
                scope=scope,
                run_session_id=run.run_session_id,
                bundle_id=FIXTURE_BUNDLE_ID,
            )
        )
        targets = readonly["select_fixture_targets"](
            session, found.targets, (before["pid"], before["window_id"])
        )
        if len(targets) != 1:
            raise ComputerUseContractError("fixture_window_required")
        observation_request = ObserveWindowRequest(
            authority=TRUSTED_COMPUTER_USE_AUTHORITY,
            scope=scope,
            target=targets[0],
            delivery=scope.delivery,
            include_image=True,
        )
        result["phase"] = "observe_before"
        observed = await session.observe(observation_request, settings=settings)
        result["before_masked_capture"] = masked_capture(observed)
        fields = [
            element
            for element in observed.observation.elements
            if element.role in {"axtextfield", "axtextarea"} and not element.sensitive
        ]
        if len(fields) != 1:
            raise ComputerUseContractError("fixture_normal_input_required")

        def authority():
            current = text_oracle(path, counter["counter_oracle"])
            counter["validate_counter_identity"](before, current, unchanged=True)
            if current["sha256"] != before["sha256"]:
                raise ComputerUseContractError("fixture_state_changed")

        result["phase"] = action_type
        outcome = await session.execute_one(
            ExecuteRequest(
                authority=TRUSTED_COMPUTER_USE_AUTHORITY,
                scope=scope,
                target=targets[0],
                observation=observed.observation,
                action=keyboard_action(
                    action_type, fields[0].element_ref, text, keyboard_marker_set
                ),
                delivery=scope.delivery,
            ),
            settings=settings,
            authority=authority,
        )
        result["action"] = {
            "status": outcome.status,
            "error_code": outcome.error_code,
            "delivery": outcome.delivery.value if outcome.delivery else None,
        }
        result["phase"] = "observe_after"
        after_observation = await session.observe(observation_request, settings=settings)
        result["after_masked_capture"] = masked_capture(after_observation)
        after = text_oracle(path, counter["counter_oracle"])
        result.update(
            independent_insert=independent_insert(
                before, after, text, counter["validate_counter_identity"]
            ),
            fresh_observation=observed.observation.observation_id
            != after_observation.observation.observation_id,
            before_state_sha256=before["sha256"],
            after_state_sha256=after["sha256"],
            input_sha256=hashlib.sha256(text.encode()).hexdigest(),
            secure_population_unchanged=before["secure"] == after["secure"],
        )
        if not input_gate_passed(result):
            raise ComputerUseContractError("fixture_input_unverified")
        await owner.close_run_session(
            CloseRunSessionRequest(
                authority=TRUSTED_COMPUTER_USE_AUTHORITY, run_session_id=run.run_session_id
            )
        )
        result.update(status="passed", reason=None, phase="closed")
    except ComputerUseContractError as exc:
        result.update(status="failed", reason=exc.code)
    except Exception as exc:
        result.update(status="failed", reason="driver_error", exception_type=type(exc).__name__)
    finally:
        try:
            await owner.shutdown()
        except Exception:
            result.update(status="failed", reason="shutdown_failed")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-desktop", action="store_true", required=True)
    parser.add_argument("--allow-one-text-insert", action="store_true")
    parser.add_argument("--allow-one-key", action="store_true")
    parser.add_argument("--keyboard-marker-set", choices=("initial", "release"), default="initial")
    parser.add_argument(
        "--action", choices=("type_text", "press_key", "hotkey"), default="type_text"
    )
    parser.add_argument("--fixture-bundle-id", choices=(FIXTURE_BUNDLE_ID,), required=True)
    parser.add_argument("--fixture-state-file", type=Path, required=True)
    parser.add_argument("--prototype-package-directory", type=Path, required=True)
    parser.add_argument("--prototype-dylib-sha256", required=True)
    parser.add_argument("--evidence-file", type=Path, required=True)
    args = parser.parse_args()
    if not (args.allow_one_text_insert if args.action == "type_text" else args.allow_one_key):
        parser.error("explicit opt-in matching the selected keyboard action required")
    security = runpy.run_path(str(Path(__file__).with_name("native_security_readonly.py")))
    try:
        sdk = security["prototype_module"](
            args.prototype_package_directory, args.prototype_dylib_sha256
        )
        result = asyncio.run(
            insert_once(
                args.fixture_state_file,
                sdk=sdk,
                prototype_sha256=args.prototype_dylib_sha256,
                action_type=args.action,
                keyboard_marker_set=args.keyboard_marker_set,
            )
        )
    except ComputerUseContractError as exc:
        result = {"status": "failed", "reason": exc.code, "sdk_input_entries": 0}
    except Exception as exc:
        result = {
            "status": "failed",
            "reason": "driver_error",
            "exception_type": type(exc).__name__,
        }
    args.evidence_file.write_text(json.dumps(result, sort_keys=True) + "\n")
    print(json.dumps(result, sort_keys=True))
    if result["status"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
