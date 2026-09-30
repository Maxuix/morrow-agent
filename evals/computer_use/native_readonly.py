"""Explicit opt-in native read-only gate for the built, already-running fixture.

No click, input, clipboard, user application, or Provider network calls. Output
contains only bounded diagnostic metadata; captures and AX contents stay local
and transient. Run from the intended responsible host and preserve its evidence.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import io
import json
import platform
import sys
from dataclasses import asdict

from morrow.adapters.computer_use.diagnostics import diagnose_host
from morrow.adapters.computer_use.owner import ComputerDriverOwner
from morrow.adapters.computer_use.sdk_loader import (
    collect_host_probe,
    construct_run_session,
    load_sdk,
)
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
    ObserveWindowRequest,
    OpenRunSessionRequest,
)
from morrow.core.runtime_policy import ComputerUseMode, ComputerUseSettings
from morrow.runtime.ids import RandomIdSource

FIXTURE_BUNDLE_ID = "com.morrow.ComputerUseFixture"


class _DiagnosedSession:
    """Inspect bounded typed metadata without persisting SDK content or errors."""

    def __init__(self, native, evidence: dict) -> None:
        self._native = native
        self._evidence = evidence

    def __getattr__(self, name):
        return getattr(self._native, name)

    async def get_window_state(self, request):
        state = await self._native.get_window_state(request)
        reason = getattr(state, "degraded_reason", None) or ""
        reason_code = next(
            (code for code in ("ax_window_unresolved", "ax_tree_empty") if reason.startswith(code)),
            "degraded" if state.degraded else None,
        )
        self._evidence["window_read"] = {
            "ax_element_count": len(state.elements or ()),
            "ax_complete": state.elements_complete,
            "truncated": state.truncated,
            "degraded": state.degraded,
            "reason": reason_code,
            "image_count": len(state.images),
            "frame_valid": state.screenshot_frame_valid,
        }
        return state


async def inspect_fixture() -> dict:
    settings = ComputerUseSettings(enabled=True, mode=ComputerUseMode.HYBRID)
    probe = collect_host_probe()
    diagnostic = diagnose_host(settings, probe, images_required=True)
    result = {
        "schema_version": 1,
        "platform": platform.platform(),
        "cpu": platform.machine(),
        "python": platform.python_version(),
        "host_executable": sys.executable,
        "probe": asdict(probe),
        "status": "failed",
        "reason": diagnostic.reason,
    }
    if diagnostic.reason != "driver_not_activated":
        return result
    sdk = load_sdk()
    owner = ComputerDriverOwner(
        sdk,
        RandomIdSource(),
        SystemStoreClock(),
        session_factory=lambda driver, name: _DiagnosedSession(
            construct_run_session(sdk, driver, name), result
        ),
    )
    scope = ComputerUseScope(
        generation=1,
        workspace_id="ws_native_fixture",
        task_run_id="task_native_fixture",
        agent_run_id="arun_native_fixture",
        apps=(ComputerUseAppIdentity(bundle_id=FIXTURE_BUNDLE_ID),),
        window_boundary=ComputerUseWindowBoundary.WINDOW,
        operations=(ComputerUseOperation.OBSERVE,),
        delivery=ComputerUseDelivery.BACKGROUND,
        image_share=ComputerUseImageShare.CONTROLLED_WINDOW,
    )
    phase = "open_session"
    try:
        run = await owner.open_run_session(
            OpenRunSessionRequest(
                authority=TRUSTED_COMPUTER_USE_AUTHORITY,
                agent_run_id=scope.agent_run_id,
                scope=scope,
            )
        )
        session = owner.session_for(run)
        phase = "discover"
        found = await session.discover(
            DiscoverRequest(
                authority=TRUSTED_COMPUTER_USE_AUTHORITY,
                scope=scope,
                run_session_id=run.run_session_id,
                bundle_id=FIXTURE_BUNDLE_ID,
            )
        )
        if len(found.targets) != 1:
            raise ComputerUseContractError("fixture_window_required")
        phase = "observe"
        observed = await session.observe(
            ObserveWindowRequest(
                authority=TRUSTED_COMPUTER_USE_AUTHORITY,
                scope=scope,
                target=found.targets[0],
                delivery=scope.delivery,
                include_image=True,
            ),
            settings=settings,
        )
        if observed.capture is None:
            raise ComputerUseContractError(observed.image_error or "image_missing")
        if not observed.observation.elements:
            raise ComputerUseContractError("fixture_ax_missing")
        phase = "decode_image"
        from PIL import Image

        with Image.open(
            io.BytesIO(observed.capture.content), formats=["PNG", "JPEG", "WEBP"]
        ) as image:
            if image.size != (observed.capture.width, observed.capture.height):
                raise ComputerUseContractError("image_dimensions")
            image.load()
        result.update(
            {
                "status": "passed",
                "reason": None,
                "ax_element_count": len(observed.observation.elements),
                "ax_complete": observed.observation.complete,
                "capture": {
                    "mime": observed.capture.mime,
                    "width": observed.capture.width,
                    "height": observed.capture.height,
                    "byte_size": len(observed.capture.content),
                    "sha256": hashlib.sha256(observed.capture.content).hexdigest(),
                },
            }
        )
        phase = "close_session"
        await owner.close_run_session(
            CloseRunSessionRequest(
                authority=TRUSTED_COMPUTER_USE_AUTHORITY,
                run_session_id=run.run_session_id,
            )
        )
    except ComputerUseContractError as exc:
        result.update(status="failed", reason=exc.code, phase=phase)
    except Exception as exc:
        result.update(
            status="failed",
            reason="driver_error",
            phase=phase,
            exception_type=type(exc).__name__,
        )
    finally:
        try:
            await owner.shutdown()
        except Exception:
            result.update(status="failed", reason="shutdown_failed")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-desktop", action="store_true", required=True)
    parser.add_argument("--fixture-bundle-id", choices=[FIXTURE_BUNDLE_ID], required=True)
    args = parser.parse_args()
    if not args.allow_desktop:
        parser.error("explicit desktop opt-in required")
    try:
        result = asyncio.run(inspect_fixture())
    except Exception as exc:
        result = {
            "status": "failed",
            "reason": "driver_error",
            "phase": "preflight_or_driver_construction",
            "exception_type": type(exc).__name__,
        }
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    if result["status"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
