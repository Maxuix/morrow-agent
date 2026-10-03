"""Typed desktop session calls. Native process, window, and element tokens stay in the registry."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable
from typing import Any

from morrow.adapters.computer_use.action_inputs import (
    NativeHotkeyInput,
    NativeKeyInput,
    NativeScrollInput,
    NativeTextInput,
    invoke_fixed_action,
    native_key,
)
from morrow.adapters.computer_use.calls import NativeActionInterrupted, NativeCalls
from morrow.adapters.computer_use.census import (
    display_label,
    running_app,
    strict_window,
    valid_pid,
    window_geometry,
)
from morrow.adapters.computer_use.process_identity import ProcessBirth, read_process_birth
from morrow.adapters.computer_use.projection import (
    outcome_from_action,
    outcome_from_tool,
    project_capture,
    project_elements,
    project_frame,
    reported_window_geometry,
)
from morrow.adapters.computer_use.registry import TrustedDesktopRegistry, WindowGeometry
from morrow.core.computer_admission import AdmittedDiscover, AdmittedExecute, AdmittedObserve
from morrow.core.computer_use import (
    MAX_AX_DEPTH,
    MAX_DISCOVERED_TARGETS,
    MAX_IMAGE_LONG_EDGE_PX,
    MAX_OBSERVATION_AGE_SECONDS,
    ActionOutcome,
    CloseRunSessionRequest,
    ComputerUseAppIdentity,
    ComputerUseContractError,
    ComputerUseDelivery,
    DiscoverRequest,
    DiscoverResult,
    Observation,
    ObservedWindow,
    ObserveWindowRequest,
    OpenRunSessionRequest,
    RunSession,
    SelectedWindowScope,
    TargetRef,
    reject_untrusted_computer_use_authority,
)
from morrow.core.domain import (
    COMPUTER_OBSERVATION_ID_PREFIX,
    COMPUTER_RUN_ID_PREFIX,
    canonical_json_bytes,
    sha256_digest,
)
from morrow.core.ports import Clock, IdSource

# The pinned SDK counts every traversed node, including collapsed layout containers.
# Bound native work separately from the unchanged 200-element model projection.
MAX_NATIVE_AX_NODES = 400


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
        window_bindings: dict | None = None,
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
        self._scope: SelectedWindowScope | None = None
        self._calls = NativeCalls(self.invalidate, timeout=call_timeout)
        self._process_reader = process_reader
        self._window_bindings = dict(window_bindings or {})

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

    @property
    def session_name(self) -> str | None:
        """Name reserved at start, including a start that has not returned yet."""

        return self._session_name or self._reserved_name

    async def discover(self, admitted: AdmittedDiscover) -> DiscoverResult:
        request = admitted.request
        self._require_scope(request.scope)
        if request.run_session_id != self._require_session():
            raise ComputerUseContractError("subject_mismatch")
        granted = {item.bundle_id for item in request.scope.apps}
        if request.bundle_id is not None:
            granted = {request.bundle_id}
        self._registry.retire_all_observations()
        listed = await self._call("list_apps", self._sdk.ListAppsInput())
        targets: list[TargetRef] = []
        for app in getattr(listed, "apps", ()) or ():
            bundle_id = getattr(app, "bundle_id", None)
            pid = getattr(app, "pid", None)
            if not isinstance(bundle_id, str) or bundle_id not in granted:
                continue
            if not valid_pid(pid) or not running_app(app):
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

    async def observe(self, admitted: AdmittedObserve) -> ObservedWindow:
        request = admitted.request
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
        resolved = admitted.settings
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
                max_elements=MAX_NATIVE_AX_NODES,
                max_depth=MAX_AX_DEPTH,
                max_dimension=None,
                max_image_dimension=min(resolved.image_long_edge_px, MAX_IMAGE_LONG_EDGE_PX),
                timeout_ms=int(resolved.max_call_seconds * 1000),
            ),
        )
        if await self._validate_live_target(window) != geometry:
            raise ComputerUseContractError("stale_observation")
        return await self._observation(
            request, window.window_identity, window.bundle_id, state, geometry=geometry
        )

    async def execute_one(
        self,
        admitted: AdmittedExecute,
        *,
        authority: Callable[[], None],
    ) -> ActionOutcome:
        """Dispatch one admitted action. Authority, process, and age are rechecked."""

        authority()
        request = admitted.request
        prepared = admitted.prepared
        self._require_scope(request.scope)
        observation = request.observation
        self._require_fresh(observation.captured_at)
        window = self._registry.window(request.target.window_identity)
        action = prepared.action
        element = self._registry.element(action.element_ref) if action.element_ref else None
        # Drop the registry token before any await so cancellation cannot dispatch twice.
        self._registry.retire_window_observations(window.window_identity)
        try:
            if element is not None and (
                element.window_identity != window.window_identity or not element.token
            ):
                raise ComputerUseContractError("unknown_element")
            if action.type in {"type_text", "press_key", "hotkey"} and (
                element is None or not element.token
            ):
                raise ComputerUseContractError("unknown_element")
            if await self._validate_live_target(window) != window.geometry:
                raise ComputerUseContractError("stale_observation")
            authority()
            self._require_scope(request.scope)
            self._validate_process(window)
            if action.type == "click":
                payload = self._sdk.ClickInput(
                    target=self._sdk.ActionTarget.WINDOW(window.pid, window.window_id),
                    position=self._click_position(
                        action, prepared.window_point, element, observation, window.geometry
                    ),
                    delivery_mode=_input_delivery(self._sdk, request.delivery),
                    session=self._require_session(),
                    button=_click_button(self._sdk, action.button),
                    count=action.count,
                )

                async def dispatch():
                    return await self._require("click")(payload)

                normalize = outcome_from_action
            else:
                token = element.token if element is not None else None
                common = dict(
                    pid=window.pid,
                    window_id=window.window_id,
                    session=self._require_session(),
                    delivery_mode=request.delivery.value,
                )
                try:
                    if action.type == "type_text":
                        payload = NativeTextInput(**common, element_token=token, text=action.text)
                    elif action.type == "press_key":
                        payload = NativeKeyInput(
                            **common, element_token=token, key=native_key(action.key)
                        )
                    elif action.type == "hotkey":
                        payload = NativeHotkeyInput(
                            **common,
                            element_token=token,
                            keys=tuple(native_key(key) for key in action.keys),
                        )
                    elif action.type == "scroll":
                        point = prepared.window_point
                        payload = NativeScrollInput(
                            **common,
                            direction=action.direction,
                            amount=action.amount,
                            element_token=token,
                            x=point[0] if point else None,
                            y=point[1] if point else None,
                        )
                    else:
                        raise ComputerUseContractError("rejected_action")
                except ValueError:
                    raise ComputerUseContractError("rejected_action") from None

                async def dispatch():
                    return await invoke_fixed_action(self._native, payload)

                normalize = outcome_from_tool

            async def admitted_call():
                # Retained native task, immediately before SDK entry.
                authority()
                self._require_scope(request.scope)
                self._validate_process(window)
                self._require_fresh(observation.captured_at)
                return await dispatch()

            try:
                result = await self._calls.run(admitted_call)
                outcome = normalize(result)
            except NativeActionInterrupted as exc:
                outcome = ActionOutcome(
                    status={"NOT_STARTED": "not_started", "COMPLETED": "completed"}.get(
                        exc.completion, "unknown"
                    ),
                    error_code="action_interrupted",
                )
            except ComputerUseContractError as exc:
                # Once admitted, an opaque SDK failure cannot prove no side effect.
                if str(exc) in {"driver_timeout", "driver_error"}:
                    outcome = ActionOutcome(status="unknown", error_code=str(exc))
                else:
                    raise
            if outcome.delivery is not None and outcome.delivery is not request.delivery:
                outcome = outcome.model_copy(
                    update={"status": "unknown", "error_code": "unexpected_delivery"}
                )
            return outcome.model_copy(update={"before_observation_id": observation.observation_id})
        finally:
            self._registry.retire_window_observations(window.window_identity)

    def _require_fresh(self, captured_at) -> None:
        age = (self._clock.now() - captured_at).total_seconds()
        if not 0 <= age < MAX_OBSERVATION_AGE_SECONDS:
            raise ComputerUseContractError("stale_observation")

    def _window_target(
        self, request: DiscoverRequest, bundle_id: str, pid: int, window: Any, birth: ProcessBirth
    ) -> TargetRef | None:
        if not strict_window(window, pid):
            return None
        try:
            geometry = window_geometry(window)
        except ComputerUseContractError:
            return None
        window_id = window.window_id
        selected_identity = None
        for selected in request.scope.windows:
            native = self._window_bindings.get(selected.window_identity)
            if (
                native is not None
                and selected.app.bundle_id == bundle_id
                and native.bundle_id == bundle_id
                and native.pid == pid
                and native.window_id == window_id
                and native.process_birth == birth
            ):
                selected_identity = selected.window_identity
                break
        if selected_identity is None:
            return None
        return self._registry.remember_window(
            window_identity=selected_identity,
            agent_run_id=request.scope.agent_run_id,
            generation=request.scope.generation,
            bundle_id=bundle_id,
            pid=pid,
            window_id=window_id,
            display_label=display_label(getattr(window, "title", None)),
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
            and running_app(app)
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
            and strict_window(native, window.pid)
        ]
        self._validate_process(window)
        if len(candidates) != 1:
            raise ComputerUseContractError("stale_observation")
        return window_geometry(candidates[0])

    async def _observation(
        self,
        request: ObserveWindowRequest,
        window_identity: str,
        bundle_id: str,
        state: Any,
        *,
        geometry: WindowGeometry,
    ) -> ObservedWindow:
        elements, omitted, truncated = project_elements(state, self._registry, window_identity)
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
            capture, image_error = project_capture(state)
        if capture is not None:
            reported = reported_window_geometry(state)
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
                frame=project_frame(state, geometry=geometry),
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
        self._registry.window(window_identity).geometry = geometry
        return ObservedWindow(
            observation=observation,
            capture=capture,
            image_error=image_error,
        )

    def _click_position(
        self,
        action: Any,
        window_point: tuple[float, float] | None,
        element: Any,
        observation: Observation,
        geometry: WindowGeometry,
    ) -> Any:
        if action.element_ref is not None:
            if element is None or element.token is None:
                raise ComputerUseContractError("unknown_element")
            if action.count == 1 and action.button == "left":
                return self._sdk.ClickPosition.ELEMENT(element.token)
            if observation.image is None:
                raise ComputerUseContractError("image_not_published")
            if element.center is None:
                raise ComputerUseContractError("element_geometry_unavailable")
            x, y = element.center
            if not (
                geometry.x <= x < geometry.x + geometry.width
                and geometry.y <= y < geometry.y + geometry.height
            ):
                raise ComputerUseContractError("out_of_bounds")
            # AX frames are screen points; SDK expects delivered-image pixels.
            px = int((x - geometry.x) * observation.frame.width / geometry.width)
            py = int((y - geometry.y) * observation.frame.height / geometry.height)
            from morrow.core.computer_use import map_image_point

            point = map_image_point(observation.frame, px, py)
            return self._sdk.ClickPosition.COORDINATES(*point)
        if window_point is None:
            raise ComputerUseContractError("unknown_scale")
        return self._sdk.ClickPosition.COORDINATES(window_point[0], window_point[1])

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

    def _require_scope(self, scope: SelectedWindowScope) -> None:
        self._require_subject(scope.agent_run_id, scope.generation)
        if scope != self._scope:
            raise ComputerUseContractError("subject_mismatch")


def _input_delivery(sdk: Any, delivery: ComputerUseDelivery) -> Any:
    name = "BACKGROUND" if delivery is ComputerUseDelivery.BACKGROUND else "FOREGROUND"
    return getattr(sdk.InputDeliveryMode, name)


def _click_button(sdk: Any, button: str) -> Any:
    return getattr(sdk.ClickButton, "RIGHT" if button == "right" else "LEFT")


# Names kept for adapter tests that project scripted SDK state.
_capture = project_capture
_frame = project_frame
_elements = project_elements
