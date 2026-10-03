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
import threading
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

from morrow.adapters.computer_use.diagnostics import diagnose_host
from morrow.adapters.computer_use.images import CaptureMask, prepare_capture
from morrow.adapters.computer_use.owner import ComputerDriverOwner
from morrow.adapters.computer_use.sdk_loader import (
    collect_host_probe,
    construct_run_session,
    load_sdk,
)
from morrow.adapters.state.operational import SystemStoreClock
from morrow.core.computer_admission import admit_discover, admit_observe
from morrow.core.computer_use import (
    TRUSTED_COMPUTER_USE_AUTHORITY,
    CloseRunSessionRequest,
    ComputerUseAppIdentity,
    ComputerUseContractError,
    ComputerUseDelivery,
    ComputerUseImageShare,
    ComputerUseOperation,
    ComputerUseWindowBoundary,
    DiscoverRequest,
    ObserveWindowRequest,
    OpenRunSessionRequest,
    SelectedWindowScope,
)
from morrow.core.runtime_policy import ComputerUseMode, ComputerUseSettings
from morrow.runtime.ids import RandomIdSource

FIXTURE_BUNDLE_ID = "com.morrow.ComputerUseFixture"


def read_fixture_window(path: Path | None) -> tuple[int, int] | None:
    if path is None:
        return None
    try:
        with path.open("rb") as stream:
            content = stream.read(64 * 1024 + 1)
        if len(content) > 64 * 1024:
            raise ValueError
        state = json.loads(content)
        pid, window_id = state["pid"], state["window"]["number"]
        if (
            type(state["schemaVersion"]) is not int
            or state["schemaVersion"] != 1
            or type(pid) is not int
            or not 0 < pid < 2**31
            or type(window_id) is not int
            or not 0 < window_id < 2**32
        ):
            raise ValueError
    except (OSError, ValueError, TypeError, KeyError):
        raise ComputerUseContractError("fixture_state_invalid") from None
    return pid, window_id


async def open_fixture_run(owner, settings, fixture_window, **scope_fields):
    """Select one strict fixture window, then open that v2 scope."""

    catalog = await owner.discover_local_candidates(
        settings, authority=TRUSTED_COMPUTER_USE_AUTHORITY
    )
    matches = []
    for item in catalog.candidates:
        if item.app.bundle_id != FIXTURE_BUNDLE_ID:
            continue
        record = owner._candidates.resolve(item.candidate_id)
        if fixture_window is not None and (record.pid, record.window_id) != tuple(fixture_window):
            continue
        matches.append(item)
    if len(matches) != 1:
        raise ComputerUseContractError("fixture_window_required")
    windows = owner.select_local_candidates(
        (matches[0].candidate_id,), authority=TRUSTED_COMPUTER_USE_AUTHORITY
    )
    scope = SelectedWindowScope(windows=windows, **scope_fields)
    run = await owner.open_run_session(
        OpenRunSessionRequest(
            authority=TRUSTED_COMPUTER_USE_AUTHORITY,
            agent_run_id=scope.agent_run_id,
            scope=scope,
        )
    )
    return run, scope


def select_fixture_targets(session, targets, fixture_window):
    if fixture_window is None:
        return targets
    pid, window_id = fixture_window
    return tuple(
        target
        for target in targets
        if (record := session._registry.window(target.window_identity)).pid == pid
        and record.window_id == window_id
    )


class _DiagnosedSession:
    """Inspect bounded typed metadata without persisting SDK content or errors."""

    def __init__(self, native, evidence: dict, *, native_walk_limit=None) -> None:
        self._native = native
        self._evidence = evidence
        self._native_walk_limit = native_walk_limit
        if native_walk_limit is not None and (
            type(native_walk_limit) is not int or native_walk_limit not in (200, 400)
        ):
            raise ComputerUseContractError("fixture_walk_limit_invalid")

    def __getattr__(self, name):
        return getattr(self._native, name)

    async def list_apps(self, request):
        result = await self._native.list_apps(request)
        fixtures = [app for app in (result.apps or ()) if app.bundle_id == FIXTURE_BUNDLE_ID]
        self._evidence.setdefault("discovery", {}).update(
            {
                "fixture_app_count": len(fixtures),
                "fixture_pids": [
                    app.pid
                    for app in fixtures[:100]
                    if type(app.pid) is int and 0 < app.pid < 2**31
                ],
            }
        )
        return result

    async def list_windows(self, request):
        result = await self._native.list_windows(request)
        self._evidence.setdefault("discovery", {})["window_count"] = len(result.windows or ())
        self._evidence["discovery"]["windows"] = [
            {
                "window_id": window.window_id,
                "fixture_title": window.title == "Morrow Computer Use Fixture",
            }
            for window in (result.windows or ())[:100]
            if type(window.window_id) is int and 0 < window.window_id < 2**32
        ]
        return result

    async def get_window_state(self, request):
        if self._native_walk_limit is not None:
            # Acceptance-only, bounded experiment. Model/adapter export caps remain unchanged.
            request.max_elements = self._native_walk_limit
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
            "max_returned_depth": max(
                (
                    element.depth
                    for element in (state.elements or ())
                    if type(getattr(element, "depth", None)) is int and 0 <= element.depth <= 100
                ),
                default=None,
            ),
            "truncation_reason": (
                getattr(state, "truncation_reason", None)
                if getattr(state, "truncation_reason", None) in ("timeout", "node_budget")
                else "unknown"
                if state.truncated
                else None
            ),
        }
        return state


async def inspect_fixture(*, fixture_window=None, native_walk_limit=None) -> dict:
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
        "owner_main_thread": threading.current_thread() is threading.main_thread(),
        "native_walk_limit": native_walk_limit,
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
        session_factory=lambda driver, name, settings: _DiagnosedSession(
            construct_run_session(sdk, driver, name), result, native_walk_limit=native_walk_limit
        ),
    )
    phase = "open_session"
    try:
        run, scope = await open_fixture_run(
            owner,
            settings,
            fixture_window,
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
        session = owner.session_for(run)
        phase = "discover"
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
        result.setdefault("discovery", {})["target_count"] = len(found.targets)
        selected = select_fixture_targets(session, found.targets, fixture_window)
        result["discovery"]["selected_count"] = len(selected)
        if len(selected) != 1:
            raise ComputerUseContractError("fixture_window_required")
        phase = "observe"
        observed = await session.observe(
            admit_observe(
                ObserveWindowRequest(
                    authority=TRUSTED_COMPUTER_USE_AUTHORITY,
                    scope=scope,
                    target=selected[0],
                    delivery=scope.delivery,
                    include_image=True,
                ),
                settings=settings,
            ),
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
                "image_share_error": observed.image_error,
                "capture": {
                    "mime": observed.capture.mime,
                    "width": observed.capture.width,
                    "height": observed.capture.height,
                    "byte_size": len(observed.capture.content),
                    "sha256": hashlib.sha256(observed.capture.content).hexdigest(),
                },
            }
        )
        editable = [
            element
            for element in observed.observation.elements
            if element.role in {"axtextfield", "axtextarea", "axcombobox", "axsearchfield"}
        ]
        result["editable_classification"] = {
            "non_sensitive": sum(not element.sensitive for element in editable),
            "sensitive": sum(element.sensitive for element in editable),
        }
        if observed.image_error is None:
            phase = "mask_capture"
            masks = tuple(
                CaptureMask(region.left, region.top, region.right, region.bottom)
                for region in observed.sensitive_regions
            )
            masked = prepare_capture(observed.capture, masks=masks)
            with Image.open(io.BytesIO(masked.content)) as image:
                for mask in masks:
                    pixels = image.crop((mask.left, mask.top, mask.right, mask.bottom))
                    if pixels.getextrema() != ((0, 0), (0, 0), (0, 0)):
                        raise ComputerUseContractError("fixture_mask_invalid")
                if image.info:
                    raise ComputerUseContractError("fixture_metadata_retained")
            result["masked_capture"] = {
                "mask_count": len(masks),
                "byte_size": len(masked.content),
                "sha256": hashlib.sha256(masked.content).hexdigest(),
                "mask_pixels_verified": True,
            }
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


async def inspect_on_core_host(*, fixture_window=None, native_walk_limit=None) -> dict:
    """Probe the real CoreHost owner loop, without GUI/application composition."""
    from morrow.server.host import ApprovalWaiters, CoreHost, RunSupervisor

    host = CoreHost(
        lambda: SimpleNamespace(
            supervisor=RunSupervisor(),
            approval_waiters=ApprovalWaiters(),
            computer_use=None,
            close=lambda: None,
        )
    )
    host.start()
    try:
        result = await host.execute_preparation(
            lambda: inspect_fixture(
                fixture_window=fixture_window, native_walk_limit=native_walk_limit
            )
        )
        result["host_mode"] = "core_owner_probe"
        return result
    finally:
        host.stop()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-desktop", action="store_true", required=True)
    parser.add_argument("--fixture-bundle-id", choices=[FIXTURE_BUNDLE_ID], required=True)
    parser.add_argument("--fixture-state-file", type=Path)
    parser.add_argument("--core-owner", action="store_true")
    parser.add_argument("--native-walk-limit", type=int, choices=(200, 400))
    args = parser.parse_args()
    if not args.allow_desktop:
        parser.error("explicit desktop opt-in required")
    try:
        inspect = inspect_on_core_host if args.core_owner else inspect_fixture
        result = asyncio.run(
            inspect(
                fixture_window=read_fixture_window(args.fixture_state_file),
                native_walk_limit=args.native_walk_limit,
            )
        )
        result.setdefault("host_mode", "cli_main")
    except ComputerUseContractError as exc:
        result = {"status": "failed", "reason": exc.code, "phase": "fixture_identity"}
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
