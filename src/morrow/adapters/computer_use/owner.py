"""Loop-affine SDK lifecycle; deliberately not registered in production yet.

A trusted composition must supply the session factory. Desktop lease and native
acceptance are prerequisites for wiring this owner into a running application.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable
from typing import Any

from morrow.adapters.computer_use.registry import TrustedDesktopRegistry
from morrow.adapters.computer_use.sdk_loader import construct_driver
from morrow.adapters.computer_use.session import TypedComputerSession
from morrow.core.computer_use import (
    TRUSTED_COMPUTER_USE_AUTHORITY,
    CloseRunSessionRequest,
    ComputerUseContractError,
    OpenRunSessionRequest,
    RunSession,
    reject_untrusted_computer_use_authority,
)
from morrow.core.domain import COMPUTER_RUN_ID_PREFIX
from morrow.core.ports import Clock, IdSource


class ComputerDriverOwner:
    """Create/use/close native resources exclusively on the constructing loop.

    There is no thread dispatch, implicit activation, or second driver on error.
    Failed lifecycle operations quarantine this owner until application shutdown.
    """

    def __init__(
        self,
        sdk: Any,
        ids: IdSource,
        clock: Clock,
        *,
        session_factory: Callable[[Any, str], Any],
        driver_factory: Callable[[Any], Any] = construct_driver,
    ) -> None:
        self._loop = asyncio.get_running_loop()
        self._thread = threading.get_ident()
        self._sdk = sdk
        self._ids = ids
        self._clock = clock
        self._session_factory = session_factory
        self._driver = driver_factory(sdk)
        self._session: TypedComputerSession | None = None
        self._run: RunSession | None = None
        self._generations: dict[str, int] = {}
        self._transitioning = False
        self._closed = False
        self._stopping = False
        self._quarantined = False

    def __repr__(self) -> str:
        return (
            f"ComputerDriverOwner(active={self._run is not None}, "
            f"closed={self._closed}, quarantined={self._quarantined})"
        )

    def _check_owner(self) -> None:
        if asyncio.get_running_loop() is not self._loop or threading.get_ident() != self._thread:
            raise ComputerUseContractError("owner_mismatch")

    def _admit(self) -> None:
        self._check_owner()
        if self._closed or self._stopping or self._quarantined:
            raise ComputerUseContractError("driver_not_activated")
        if self._transitioning:
            raise ComputerUseContractError("desktop_busy")

    async def open_run_session(self, request: OpenRunSessionRequest) -> RunSession:
        self._admit()
        reject_untrusted_computer_use_authority(request.authority)
        if request.agent_run_id != request.scope.agent_run_id:
            raise ComputerUseContractError("subject_mismatch")
        if self._run is not None:
            raise ComputerUseContractError("desktop_busy")
        if request.scope.generation <= self._generations.get(request.agent_run_id, 0):
            raise ComputerUseContractError("stale_observation")
        self._transitioning = True
        native = None
        try:
            name = self._ids.new_id(COMPUTER_RUN_ID_PREFIX)
            native = self._session_factory(self._driver, name)
            session = TypedComputerSession(
                self._sdk,
                native,
                TrustedDesktopRegistry(self._ids),
                self._ids,
                self._clock,
                session_name=name,
            )
            run = await session.open_run_session(request)
            self._session, self._run = session, run
            self._generations[request.agent_run_id] = request.scope.generation
            return run
        except BaseException as exc:
            self._quarantined = True
            if native is not None:
                try:
                    native.close()
                except Exception:
                    pass
            if isinstance(exc, asyncio.CancelledError):
                raise
            raise ComputerUseContractError("driver_error") from None
        finally:
            self._transitioning = False

    def session_for(self, run: RunSession) -> TypedComputerSession:
        self._admit()
        if run != self._run or self._session is None:
            raise ComputerUseContractError("subject_mismatch")
        return self._session

    async def close_run_session(self, request: CloseRunSessionRequest) -> None:
        self._admit()
        reject_untrusted_computer_use_authority(request.authority)
        if self._run is None or request.run_session_id != self._run.run_session_id:
            raise ComputerUseContractError("subject_mismatch")
        assert self._session is not None
        self._transitioning = True
        try:
            await self._session.close_run_session(request)
            self._session, self._run = None, None
        except BaseException as exc:
            self._quarantined = True
            if isinstance(exc, asyncio.CancelledError):
                raise
            raise ComputerUseContractError("driver_error") from None
        finally:
            self._transitioning = False

    async def shutdown(self) -> None:
        self._check_owner()
        if self._closed:
            return
        if self._transitioning:
            raise ComputerUseContractError("desktop_busy")
        self._stopping = True
        self._transitioning = True
        try:
            if self._run is not None and self._session is not None:
                try:
                    await self._session.close_run_session(
                        CloseRunSessionRequest(
                            authority=TRUSTED_COMPUTER_USE_AUTHORITY,
                            run_session_id=self._run.run_session_id,
                        )
                    )
                except Exception:
                    self._quarantined = True
            # Native shutdown is the settling boundary, including failed start/end.
            await self._driver.shutdown()
            self._session, self._run = None, None
            self._closed = True
        except BaseException as exc:
            self._quarantined = True
            if isinstance(exc, asyncio.CancelledError):
                raise
            raise ComputerUseContractError("driver_error") from None
        finally:
            self._transitioning = False
