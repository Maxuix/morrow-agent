"""Lazy desktop lifecycle, shared by local application surfaces and Core Host."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable

from morrow.application.computer_authorization import authorize_computer_execution
from morrow.core.computer_use import (
    COMPUTER_OBSERVE_TOOL,
    TRUSTED_COMPUTER_USE_AUTHORITY,
    CloseRunSessionRequest,
    ComputerUseContractError,
    ComputerUseLifecyclePort,
    ComputerUsePreflight,
    ComputerUseScope,
    ComputerUseSessionPort,
    DiscoverResult,
    ObservedWindow,
    OpenRunSessionRequest,
    RunSession,
    reject_untrusted_computer_use_authority,
)
from morrow.core.ports import Clock
from morrow.core.runtime_policy import ComputerUseMode, ComputerUseSettings
from morrow.services.computer_use import ComputerUseRunService


class ComputerUseLifecycle:
    def __init__(
        self,
        owner_factory: Callable[[], ComputerUseLifecyclePort],
        diagnostic: Callable[[], ComputerUsePreflight],
        *,
        native_verified: bool = False,
    ) -> None:
        self._factory = owner_factory
        self._diagnostic = diagnostic
        self._native_verified = native_verified
        self._owner: ComputerUseLifecyclePort | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: int | None = None
        self._stopping = False
        self._failed = False

    @property
    def shutdown_pending(self) -> bool:
        return self._owner is not None and self._owner.shutdown_pending

    def preflight(self) -> ComputerUsePreflight:
        return self._diagnostic()

    def _bind(self) -> None:
        loop, thread = asyncio.get_running_loop(), threading.get_ident()
        if self._loop is None:
            self._loop, self._thread = loop, thread
        if self._loop is not loop or self._thread != thread:
            raise ComputerUseContractError("owner_mismatch")

    async def open_run_session(self, request: OpenRunSessionRequest) -> RunSession:
        self._bind()
        reject_untrusted_computer_use_authority(request.authority)
        if self._stopping or self._failed:
            raise ComputerUseContractError("driver_not_activated")
        if request.agent_run_id != request.scope.agent_run_id:
            raise ComputerUseContractError("subject_mismatch")
        diagnosis = self.preflight()
        if diagnosis.reason not in {"driver_not_activated", "native_unverified"}:
            raise ComputerUseContractError(diagnosis.reason)
        if not self._native_verified:
            raise ComputerUseContractError("native_unverified")
        if self._owner is None:
            try:
                self._owner = self._factory()
            except Exception:
                self._failed = True
                raise ComputerUseContractError("driver_error") from None
        return await self._owner.open_run_session(request)

    async def close_run_session(self, request: CloseRunSessionRequest) -> None:
        self._bind()
        reject_untrusted_computer_use_authority(request.authority)
        if self._owner is None:
            raise ComputerUseContractError("driver_not_activated")
        await self._owner.close_run_session(request)

    def session_for(self, run: RunSession) -> ComputerUseSessionPort:
        self._bind()
        if self._stopping or self._failed or self._owner is None:
            raise ComputerUseContractError("driver_not_activated")
        return self._owner.session_for(run)

    def stop_admission(self) -> None:
        self._bind()
        self._stopping = True
        if self._owner is not None:
            self._owner.stop_admission()

    async def shutdown(self) -> None:
        self.stop_admission()
        if self._owner is not None:
            await self._owner.shutdown()


class ComputerUseObservationService:
    """Frozen AgentRun admission around a shared lifecycle and transient read port."""

    def __init__(
        self,
        lifecycle: ComputerUseLifecyclePort,
        journal,
        scope: ComputerUseScope,
        settings: ComputerUseSettings,
        clock: Clock,
    ):
        self._lifecycle, self._journal = lifecycle, journal
        self._scope, self._settings, self._clock = scope, settings, clock
        self._run: ComputerUseRunService | None = None
        self._opening = False
        self._closed = False

    def _authority(self, execution_id: str, *, include_image: bool):
        try:
            if self._closed:
                raise ComputerUseContractError("driver_not_activated")
            return authorize_computer_execution(
                self._journal,
                workspace_id=self._scope.workspace_id,
                execution_id=execution_id,
                scope=self._scope,
                tool_name=COMPUTER_OBSERVE_TOOL,
                include_image=include_image,
                now=self._clock.now(),
            )
        except ComputerUseContractError:
            if self._run is not None:
                self._run.stop()
            raise

    async def _ensure_run(self, authority: Callable[[], None]) -> ComputerUseRunService:
        authority()
        if not self._settings.enabled:
            raise ComputerUseContractError("disabled")
        if self._opening:
            raise ComputerUseContractError("desktop_busy")
        if self._run is None:
            self._opening = True
            try:
                run = await self._lifecycle.open_run_session(
                    OpenRunSessionRequest(
                        authority=TRUSTED_COMPUTER_USE_AUTHORITY,
                        agent_run_id=self._scope.agent_run_id,
                        scope=self._scope,
                    )
                )
                try:
                    authority()
                    session = self._lifecycle.session_for(run)
                except ComputerUseContractError:
                    await self._lifecycle.close_run_session(
                        CloseRunSessionRequest(
                            authority=TRUSTED_COMPUTER_USE_AUTHORITY,
                            run_session_id=run.run_session_id,
                        )
                    )
                    raise
                self._run = ComputerUseRunService(
                    session, run, self._scope, self._settings, self._clock
                )
            finally:
                self._opening = False
        assert self._run is not None
        return self._run

    async def discover(self, execution_id: str, *, bundle_id: str | None = None) -> DiscoverResult:
        def authority():
            self._authority(execution_id, include_image=False)

        allowed = {app.bundle_id for app in self._scope.apps}
        if bundle_id is not None and bundle_id not in allowed:
            authority()
            raise ComputerUseContractError("app_not_granted")
        run = await self._ensure_run(authority)
        return await run.discover(authority=authority, bundle_id=bundle_id)

    async def observe(
        self, execution_id: str, target_ref: str, *, include_image: bool | None = None
    ) -> ObservedWindow:
        include_image = (
            self._settings.mode is ComputerUseMode.HYBRID
            if include_image is None
            else include_image
        )

        def authority():
            self._authority(execution_id, include_image=include_image)

        authority()
        if self._run is None:
            raise ComputerUseContractError("unknown_target")
        return await self._run.observe(target_ref, authority=authority, include_image=include_image)

    async def close(self) -> None:
        self._closed = True
        if self._run is not None:
            self._run.stop()
            await self._lifecycle.close_run_session(
                CloseRunSessionRequest(
                    authority=TRUSTED_COMPUTER_USE_AUTHORITY,
                    run_session_id=self._run.run.run_session_id,
                )
            )
            self._run = None
