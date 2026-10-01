"""Lazy desktop lifecycle, shared by local application surfaces and Core Host."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable

from morrow.application.computer_authorization import authorize_computer_execution
from morrow.core.computer_use import (
    COMPUTER_ACTION_TOOL,
    COMPUTER_OBSERVE_TOOL,
    TRUSTED_COMPUTER_USE_AUTHORITY,
    ActionOutcome,
    CloseRunSessionRequest,
    ComputerUseAction,
    ComputerUseContractError,
    ComputerUseLifecyclePort,
    ComputerUsePreflight,
    ComputerUseScope,
    ComputerUseSessionPort,
    DiscoverResult,
    Observation,
    ObservationImageRef,
    ObservedWindow,
    OpenRunSessionRequest,
    RunSession,
    outcome_for_rejection,
    reject_untrusted_computer_use_authority,
)
from morrow.core.models import ToolVisualRef
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
        self._action_executions: set[str] = set()

    def _authority(
        self, execution_id: str, *, include_image: bool, tool_name: str = COMPUTER_OBSERVE_TOOL
    ):
        try:
            if self._closed:
                raise ComputerUseContractError("driver_not_activated")
            return authorize_computer_execution(
                self._journal,
                workspace_id=self._scope.workspace_id,
                execution_id=execution_id,
                scope=self._scope,
                tool_name=tool_name,
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

    def execution_for_context(self, context) -> str:
        """Resolve the executing ledger row, never accept an execution ID from a model."""
        candidates = [
            execution
            for execution in self._journal.list_executions(
                self._scope.workspace_id, agent_run_id=self._scope.agent_run_id
            )
            if execution.call_id == context.call_id
            and execution.tool_name == COMPUTER_OBSERVE_TOOL
            and execution.session_id == context.run.session_id
            and execution.task_run_id == self._scope.task_run_id
            and execution.intent.ordinal == context.ordinal
        ]
        if context.tool_name != COMPUTER_OBSERVE_TOOL or len(candidates) != 1:
            raise ComputerUseContractError("execution_not_authorized")
        execution_id = candidates[0].tool_execution_id
        self._authority(execution_id, include_image=False)
        return execution_id

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

    async def observe_published(
        self,
        execution_id: str,
        target_ref: str,
        *,
        visuals,
        include_image: bool | None = None,
    ) -> tuple[Observation, tuple[ToolVisualRef, ...]]:
        """Return safe durable DTOs; capture and mask coordinates stay transient."""
        read = await self.observe(execution_id, target_ref, include_image=include_image)
        if read.capture is None:
            return read.observation, ()
        self._authority(execution_id, include_image=True)
        reference = visuals.publish_observed(
            read,
            tool_execution_id=execution_id,
            scope=self._scope,
            settings=self._settings,
        )
        image = ObservationImageRef.model_validate(
            reference.model_dump(include=set(ObservationImageRef.model_fields))
        )
        observation = read.observation.model_copy(update={"image": image})
        assert self._run is not None
        self._run.accept_published_observation(observation)
        return observation, (reference,)

    async def execute_one(
        self, execution_id: str, observation_id: str, action: ComputerUseAction
    ) -> ActionOutcome:
        def authority():
            self._authority(execution_id, include_image=False, tool_name=COMPUTER_ACTION_TOOL)

        try:
            authority()
            if execution_id in self._action_executions:
                raise ComputerUseContractError("execution_already_used")
            self._action_executions.add(execution_id)
            if self._run is None:
                raise ComputerUseContractError("stale_observation")
            return await self._run.execute_one(observation_id, action, authority=authority)
        except ComputerUseContractError as exc:
            return outcome_for_rejection(str(exc))

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
