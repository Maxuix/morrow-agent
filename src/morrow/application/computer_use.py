"""Lazy desktop lifecycle, shared by local application surfaces and Core Host."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Awaitable, Callable

from morrow.application.computer_authorization import authorize_computer_execution
from morrow.core.computer_use import (
    COMPUTER_ACTION_TOOL,
    COMPUTER_OBSERVE_TOOL,
    TRUSTED_COMPUTER_USE_AUTHORITY,
    ActionOutcome,
    CloseRunSessionRequest,
    ComputerActionResult,
    ComputerUseAction,
    ComputerUseContractError,
    ComputerUseLifecyclePort,
    ComputerUsePreflight,
    ComputerUseRuntimeStatus,
    ComputerUseScope,
    ComputerUseSessionPort,
    DiscoverResult,
    LocalComputerUseCandidates,
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
from morrow.runtime.durable_log import durable_call_id
from morrow.services.computer_use import ComputerUseRunService
from morrow.services.computer_verification import evaluate_postcondition


class ComputerUseLifecycle:
    def __init__(
        self,
        owner_factory: Callable[[], ComputerUseLifecyclePort],
        diagnostic: Callable[[], ComputerUsePreflight],
        *,
        native_verified: bool = False,
        run_diagnostic: Callable[[ComputerUseSettings], ComputerUsePreflight] | None = None,
    ) -> None:
        self._factory = owner_factory
        self._diagnostic = diagnostic
        self._native_verified = native_verified
        self._run_diagnostic = run_diagnostic
        self._owner: ComputerUseLifecyclePort | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: int | None = None
        self._stopping = False
        self._failed = False

    @property
    def runtime_status(self) -> ComputerUseRuntimeStatus:
        if self._owner is None:
            return ComputerUseRuntimeStatus(
                state="closed" if self._stopping else "not_activated", native_pending=False
            )
        status = getattr(self._owner, "runtime_status", None)
        return (
            status
            if isinstance(status, ComputerUseRuntimeStatus)
            else ComputerUseRuntimeStatus(state="unknown", native_pending=None)
        )

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
        self._activate_owner(request.settings)
        return await self._owner.open_run_session(request)

    def _activate_owner(self, settings):
        diagnosis = (
            self._run_diagnostic(settings)
            if settings is not None and self._run_diagnostic is not None
            else self.preflight()
        )
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

    async def discover_local_candidates(self, settings, *, authority) -> LocalComputerUseCandidates:
        self._bind()
        reject_untrusted_computer_use_authority(authority)
        if self._stopping or self._failed:
            raise ComputerUseContractError("driver_not_activated")
        if not isinstance(settings, ComputerUseSettings) or not settings.enabled:
            raise ComputerUseContractError("disabled")
        self._activate_owner(settings)
        return await self._owner.discover_local_candidates(settings, authority=authority)

    def select_local_candidates(self, candidate_ids, *, authority):
        self._bind()
        reject_untrusted_computer_use_authority(authority)
        if self._stopping or self._failed or self._owner is None:
            raise ComputerUseContractError("driver_not_activated")
        return self._owner.select_local_candidates(candidate_ids, authority=authority)

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
        *,
        verification_wait: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ):
        self._verification_wait = verification_wait
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
                        settings=self._settings,
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

    def display_target(self, observation_id, context):
        """Safe label for the current published observation; never refreshes the device."""
        self.execution_for_context(context, tool_name=context.tool_name)
        return self._run.action_preview_target(observation_id) if self._run else None

    def action_preview(self, observation_id, action, context) -> tuple[str, ...]:
        """Local approval copy from this run's frozen scope and published target only."""
        run = self._journal.get_agent_run(self._scope.workspace_id, self._scope.agent_run_id)
        if (
            self._closed
            or context.tool_name != COMPUTER_ACTION_TOOL
            or context.run.run_id != self._scope.agent_run_id
            or run is None
            or context.run.session_id != run.session_id
        ):
            raise ComputerUseContractError("execution_not_authorized")
        target = self._run.action_preview_target(observation_id) if self._run else None
        from morrow.application.computer_permissions import computer_action_preview_lines

        return computer_action_preview_lines(self._scope, target, action)

    def execution_for_context(self, context, *, tool_name: str = COMPUTER_OBSERVE_TOOL) -> str:
        """Resolve the executing ledger row, never accept an execution ID from a model."""
        candidates = [
            execution
            for execution in self._journal.list_executions(
                self._scope.workspace_id, agent_run_id=self._scope.agent_run_id
            )
            if execution.call_id == durable_call_id(context.call_id)
            and execution.tool_name == tool_name
            and execution.session_id == context.run.session_id
            and execution.task_run_id == self._scope.task_run_id
            and execution.intent.ordinal == context.ordinal
        ]
        if (
            tool_name not in {COMPUTER_OBSERVE_TOOL, COMPUTER_ACTION_TOOL}
            or context.tool_name != tool_name
            or context.run.run_id != self._scope.agent_run_id
            or len(candidates) != 1
        ):
            raise ComputerUseContractError("execution_not_authorized")
        execution_id = candidates[0].tool_execution_id
        self._authority(execution_id, include_image=False, tool_name=tool_name)
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

    async def execute_published(
        self,
        execution_id: str,
        observation_id: str,
        action: ComputerUseAction,
        *,
        visuals,
    ) -> tuple[ComputerActionResult, tuple[ToolVisualRef, ...]]:
        try:
            self._authority(execution_id, include_image=False, tool_name=COMPUTER_ACTION_TOOL)
            if self._run is None:
                raise ComputerUseContractError("stale_observation")
            with self._run.observation_sequence():
                return await self._execute_published(
                    execution_id, observation_id, action, visuals=visuals
                )
        except ComputerUseContractError as exc:
            return ComputerActionResult(outcome=outcome_for_rejection(exc.code)), ()

    async def _execute_published(
        self,
        execution_id: str,
        observation_id: str,
        action: ComputerUseAction,
        *,
        visuals,
    ) -> tuple[ComputerActionResult, tuple[ToolVisualRef, ...]]:
        """Dispatch once, then observe the same target without erasing effects."""
        target_ref = None
        try:
            if self._run is not None:
                target_ref = self._run.target_for_observation(observation_id)
        except ComputerUseContractError:
            pass
        outcome = await self.execute_one(execution_id, observation_id, action)
        if outcome.status == "not_started":
            return ComputerActionResult(outcome=outcome), ()
        include_image = self._settings.mode is ComputerUseMode.HYBRID

        def authority():
            self._authority(
                execution_id, include_image=include_image, tool_name=COMPUTER_ACTION_TOOL
            )

        try:
            authority()
            if self._run is None or target_ref is None:
                raise ComputerUseContractError("stale_observation")
            read = await self._run.observe(
                target_ref, authority=authority, include_image=include_image
            )
            if read.observation.observation_id == observation_id:
                self._run.stop()
                raise ComputerUseContractError("stale_observation")
            predicate = action.postcondition
            verification = "not_checked"
            if predicate is not None:
                verification = evaluate_postcondition(read.observation, predicate)
                started = self._clock.now()
                seen = {observation_id, read.observation.observation_id}
                for _ in range(9):
                    if verification in {"passed", "not_checked"}:
                        break
                    elapsed = (self._clock.now() - started).total_seconds()
                    if elapsed < 0 or elapsed >= 5:
                        break
                    authority()
                    await self._verification_wait(min(0.5, 5 - elapsed))
                    authority()
                    remaining = 5 - (self._clock.now() - started).total_seconds()
                    if not 0 < remaining <= 5:
                        break
                    async with asyncio.timeout(remaining):
                        read = await self._run.observe(
                            target_ref, authority=authority, include_image=include_image
                        )
                    if read.observation.observation_id in seen:
                        self._run.stop()
                        raise ComputerUseContractError("stale_observation")
                    seen.add(read.observation.observation_id)
                    verification = evaluate_postcondition(read.observation, predicate)
            verification = "not_checked" if verification == "pending" else verification
            outcome = outcome.model_copy(update={"postcondition": verification})
        except TimeoutError:
            self._run.stop()
            return ComputerActionResult(
                outcome=outcome,
                observation_error="verification_timeout",
                verification_error="verification_unavailable",
            ), ()
        except ComputerUseContractError as exc:
            return ComputerActionResult(outcome=outcome, observation_error=exc.code), ()
        except asyncio.CancelledError:
            if self._run is not None:
                self._run.stop()
            raise
        except Exception:
            return ComputerActionResult(outcome=outcome, observation_error="observation_failed"), ()
        observation = read.observation
        verification_error = (
            "verification_failed"
            if outcome.postcondition == "failed"
            else "verification_unavailable"
            if action.postcondition is not None and outcome.postcondition == "not_checked"
            else None
        )
        outcome = outcome.model_copy(update={"after_observation_id": observation.observation_id})
        try:
            references = ()
            if read.capture is not None:
                authority()
                reference = visuals.publish_observed(
                    read,
                    tool_execution_id=execution_id,
                    scope=self._scope,
                    settings=self._settings,
                )
                authority()
                image = ObservationImageRef.model_validate(
                    reference.model_dump(include=set(ObservationImageRef.model_fields))
                )
                observation = observation.model_copy(update={"image": image})
                self._run.accept_published_observation(observation)
                references = (reference,)
            return ComputerActionResult(
                outcome=outcome, observation=observation, verification_error=verification_error
            ), references
        except ComputerUseContractError as exc:
            safe_observation = (
                None
                if exc.code
                in {
                    "grant_inactive",
                    "execution_cancelled",
                    "execution_not_authorized",
                    "driver_not_activated",
                    "run_budget",
                }
                else read.observation
            )
            return ComputerActionResult(
                outcome=outcome,
                observation=safe_observation,
                observation_error=exc.code,
                verification_error=verification_error,
            ), ()
        except Exception:
            # Opaque publisher errors are independent of an already dispatched action.
            return ComputerActionResult(
                outcome=outcome,
                observation=read.observation,
                observation_error="image_publish_failed",
                verification_error=verification_error,
            ), ()

    def stop_admission(self) -> None:
        self._closed = True
        if self._run is not None:
            self._run.stop()

    async def close(self) -> None:
        self.stop_admission()
        if self._run is not None:
            await self._lifecycle.close_run_session(
                CloseRunSessionRequest(
                    authority=TRUSTED_COMPUTER_USE_AUTHORITY,
                    run_session_id=self._run.run.run_session_id,
                )
            )
            self._run = None
