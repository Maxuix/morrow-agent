"""Lazy desktop lifecycle, shared by local application surfaces and Core Host."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable

from morrow.core.computer_use import (
    CloseRunSessionRequest,
    ComputerUseContractError,
    ComputerUseLifecyclePort,
    ComputerUsePreflight,
    OpenRunSessionRequest,
    RunSession,
    reject_untrusted_computer_use_authority,
)


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

    def stop_admission(self) -> None:
        self._bind()
        self._stopping = True
        if self._owner is not None:
            self._owner.stop_admission()

    async def shutdown(self) -> None:
        self.stop_admission()
        if self._owner is not None:
            await self._owner.shutdown()
