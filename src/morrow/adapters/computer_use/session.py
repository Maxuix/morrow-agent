"""Typed desktop session calls. Native process, window, and element tokens stay in the registry."""

from __future__ import annotations

import asyncio
import base64
import math
import re
import threading
from collections.abc import Callable
from typing import Any

from morrow.adapters.computer_use.calls import NativeActionInterrupted, NativeCalls
from morrow.adapters.computer_use.process_identity import ProcessBirth, read_process_birth
from morrow.adapters.computer_use.registry import TrustedDesktopRegistry, WindowGeometry
from morrow.core.computer_use import (
    MAX_AX_DEPTH,
    MAX_AX_ELEMENTS,
    MAX_AX_TEXT_BYTES,
    MAX_DISCOVERED_TARGETS,
    MAX_IMAGE_BYTES,
    MAX_IMAGE_LONG_EDGE_PX,
    MAX_IMAGE_PIXELS,
    ActionOutcome,
    AxElement,
    CloseRunSessionRequest,
    ComputerUseAppIdentity,
    ComputerUseContractError,
    ComputerUseDelivery,
    ComputerUseOperation,
    ComputerUseScope,
    CoordinateFrame,
    DiscoverRequest,
    DiscoverResult,
    Observation,
    ObservedWindow,
    ObserveWindowRequest,
    OpenRunSessionRequest,
    PreparedComputerAction,
    RunSession,
    SensitiveCaptureRegion,
    TargetRef,
    TransientCapture,
    reject_untrusted_computer_use_authority,
)
from morrow.core.domain import (
    COMPUTER_OBSERVATION_ID_PREFIX,
    COMPUTER_RUN_ID_PREFIX,
    canonical_json_bytes,
    refuse_secret_material,
    sha256_digest,
)
from morrow.core.ports import Clock, IdSource
from morrow.core.runtime_policy import ComputerUseSettings

_CODE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_MIME = {
    "image/png": "image/png",
    "image/jpeg": "image/jpeg",
    "image/jpg": "image/jpeg",
    "image/webp": "image/webp",
}


class TypedComputerSession:
    """One run's typed SDK session. It never calls a generic tool method."""

    def __init__(
        self,
        sdk: Any,
        native_session: Any,
        registry: TrustedDesktopRegistry,
        id_source: IdSource,
        clock: Clock,
        *,
        session_name: str | None = None,
        call_timeout: float = 15,
        process_reader: Callable[[int], ProcessBirth] = read_process_birth,
    ) -> None:
        self._sdk = sdk
        self._native = native_session
        self._registry = registry
        self._ids = id_source
        self._clock = clock
        self._reserved_name = session_name
        self._owner_loop: asyncio.AbstractEventLoop | None = None
        self._owner_thread: int | None = None
        self._closed = False
        self._opening = False
        self._session_name: str | None = None
        self._agent_run_id: str | None = None
        self._generation: int | None = None
        self._scope: ComputerUseScope | None = None
        self._calls = NativeCalls(self.invalidate, timeout=call_timeout)
        self._process_reader = process_reader

    async def open_run_session(self, request: OpenRunSessionRequest) -> RunSession:
        self._check_owner(bind=True)
        reject_untrusted_computer_use_authority(request.authority)
        if request.agent_run_id != request.scope.agent_run_id:
            raise ComputerUseContractError("subject_mismatch")
        if self._closed or self._session_name is not None or self._opening:
            raise ComputerUseContractError("session_not_reusable")
        self._opening = True
        run_id = self._reserved_name or self._ids.new_id(COMPUTER_RUN_ID_PREFIX)
        self._reserved_name = run_id
        try:
            await self._call(
                "start_session",
                self._sdk.StartSessionInput(
                    session=run_id,
                    capture_scope=self._sdk.CaptureScope.WINDOW,
                    cursor_theme=None,
                ),
            )
        except BaseException as exc:
            self._closed = True
            if isinstance(exc, asyncio.CancelledError):
                raise
            raise ComputerUseContractError("driver_error") from None
        finally:
            self._opening = False
        self._session_name = run_id
        self._agent_run_id = request.agent_run_id
        self._generation = request.scope.generation
        self._scope = request.scope
        return RunSession(
            run_session_id=run_id,
            agent_run_id=request.agent_run_id,
            generation=request.scope.generation,
        )

    async def close_run_session(self, request: CloseRunSessionRequest) -> None:
        self._check_owner()
        reject_untrusted_computer_use_authority(request.authority)
        name = self._session_name or self._reserved_name
        if name is None or request.run_session_id != name:
            raise ComputerUseContractError("subject_mismatch")
        self.invalidate()
        await self._calls.settle()
        failure: ComputerUseContractError | None = None
        try:
            await self._call("end_session", self._sdk.EndSessionInput(session=name), cleanup=True)
        except ComputerUseContractError as exc:
            failure = exc
        except Exception:
            failure = ComputerUseContractError("driver_error")
        await self._calls.settle()
        close = getattr(self._native, "close", None)
        if close is not None:
            try:
                close()
            except Exception:
                failure = failure or ComputerUseContractError("driver_error")
        self._session_name = None
        if failure is not None:
            raise failure

    async def discover(self, request: DiscoverRequest) -> DiscoverResult:
        reject_untrusted_computer_use_authority(request.authority)
        self._require_scope(request.scope)
        if ComputerUseOperation.OBSERVE not in request.scope.operations:
            raise ComputerUseContractError("operation_not_granted")
        if request.run_session_id != self._require_session():
            raise ComputerUseContractError("subject_mismatch")
        granted = {item.bundle_id for item in request.scope.apps}
        if request.bundle_id is not None and request.bundle_id not in granted:
            raise ComputerUseContractError("app_not_granted")
        if request.bundle_id is not None:
            granted = {request.bundle_id}
        listed = await self._call("list_apps", self._sdk.ListAppsInput())
        targets: list[TargetRef] = []
        for app in getattr(listed, "apps", ()) or ():
            bundle_id = getattr(app, "bundle_id", None)
            pid = getattr(app, "pid", None)
            if not isinstance(bundle_id, str) or bundle_id not in granted:
                continue
            if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
                continue
            if getattr(app, "running", True) is False:
                continue
            try:
                ComputerUseAppIdentity(bundle_id=bundle_id)
            except ValueError:
                continue
            birth = self._process_reader(pid)
            windows = await self._call(
                "list_windows", self._sdk.ListWindowsInput(pid=pid, on_screen_only=True)
            )
            if self._process_reader(pid) != birth:
                raise ComputerUseContractError("stale_observation")
            for window in getattr(windows, "windows", ()) or ():
                target = self._window_target(request, bundle_id, pid, window, birth)
                if target is not None:
                    targets.append(target)
                    if len(targets) > MAX_DISCOVERED_TARGETS:
                        raise ComputerUseContractError("target_budget")
        return DiscoverResult(targets=tuple(targets))

    async def observe(
        self,
        request: ObserveWindowRequest,
        *,
        settings: ComputerUseSettings | None = None,
    ) -> ObservedWindow:
        reject_untrusted_computer_use_authority(request.authority)
        self._require_scope(request.scope)
        window = self._registry.window(request.target.window_identity)
        if (
            window.agent_run_id != request.target.agent_run_id
            or window.generation != request.target.generation
            or window.bundle_id != request.target.app.bundle_id
            or window.process_identity != request.target.process_identity
            or window.target_ref != request.target.target_ref
        ):
            raise ComputerUseContractError("stale_observation")
        self._registry.retire_window_observations(window.window_identity)
        resolved = settings or ComputerUseSettings()
        geometry = await self._validate_live_target(window)
        state = await self._call(
            "get_window_state",
            self._sdk.GetWindowStateInput(
                pid=window.pid,
                window_id=window.window_id,
                session=self._require_session(),
                query=None,
                include_accessibility_tree=True,
                include_screenshot=request.include_image,
                screenshot_out_file=None,
                max_elements=MAX_AX_ELEMENTS,
                max_depth=MAX_AX_DEPTH,
                max_dimension=None,
                max_image_dimension=min(resolved.image_long_edge_px, MAX_IMAGE_LONG_EDGE_PX),
                timeout_ms=int(resolved.max_call_seconds * 1000),
            ),
        )
        if await self._validate_live_target(window) != geometry:
            raise ComputerUseContractError("stale_observation")
        return self._observation(
            request, window.window_identity, window.bundle_id, state, geometry=geometry
        )

    async def execute(
        self,
        prepared: PreparedComputerAction,
        *,
        window_identity: str,
        delivery: ComputerUseDelivery,
        agent_run_id: str,
        generation: int,
    ) -> ActionOutcome:
        self._require_subject(agent_run_id, generation)
        window = self._registry.window(window_identity)
        if window.agent_run_id != agent_run_id or window.generation != generation:
            raise ComputerUseContractError("stale_observation")
        self._validate_process(window)
        action = prepared.action
        target = self._sdk.ActionTarget.WINDOW(window.pid, window.window_id)
        session_name = self._require_session()
        try:
            if action.type == "click":
                result = await self._call(
                    "click",
                    self._sdk.ClickInput(
                        target=target,
                        position=self._click_position(action, prepared.window_point),
                        delivery_mode=_input_delivery(self._sdk, delivery),
                        session=session_name,
                        button=_click_button(self._sdk, action.button),
                        count=action.count,
                    ),
                )
                return outcome_from_action(result)
            if action.type == "type_text":
                result = await self._call(
                    "type_text",
                    self._sdk.TypeTextInput(
                        text=action.text,
                        target=target,
                        scope=None,
                        session=session_name,
                    ),
                )
                return outcome_from_tool(result)
            if action.type == "scroll":
                point = self._scroll_point(action, prepared.window_point)
                result = await self._call(
                    "scroll",
                    self._sdk.ScrollInput(
                        x=point[0],
                        y=point[1],
                        direction=getattr(self._sdk.ScrollDirection, action.direction.upper()),
                        target=target,
                        scope=None,
                        session=session_name,
                        by=None,
                        amount=action.amount,
                    ),
                )
                return outcome_from_tool(result)
            if action.type == "press_key":
                result = await self._call(
                    "press_key",
                    self._sdk.PressKeyInput(
                        key=action.key,
                        target=target,
                        scope=None,
                        session=session_name,
                        modifiers=None,
                    ),
                )
                return outcome_from_tool(result)
            if action.type == "hotkey":
                result = await self._call(
                    "hotkey",
                    self._sdk.HotkeyInput(
                        keys=list(action.keys),
                        target=target,
                        scope=None,
                        session=session_name,
                    ),
                )
                return outcome_from_tool(result)
            raise ComputerUseContractError("rejected_action")
        except NativeActionInterrupted as exc:
            return ActionOutcome(
                status={"NOT_STARTED": "not_started", "COMPLETED": "completed"}.get(
                    exc.completion, "unknown"
                ),
                error_code="action_interrupted",
            )
        except ComputerUseContractError as exc:
            if str(exc) == "driver_timeout":
                return ActionOutcome(status="unknown", error_code="driver_timeout")
            raise
        except Exception as exc:
            if type(exc).__name__ == "ActionInterrupted":
                return _interrupted_outcome(exc)
            return ActionOutcome(status="unknown", error_code="driver_error")

    def _window_target(
        self, request: DiscoverRequest, bundle_id: str, pid: int, window: Any, birth: ProcessBirth
    ) -> TargetRef | None:
        window_id = getattr(window, "window_id", None)
        if (
            not isinstance(window_id, int)
            or isinstance(window_id, bool)
            or not 0 < window_id <= 2**32 - 1
        ):
            return None
        owner_pid = getattr(window, "pid", None)
        if owner_pid is not None and (
            isinstance(owner_pid, bool) or not isinstance(owner_pid, int) or owner_pid != pid
        ):
            return None
        try:
            geometry = _window_geometry(window)
        except ComputerUseContractError:
            return None
        minimized = getattr(window, "minimized", False) is True
        on_screen = getattr(window, "is_on_screen", True) is not False
        if minimized or not on_screen:
            return None
        return self._registry.remember_window(
            agent_run_id=request.scope.agent_run_id,
            generation=request.scope.generation,
            bundle_id=bundle_id,
            pid=pid,
            window_id=window_id,
            display_label=_display_label(getattr(window, "title", None)),
            process_birth=birth,
            geometry=geometry,
        )

    def _validate_process(self, window: Any) -> None:
        try:
            birth = self._process_reader(window.pid)
        except ComputerUseContractError:
            raise ComputerUseContractError("stale_observation") from None
        if birth != window.process_birth:
            raise ComputerUseContractError("stale_observation")

    async def _validate_live_target(self, window: Any) -> WindowGeometry:
        self._validate_process(window)
        apps = await self._call("list_apps", self._sdk.ListAppsInput())
        if not any(
            getattr(app, "pid", None) == window.pid
            and getattr(app, "bundle_id", None) == window.bundle_id
            and getattr(app, "running", True) is not False
            for app in getattr(apps, "apps", ()) or ()
        ):
            raise ComputerUseContractError("stale_observation")
        listed = await self._call(
            "list_windows", self._sdk.ListWindowsInput(pid=window.pid, on_screen_only=True)
        )
        candidates = [
            native
            for native in getattr(listed, "windows", ()) or ()
            if getattr(native, "window_id", None) == window.window_id
            and getattr(native, "pid", None) in {None, window.pid}
            and getattr(native, "is_on_screen", True) is not False
            and getattr(native, "minimized", False) is not True
        ]
        self._validate_process(window)
        if len(candidates) != 1:
            raise ComputerUseContractError("stale_observation")
        return _window_geometry(candidates[0])

    def _observation(
        self,
        request: ObserveWindowRequest,
        window_identity: str,
        bundle_id: str,
        state: Any,
        *,
        geometry: WindowGeometry,
    ) -> ObservedWindow:
        elements, omitted, truncated = _elements(state, self._registry, window_identity)
        degraded = bool(getattr(state, "degraded", False))
        degraded_reason = None
        if degraded:
            reason = getattr(state, "degraded_reason", None)
            prefix = reason.split(":", 1)[0] if isinstance(reason, str) else None
            degraded_reason = (
                prefix if prefix in {"ax_window_unresolved", "ax_tree_empty"} else "unknown"
            )
        if omitted > 0:
            truncated = True
        if truncated and omitted < 1:
            omitted = 1
        complete = (
            getattr(state, "elements_complete", None) is True
            and not degraded
            and not truncated
            and omitted == 0
        )
        capture, image_error = (None, None)
        if request.include_image:
            capture, image_error = _capture(state)
        if capture is not None:
            reported = _bounds_geometry(getattr(state, "window_bounds", None))
            if reported != geometry:
                raise ComputerUseContractError("stale_observation")
        digest_source = (
            capture.content
            if capture is not None
            else canonical_json_bytes({"elements": [item.element_ref for item in elements]})
        )
        observation_id = self._ids.new_id(COMPUTER_OBSERVATION_ID_PREFIX)
        snapshot_id = getattr(state, "snapshot_id", None)
        if isinstance(snapshot_id, str):
            self._registry.remember_snapshot(
                observation_id, snapshot_id, window_identity=window_identity
            )
        try:
            observation = Observation(
                observation_id=observation_id,
                target_ref=request.target.target_ref,
                agent_run_id=request.target.agent_run_id,
                generation=request.target.generation,
                bundle_id=bundle_id,
                process_identity=request.target.process_identity,
                window_identity=window_identity,
                capture_digest=sha256_digest(digest_source),
                captured_at=self._clock.now(),
                frame=_frame(state, geometry=geometry),
                elements=tuple(elements),
                complete=complete,
                degraded=degraded,
                degraded_reason=degraded_reason,
                truncated=truncated,
                omitted_count=omitted,
            )
        except ComputerUseContractError:
            raise
        except ValueError:
            raise ComputerUseContractError("rejected_action") from None
        regions = ()
        if capture is not None:
            try:
                if degraded or truncated or omitted:
                    raise ComputerUseContractError("image_safety_unconfirmed")
                regions = _sensitive_regions(state, elements, geometry, capture)
            except ComputerUseContractError:
                image_error = "image_safety_unconfirmed"
        return ObservedWindow(
            observation=observation,
            capture=capture,
            image_error=image_error,
            sensitive_regions=regions,
        )

    def _click_position(self, action: Any, window_point: tuple[float, float] | None) -> Any:
        if action.element_ref is not None:
            element = self._registry.element(action.element_ref)
            if element.token is None:
                raise ComputerUseContractError("unknown_element")
            return self._sdk.ClickPosition.ELEMENT(element.token)
        if window_point is None:
            raise ComputerUseContractError("unknown_scale")
        return self._sdk.ClickPosition.COORDINATES(window_point[0], window_point[1])

    def _scroll_point(
        self, action: Any, window_point: tuple[float, float] | None
    ) -> tuple[float, float]:
        if window_point is not None:
            return window_point
        if action.element_ref is not None:
            center = self._registry.element(action.element_ref).center
            if center is not None:
                return center
        raise ComputerUseContractError("rejected_action")

    @property
    def pending(self) -> bool:
        return self._calls.pending

    @property
    def quarantined(self) -> bool:
        return self._calls.quarantined

    def invalidate(self) -> None:
        self._closed = True
        self._registry.clear()
        self._calls.stop()

    async def settle(self) -> None:
        self._check_owner()
        await self._calls.settle()

    async def _call(self, name: str, payload: Any, *, cleanup: bool = False) -> Any:
        method = self._require(name)
        return await self._calls.run(lambda: method(payload), cleanup=cleanup)

    def _check_owner(self, *, bind: bool = False) -> None:
        loop = asyncio.get_running_loop()
        thread = threading.get_ident()
        if bind and self._owner_loop is None:
            self._owner_loop, self._owner_thread = loop, thread
        if self._owner_loop is not loop or self._owner_thread != thread:
            raise ComputerUseContractError("owner_mismatch")

    def _require(self, name: str) -> Any:
        self._check_owner()
        method = getattr(self._native, name, None)
        if method is None:
            raise ComputerUseContractError("abi_mismatch")
        return method

    def _require_session(self) -> str:
        self._check_owner()
        if self._closed or self._session_name is None:
            raise ComputerUseContractError("driver_not_activated")
        return self._session_name

    def _require_subject(self, agent_run_id: str, generation: int) -> None:
        self._require_session()
        if self._agent_run_id != agent_run_id or self._generation != generation:
            raise ComputerUseContractError("subject_mismatch")

    def _require_scope(self, scope: ComputerUseScope) -> None:
        self._require_subject(scope.agent_run_id, scope.generation)
        if scope != self._scope:
            raise ComputerUseContractError("subject_mismatch")


def outcome_from_action(result: Any) -> ActionOutcome:
    effect = _enum_name(getattr(result, "effect", None))
    delivery = _actual_delivery(getattr(result, "delivery", None))
    if effect == "REFUSED":
        code = _stable_code(getattr(getattr(result, "error", None), "code", None), "refused")
        return ActionOutcome(status="not_started", error_code=code)
    if effect == "CONFIRMED" and delivery is not None:
        return ActionOutcome(status="completed", delivery=delivery)
    if effect == "PARTIAL" and delivery is not None:
        return ActionOutcome(status="completed", delivery=delivery, error_code="partial_effect")
    if effect == "SUSPECTED_NOOP":
        return ActionOutcome(status="unknown", delivery=delivery, error_code="suspected_noop")
    if effect in {"CONFIRMED", "PARTIAL", "UNVERIFIABLE"}:
        code = "unknown_delivery" if delivery is None else "unverified_action"
        return ActionOutcome(status="unknown", delivery=delivery, error_code=code)
    return ActionOutcome(status="unknown", error_code="driver_error")


def outcome_from_tool(result: Any) -> ActionOutcome:
    action = getattr(result, "action", None)
    if action is not None:
        return outcome_from_action(action)
    if bool(getattr(result, "degraded", False)):
        return ActionOutcome(status="unknown", error_code="degraded_result")
    if bool(getattr(result, "is_error", False)):
        return ActionOutcome(
            status="not_started",
            error_code=_stable_code(getattr(result, "error_code", None), "tool_error"),
        )
    return ActionOutcome(status="unknown", error_code="unverified_action")


def _interrupted_outcome(exc: BaseException) -> ActionOutcome:
    name = _enum_name(getattr(exc, "completion", None))
    if name == "NOT_STARTED":
        status = "not_started"
    elif name == "COMPLETED":
        status = "completed"
    else:
        status = "unknown"
    return ActionOutcome(status=status, error_code="action_interrupted")


def _elements(
    state: Any, registry: TrustedDesktopRegistry, window_identity: str
) -> tuple[list[AxElement], int, bool]:
    raw_elements = getattr(state, "elements", None) or ()
    total = getattr(state, "total_element_count", None)
    returned = getattr(state, "returned_element_count", None)
    omitted = 0
    if isinstance(total, int) and isinstance(returned, int) and not isinstance(total, bool):
        omitted = max(0, total - returned)
    truncated = bool(getattr(state, "truncated", False))
    kept: list[AxElement] = []
    text_bytes = 0
    for raw in raw_elements:
        if len(kept) >= MAX_AX_ELEMENTS:
            omitted += 1
            truncated = True
            continue
        depth = getattr(raw, "depth", None)
        if (
            isinstance(depth, bool)
            or not isinstance(depth, int)
            or depth < 0
            or depth > MAX_AX_DEPTH
        ):
            omitted += 1
            truncated = True
            continue
        role = _role(getattr(raw, "role", ""))
        label, sensitive = _element_label(raw, role)
        addition = len(role.encode()) + len((label or "").encode())
        if text_bytes + addition > MAX_AX_TEXT_BYTES:
            omitted += 1
            truncated = True
            continue
        text_bytes += addition
        token = getattr(raw, "element_token", None)
        element_ref = registry.remember_element(
            window_identity=window_identity,
            token=token if isinstance(token, str) and token else None,
            center=_center(getattr(raw, "frame", None)),
        )
        kept.append(
            AxElement(
                element_ref=element_ref, depth=depth, role=role, label=label, sensitive=sensitive
            )
        )
    return kept, omitted, truncated


def _window_geometry(window: Any) -> WindowGeometry:
    return _bounds_geometry(getattr(window, "bounds", None))


def _bounds_geometry(bounds: Any) -> WindowGeometry:
    values = tuple(getattr(bounds, name, None) for name in ("x", "y", "width", "height"))
    if any(
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or abs(value) > 2**31 - 1
        for value in values
    ):
        raise ComputerUseContractError("unknown_scale")
    if values[2] <= 0 or values[3] <= 0:
        raise ComputerUseContractError("unknown_scale")
    return WindowGeometry(*values)


def _frame(state: Any, *, geometry: WindowGeometry) -> CoordinateFrame:
    width = _positive_int(getattr(state, "screenshot_width", None))
    height = _positive_int(getattr(state, "screenshot_height", None))
    scale = _positive_float(getattr(state, "screenshot_scale", None))
    crop_width = None
    crop_height = None
    if width is None or height is None:
        width = math.ceil(geometry.width)
        height = math.ceil(geometry.height)
        scale = None
    elif getattr(state, "screenshot_frame_valid", None) is True and scale is not None:
        # The pinned SDK accepts pixels of its delivered (possibly resized)
        # window screenshot. Its session cache undoes resizing and the native
        # backend applies backing scale. Do not divide by Retina scale here.
        scale = 1.0
        crop_width = width
        crop_height = height
    else:
        scale = None
    if width is None or height is None:
        raise ComputerUseContractError("unknown_scale")
    try:
        return CoordinateFrame(
            width=width,
            height=height,
            scale_x=scale,
            scale_y=scale,
            crop_width=crop_width,
            crop_height=crop_height,
        )
    except ValueError:
        raise ComputerUseContractError("unknown_scale") from None


def _capture(state: Any) -> tuple[TransientCapture | None, str | None]:
    images = getattr(state, "images", ()) or ()
    if not images:
        return None, "image_missing"
    if getattr(state, "screenshot_frame_valid", None) is not True:
        return None, "unknown_scale"
    image = images[0]
    mime = _MIME.get(str(getattr(image, "mime_type", "")).casefold())
    if mime is None:
        return None, "image_mime"
    encoded = getattr(image, "data_base64", "")
    if not isinstance(encoded, str) or not encoded:
        return None, "image_decode"
    if len(images) != 1 or len(encoded) > ((MAX_IMAGE_BYTES + 2) // 3) * 4:
        return None, "image_bounds"
    try:
        content = base64.b64decode(encoded, validate=True)
    except Exception:
        return None, "image_decode"
    width = _positive_int(getattr(state, "screenshot_width", None))
    height = _positive_int(getattr(state, "screenshot_height", None))
    if width is None or height is None:
        return None, "unknown_scale"
    if (
        not content
        or len(content) > MAX_IMAGE_BYTES
        or width * height > MAX_IMAGE_PIXELS
        or max(width, height) > MAX_IMAGE_LONG_EDGE_PX
    ):
        return None, "image_bounds"
    return TransientCapture(content=content, mime=mime, width=width, height=height), None


def _input_delivery(sdk: Any, delivery: ComputerUseDelivery) -> Any:
    name = "BACKGROUND" if delivery is ComputerUseDelivery.BACKGROUND else "FOREGROUND"
    return getattr(sdk.InputDeliveryMode, name)


def _click_button(sdk: Any, button: str) -> Any:
    return getattr(sdk.ClickButton, "RIGHT" if button == "right" else "LEFT")


def _actual_delivery(delivery: Any) -> ComputerUseDelivery | None:
    name = _enum_name(getattr(delivery, "mode", None))
    if name == "FOREGROUND":
        return ComputerUseDelivery.FOREGROUND
    if name == "BACKGROUND":
        return ComputerUseDelivery.BACKGROUND
    return None


def _enum_name(value: Any) -> str | None:
    name = getattr(value, "name", None)
    return name if isinstance(name, str) else None


def _stable_code(value: object, fallback: str) -> str:
    if isinstance(value, str) and _CODE.fullmatch(value):
        return value
    return fallback


def _role(value: object) -> str:
    text = value if isinstance(value, str) else ""
    pieces = [char if char.isalnum() else "_" for char in text.casefold()]
    token = "_".join(part for part in "".join(pieces).split("_") if part)
    if not token or not token[0].isalpha():
        token = f"ax_{token}" if token else "ax_element"
    return token[:64]


def _element_label(raw: Any, role: str) -> tuple[str | None, bool]:
    if "secure" in role or "password" in role:
        return None, True
    # Inspect transient values only for known secret material; never project them.
    for name in ("value", "value_description", "label"):
        value = getattr(raw, name, None)
        if isinstance(value, str):
            try:
                refuse_secret_material(value, label="computer use element")
            except ValueError:
                return None, True
    label = getattr(raw, "label", None)
    if not isinstance(label, str):
        return None, False
    cleaned = " ".join(label.split())
    if not cleaned or len(cleaned) > 200:
        return None, False
    return cleaned, False


def _sensitive_regions(
    state: Any,
    elements: list[AxElement],
    geometry: WindowGeometry,
    capture: TransientCapture,
) -> tuple[SensitiveCaptureRegion, ...]:
    """Pinned AX frames are top-left screen points, unlike SDK action pixels.

    Only a full, uncropped window image with matching live bounds is accepted.
    Round outward so downscaling never leaves an edge of a secret visible.
    """
    raw = iter(getattr(state, "elements", None) or ())
    regions = []
    for element in elements:
        native = next(raw, None)
        if not element.sensitive:
            continue
        frame = getattr(native, "frame", None)
        numbers = tuple(getattr(frame, name, None) for name in ("x", "y", "w", "h"))
        if any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            for value in numbers
        ):
            raise ComputerUseContractError("image_safety_unconfirmed")
        x, y, width, height = numbers
        if (
            width <= 0
            or height <= 0
            or x < geometry.x
            or y < geometry.y
            or x + width > geometry.x + geometry.width
            or y + height > geometry.y + geometry.height
        ):
            raise ComputerUseContractError("image_safety_unconfirmed")
        sx, sy = capture.width / geometry.width, capture.height / geometry.height
        regions.append(
            SensitiveCaptureRegion(
                element_ref=element.element_ref,
                left=math.floor((x - geometry.x) * sx),
                top=math.floor((y - geometry.y) * sy),
                right=math.ceil((x + width - geometry.x) * sx),
                bottom=math.ceil((y + height - geometry.y) * sy),
            )
        )
    return tuple(regions)


def _display_label(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = " ".join(value.split())
    if not cleaned or len(cleaned) > 120:
        return None
    try:
        refuse_secret_material(cleaned, label="computer use label")
    except ValueError:
        return None
    return cleaned


def _center(frame: Any) -> tuple[float, float] | None:
    if frame is None:
        return None
    numbers = [getattr(frame, name, None) for name in ("x", "y", "w", "h")]
    if any(isinstance(item, bool) or not isinstance(item, (int, float)) for item in numbers):
        return None
    x, y, width, height = (float(item) for item in numbers)
    if width <= 0 or height <= 0 or not math.isfinite(x + y + width + height):
        return None
    return x + width / 2, y + height / 2


def _positive_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if isinstance(value, float) and not value.is_integer():
        return None
    number = int(value)
    return number if number >= 1 else None


def _positive_float(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        return None
    return number
