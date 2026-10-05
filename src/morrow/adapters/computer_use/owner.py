"""Loop-affine SDK resources and a lease retained until admitted work settles."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable
from typing import Any

from morrow.adapters.computer_use.calls import NativeCalls
from morrow.adapters.computer_use.candidates import LocalCandidateRegistry, LocalWindowIdentity
from morrow.adapters.computer_use.census import (
    display_label,
    running_app,
    strict_window,
    valid_pid,
    window_geometry,
)
from morrow.adapters.computer_use.lease import DesktopLease, FileDesktopLease
from morrow.adapters.computer_use.process_identity import ProcessBirth, read_process_birth
from morrow.adapters.computer_use.registry import TrustedDesktopRegistry
from morrow.adapters.computer_use.sdk_loader import construct_driver
from morrow.adapters.computer_use.session import TypedComputerSession
from morrow.core.computer_use import (
    MAX_DISCOVERED_TARGETS,
    TRUSTED_COMPUTER_USE_AUTHORITY,
    CloseRunSessionRequest,
    ComputerUseAppIdentity,
    ComputerUseContractError,
    ComputerUseRuntimeStatus,
    LocalComputerUseCandidates,
    OpenRunSessionRequest,
    RunSession,
    reject_untrusted_computer_use_authority,
)
from morrow.core.domain import COMPUTER_RUN_ID_PREFIX
from morrow.core.ports import Clock, IdSource
from morrow.core.runtime_policy import ComputerUseSettings


class ComputerDriverOwner:
    """No native task is cancelled just because its Python caller stops waiting."""

    def __init__(
        self,
        sdk: Any,
        ids: IdSource,
        clock: Clock,
        *,
        session_factory: Callable[[Any, str, ComputerUseSettings], Any],
        driver_factory: Callable[[Any], Any] = construct_driver,
        lease: DesktopLease | None = None,
        call_timeout: float = 15,
        process_reader: Callable[[int], ProcessBirth] = read_process_birth,
    ) -> None:
        self._loop = asyncio.get_running_loop()
        self._thread = threading.get_ident()
        self._sdk, self._ids, self._clock = sdk, ids, clock
        self._session_factory = session_factory
        self._driver = driver_factory(sdk)
        self._lease = lease if lease is not None else FileDesktopLease()
        self._leased = False
        self._call_timeout = call_timeout
        self._process_reader = process_reader
        self._session: TypedComputerSession | None = None
        self._run: RunSession | None = None
        self._generations: dict[str, int] = {}
        self._closed = False
        self._stopping = False
        self._quarantined = False
        self._lifecycle = NativeCalls(self._quarantine, timeout=None)
        self._candidates = LocalCandidateRegistry(ids, clock)
        self._candidate_calls: NativeCalls | None = None

    def __repr__(self) -> str:
        return (
            f"ComputerDriverOwner(active={self._run is not None}, "
            f"closed={self._closed}, quarantined={self.quarantined})"
        )

    @property
    def quarantined(self) -> bool:
        return self._quarantined or bool(self._session and self._session.quarantined)

    @property
    def runtime_status(self) -> ComputerUseRuntimeStatus:
        pending = (
            self._lifecycle.pending
            or bool(self._candidate_calls and self._candidate_calls.pending)
            or bool(self._session and self._session.pending)
        )
        state = (
            "closed"
            if self._closed
            else "quarantined"
            if self.quarantined
            else "stopping"
            if self._stopping
            else "active"
            if self._session is not None
            else "idle"
        )
        return ComputerUseRuntimeStatus(state=state, native_pending=pending)

    @property
    def shutdown_pending(self) -> bool:
        """A Host must keep its owner loop alive until shutdown actually succeeds."""
        return not self._closed

    def _check_owner(self) -> None:
        if asyncio.get_running_loop() is not self._loop or threading.get_ident() != self._thread:
            raise ComputerUseContractError("owner_mismatch")

    def _quarantine(self) -> None:
        self._quarantined = True
        self._candidates.clear()
        if self._candidate_calls is not None:
            self._candidate_calls.stop()
        if self._session is not None:
            self._session.invalidate()

    def _admit(self) -> None:
        self._check_owner()
        if self._closed or self._stopping or self.quarantined:
            raise ComputerUseContractError("driver_not_activated")
        if self._lifecycle.pending:
            raise ComputerUseContractError("desktop_busy")

    async def discover_local_candidates(self, settings, *, authority) -> LocalComputerUseCandidates:
        """Explicit local read, sharing the same Driver and desktop lease as runs."""
        self._admit()
        reject_untrusted_computer_use_authority(authority)
        if not isinstance(settings, ComputerUseSettings) or not settings.enabled:
            raise ComputerUseContractError("disabled")
        if self._session is not None:
            raise ComputerUseContractError("desktop_busy")
        self._candidates.clear_candidates()
        self._lease.acquire()
        self._leased = True
        self._candidate_calls = NativeCalls(self._quarantine, timeout=settings.max_call_seconds)
        return await self._lifecycle.run(self._discover_local_candidates)

    async def _discover_local_candidates(self):
        calls = self._candidate_calls
        assert calls is not None
        try:
            listed = await calls.run(lambda: self._driver.list_apps(self._sdk.ListAppsInput()))
            # The SDK also lists installed applications. Only explicitly running
            # apps can supply local window candidates or consume their budget.
            apps = tuple(app for app in (getattr(listed, "apps", ()) or ()) if running_app(app))
            if len(apps) > MAX_DISCOVERED_TARGETS:
                raise ComputerUseContractError("target_budget")
            candidates, seen = [], set()
            for app in apps:
                bundle_id, pid = getattr(app, "bundle_id", None), getattr(app, "pid", None)
                if not valid_pid(pid) or not running_app(app):
                    continue
                try:
                    ComputerUseAppIdentity(bundle_id=bundle_id)
                    birth = self._process_reader(pid)
                except (ValueError, ComputerUseContractError):
                    continue
                windows = await calls.run(
                    lambda pid=pid: self._driver.list_windows(
                        self._sdk.ListWindowsInput(pid=pid, on_screen_only=True)
                    )
                )
                if self._process_reader(pid) != birth:
                    raise ComputerUseContractError("stale_observation")
                items = getattr(windows, "windows", ()) or ()
                if len(items) > MAX_DISCOVERED_TARGETS:
                    raise ComputerUseContractError("target_budget")
                for window in items:
                    if not strict_window(window, pid):
                        continue
                    try:
                        window_geometry(window)
                    except ComputerUseContractError:
                        continue
                    identity = LocalWindowIdentity(bundle_id, pid, birth, window.window_id)
                    if identity in seen:
                        continue
                    seen.add(identity)
                    candidates.append((identity, display_label(getattr(window, "title", None))))
                    if len(candidates) > MAX_DISCOVERED_TARGETS:
                        raise ComputerUseContractError("target_budget")
            if self.quarantined or self._stopping or self._closed:
                raise ComputerUseContractError("driver_not_activated")
            return self._candidates.publish(candidates)
        finally:
            if not self.quarantined and not calls.pending:
                self._release()

    def select_local_candidates(self, candidate_ids, *, authority):
        self._admit()
        reject_untrusted_computer_use_authority(authority)
        if self._session is not None:
            raise ComputerUseContractError("desktop_busy")
        return self._candidates.select(candidate_ids)

    def discard_local_selection(self, windows, *, authority):
        self._check_owner()
        reject_untrusted_computer_use_authority(authority)
        self._candidates.discard_bindings(windows)

    async def open_run_session(self, request: OpenRunSessionRequest) -> RunSession:
        self._admit()
        reject_untrusted_computer_use_authority(request.authority)
        if request.agent_run_id != request.scope.agent_run_id:
            raise ComputerUseContractError("subject_mismatch")
        if self._session is not None:
            raise ComputerUseContractError("desktop_busy")
        if request.scope.generation <= self._generations.get(request.agent_run_id, 0):
            raise ComputerUseContractError("stale_observation")
        self._lease.acquire()
        self._leased = True
        try:
            bindings = self._candidates.take_bindings(request.scope.windows, self._process_reader)
        except Exception:
            # Local selection failure preceded SDK admission; retry can select afresh.
            self._release()
            raise
        return await self._lifecycle.run(lambda: self._open(request, bindings))

    async def _open(self, request: OpenRunSessionRequest, bindings) -> RunSession:
        try:
            name = self._ids.new_id(COMPUTER_RUN_ID_PREFIX)
            settings = request.settings or ComputerUseSettings()
            native = self._session_factory(self._driver, name, settings)
            self._session = TypedComputerSession(
                self._sdk,
                native,
                TrustedDesktopRegistry(self._ids),
                self._ids,
                self._clock,
                session_name=name,
                call_timeout=(
                    request.settings.max_call_seconds
                    if request.settings is not None
                    else self._call_timeout
                ),
                process_reader=self._process_reader,
                window_bindings=bindings,
            )
            self._run = await self._session.open_run_session(request)
            self._generations[request.agent_run_id] = request.scope.generation
            return self._run
        except BaseException:
            # No release or handle disposal while a timed-out start may still run.
            self._quarantine()
            raise

    def session_for(self, run: RunSession) -> TypedComputerSession:
        self._admit()
        if run != self._run or self._session is None:
            raise ComputerUseContractError("subject_mismatch")
        return self._session

    async def close_run_session(self, request: CloseRunSessionRequest) -> None:
        self._check_owner()
        reject_untrusted_computer_use_authority(request.authority)
        if self._lifecycle.pending:
            raise ComputerUseContractError("desktop_busy")
        if self._run is None or request.run_session_id != self._run.run_session_id:
            raise ComputerUseContractError("subject_mismatch")
        assert self._session is not None
        self._session.invalidate()
        await self._lifecycle.run(lambda: self._close(request), cleanup=True)

    async def _close(self, request: CloseRunSessionRequest) -> None:
        assert self._session is not None
        try:
            await self._session.close_run_session(request)
            self._session, self._run = None, None
            self._release()
        except BaseException:
            self._quarantine()
            raise

    def stop_admission(self) -> None:
        self._check_owner()
        self._stopping = True
        self._candidates.clear()
        if self._candidate_calls is not None:
            self._candidate_calls.stop()
        if self._session is not None:
            self._session.invalidate()

    def _release(self) -> None:
        if self._leased:
            self._lease.release()
            self._leased = False

    async def shutdown(self) -> None:
        self._check_owner()
        if self._closed:
            return
        # Lifecycle operations remain live after cancellation. Wait, never replace.
        self.stop_admission()
        await self._lifecycle.settle()
        if self._candidate_calls is not None:
            await self._candidate_calls.settle()
        if self._closed:
            return
        await self._lifecycle.run(self._shutdown, cleanup=True)

    async def _shutdown(self) -> None:
        if self._session is not None:
            await self._session.settle()
            name = self._session.session_name
            if name is not None:
                try:
                    await self._session.close_run_session(
                        CloseRunSessionRequest(
                            authority=TRUSTED_COMPUTER_USE_AUTHORITY,
                            run_session_id=name,
                        )
                    )
                except Exception:
                    self._quarantine()
                # End itself may have timed out. Driver shutdown is not proof that
                # the admitted end call finished, so also settle that tracked work.
                await self._session.settle()
        try:
            await self._driver.shutdown()
        except BaseException:
            self._quarantine()
            raise
        self._session, self._run = None, None
        self._closed = True
        self._release()
