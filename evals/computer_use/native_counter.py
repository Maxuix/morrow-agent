"""Explicit one-increment fixture gate; never retries, types, or opens user apps.

This tests the real typed adapter's token click and fresh observation against
the fixture's independent counter. It is not full product/action acceptance.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import runpy
from pathlib import Path
from uuid import UUID

from morrow.adapters.computer_use.diagnostics import diagnose_host
from morrow.adapters.computer_use.images import prepare_capture
from morrow.adapters.computer_use.owner import ComputerDriverOwner
from morrow.adapters.computer_use.sdk_loader import (
    collect_host_probe,
    construct_run_session,
    load_sdk,
)
from morrow.adapters.state.operational import SystemStoreClock
from morrow.core.computer_admission import admit_discover, admit_execute, admit_observe
from morrow.core.computer_use import (
    TRUSTED_COMPUTER_USE_AUTHORITY,
    ClickAction,
    CloseRunSessionRequest,
    ComputerUseAppIdentity,
    ComputerUseContractError,
    ComputerUseDelivery,
    ComputerUseImageShare,
    ComputerUseOperation,
    ComputerUseWindowBoundary,
    DiscoverRequest,
    ExecuteRequest,
    ObserveWindowRequest,
)
from morrow.core.runtime_policy import ComputerUseMode, ComputerUseSettings
from morrow.runtime.ids import RandomIdSource

FIXTURE_BUNDLE_ID = "com.morrow.ComputerUseFixture"


def counter_oracle(path: Path) -> dict:
    try:
        with path.open("rb") as stream:
            content = stream.read(64 * 1024 + 1)
        if len(content) > 64 * 1024:
            raise ValueError
        state = json.loads(content)
        pid, number, count = state["pid"], state["window"]["number"], state["count"]
        instance = str(UUID(state["instanceId"]))
        if (
            type(state["schemaVersion"]) is not int
            or state["schemaVersion"] not in (1, 2)
            or type(pid) is not int
            or not 0 < pid < 2**31
            or type(number) is not int
            or not 0 < number < 2**32
            or type(count) is not int
            or not 0 <= count < 2**31
        ):
            raise ValueError
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        raise ComputerUseContractError("fixture_state_invalid") from None
    return {
        "pid": pid,
        "window_id": number,
        "instance": instance,
        "count": count,
        "sha256": hashlib.sha256(content).hexdigest(),
    }


def validate_counter_identity(before: dict, after: dict, *, unchanged: bool) -> None:
    if any(before[key] != after[key] for key in ("pid", "window_id", "instance")):
        raise ComputerUseContractError("fixture_instance_changed")
    if unchanged and before["count"] != after["count"]:
        raise ComputerUseContractError("fixture_counter_changed")


async def increment_once(path: Path, *, delivery: ComputerUseDelivery) -> dict:
    read = runpy.run_path(str(Path(__file__).with_name("native_readonly.py")))
    baseline = counter_oracle(path)
    result = {"status": "failed", "phase": "open", "sdk_click_entries": 0}
    settings = ComputerUseSettings(enabled=True, mode=ComputerUseMode.HYBRID)
    diagnostic = diagnose_host(settings, collect_host_probe(), images_required=True)
    if diagnostic.reason != "driver_not_activated":
        raise ComputerUseContractError(diagnostic.reason)
    sdk = load_sdk()

    class Native(read["_DiagnosedSession"]):
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

        async def click(self, request):
            result["sdk_click_entries"] += 1
            return await self._native.click(request)

    owner = ComputerDriverOwner(
        sdk,
        RandomIdSource(),
        SystemStoreClock(),
        session_factory=lambda driver, name, settings: Native(
            construct_run_session(sdk, driver, name), result
        ),
    )
    try:
        run, scope = await read["open_fixture_run"](
            owner,
            settings,
            (baseline["pid"], baseline["window_id"]),
            generation=1,
            workspace_id="ws_native_counter",
            task_run_id="task_native_counter",
            agent_run_id="arun_native_counter",
            apps=(ComputerUseAppIdentity(bundle_id=FIXTURE_BUNDLE_ID),),
            window_boundary=ComputerUseWindowBoundary.WINDOW,
            operations=(ComputerUseOperation.OBSERVE, ComputerUseOperation.ACTION),
            delivery=delivery,
            image_share=ComputerUseImageShare.CONTROLLED_WINDOW,
        )
        session = owner.session_for(run)
        found = await session.discover(
            admit_discover(
                DiscoverRequest(
                    authority=TRUSTED_COMPUTER_USE_AUTHORITY,
                    scope=scope,
                    run_session_id=run.run_session_id,
                    bundle_id=FIXTURE_BUNDLE_ID,
                )
            )
        )
        targets = read["select_fixture_targets"](
            session, found.targets, (baseline["pid"], baseline["window_id"])
        )
        if len(targets) != 1:
            raise ComputerUseContractError("fixture_window_required")
        request = ObserveWindowRequest(
            authority=TRUSTED_COMPUTER_USE_AUTHORITY,
            scope=scope,
            target=targets[0],
            delivery=delivery,
            include_image=True,
        )
        result["phase"] = "observe_before"
        before = await session.observe(admit_observe(request, settings=settings))
        if before.capture is None or before.image_error is not None or before.observation.truncated:
            raise ComputerUseContractError("fixture_image_unconfirmed")
        prepared = prepare_capture(before.capture)
        result["before_capture_sha256"] = hashlib.sha256(prepared.content).hexdigest()
        # Select one positively observed token; do not claim whole-tree uniqueness/completeness.
        buttons = [
            element
            for element in before.observation.elements
            if element.role == "axbutton" and element.label == "Increment"
        ]
        if len(buttons) != 1:
            raise ComputerUseContractError("fixture_counter_target_required")

        def authority():
            validate_counter_identity(baseline, counter_oracle(path), unchanged=True)

        result["phase"] = "click"
        outcome = await session.execute_one(
            admit_execute(
                ExecuteRequest(
                    authority=TRUSTED_COMPUTER_USE_AUTHORITY,
                    scope=scope,
                    target=targets[0],
                    observation=before.observation,
                    action=ClickAction(type="click", element_ref=buttons[0].element_ref),
                    delivery=delivery,
                ),
                settings=settings,
            ),
            authority=authority,
        )
        result["action"] = {
            "status": outcome.status,
            "error_code": outcome.error_code,
            "delivery": outcome.delivery.value if outcome.delivery else None,
        }
        result["phase"] = "observe_after"
        after = await session.observe(admit_observe(request, settings=settings))
        final = counter_oracle(path)
        validate_counter_identity(baseline, final, unchanged=False)
        result.update(
            before_count=baseline["count"],
            after_count=final["count"],
            before_state_sha256=baseline["sha256"],
            after_state_sha256=final["sha256"],
            fresh_observation=after.observation.observation_id != before.observation.observation_id,
            after_image_error=after.image_error,
            independent_increment=final["count"] == baseline["count"] + 1,
        )
        if after.capture is None or after.image_error is not None:
            raise ComputerUseContractError("fixture_after_image_unconfirmed")
        prepared_after = prepare_capture(after.capture)
        result["after_capture_sha256"] = hashlib.sha256(prepared_after.content).hexdigest()
        if (
            outcome.status != "completed"
            or outcome.error_code is not None
            or final["count"] != baseline["count"] + 1
            or after.image_error is not None
            or result.get("sdk_fresh_snapshot") is not True
            or result["fresh_observation"] is not True
        ):
            raise ComputerUseContractError("fixture_effect_unverified")
        result.update(status="passed", reason=None)
        await owner.close_run_session(
            CloseRunSessionRequest(
                authority=TRUSTED_COMPUTER_USE_AUTHORITY,
                run_session_id=run.run_session_id,
            )
        )
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
    parser.add_argument("--allow-one-increment", action="store_true", required=True)
    parser.add_argument("--fixture-bundle-id", choices=(FIXTURE_BUNDLE_ID,), required=True)
    parser.add_argument("--fixture-state-file", type=Path, required=True)
    parser.add_argument("--delivery", choices=("background", "foreground"), required=True)
    args = parser.parse_args()
    try:
        result = asyncio.run(
            increment_once(args.fixture_state_file, delivery=ComputerUseDelivery(args.delivery))
        )
    except ComputerUseContractError as exc:
        result = {"status": "failed", "reason": exc.code, "sdk_click_entries": 0}
    except Exception as exc:
        result = {
            "status": "failed",
            "reason": "driver_error",
            "exception_type": type(exc).__name__,
        }
    print(json.dumps(result, sort_keys=True))
    if result["status"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
