"""A frozen run's bounded desktop observations; authority is injected by application."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import contextmanager

from morrow.core.computer_use import (
    MAX_DISCOVERED_TARGETS,
    MAX_OBSERVATION_AGE_SECONDS,
    TRUSTED_COMPUTER_USE_AUTHORITY,
    ActionOutcome,
    ComputerUseAction,
    ComputerUseContractError,
    ComputerUseOperation,
    ComputerUseScope,
    ComputerUseSessionPort,
    DiscoverRequest,
    DiscoverResult,
    ExecuteRequest,
    Observation,
    ObservedWindow,
    ObserveWindowRequest,
    RunSession,
    TargetRef,
    images_allowed,
    outcome_for_rejection,
    prepare_execute_request,
)
from morrow.core.ports import Clock
from morrow.core.runtime_policy import ComputerUseMode, ComputerUseSettings


class ComputerUseRunService:
    """No queue, permission grant, history writer or native handle lives here."""

    def __init__(
        self,
        session: ComputerUseSessionPort,
        run: RunSession,
        scope: ComputerUseScope,
        settings: ComputerUseSettings,
        clock: Clock,
    ) -> None:
        if run.agent_run_id != scope.agent_run_id or run.generation != scope.generation:
            raise ComputerUseContractError("subject_mismatch")
        if not settings.enabled:
            raise ComputerUseContractError("disabled")
        self.session, self._run, self._scope, self._settings, self.clock = (
            session,
            run,
            scope,
            settings,
            clock,
        )
        self._started_at = clock.now()
        self._operations = 0
        self._busy = False
        self._sequence_owner: asyncio.Task | None = None
        self._stopped = False
        self._targets: dict[str, TargetRef] = {}
        self._observations: dict[str, Observation] = {}

    @property
    def run(self) -> RunSession:
        return self._run

    @property
    def scope(self) -> ComputerUseScope:
        return self._scope

    @property
    def settings(self) -> ComputerUseSettings:
        return self._settings

    def __repr__(self) -> str:
        return f"ComputerUseRunService(operations={self._operations}, stopped={self._stopped})"

    def stop(self) -> None:
        self._stopped = True
        self._targets.clear()
        self._observations.clear()
        self.session.invalidate()

    def _admit(self, authority: Callable[[], None], *, include_image: bool = False) -> None:
        authority()
        if self._stopped:
            raise ComputerUseContractError("driver_not_activated")
        if self._busy or (
            self._sequence_owner is not None and self._sequence_owner is not asyncio.current_task()
        ):
            raise ComputerUseContractError("desktop_busy")
        elapsed = (self.clock.now() - self._started_at).total_seconds()
        if elapsed < 0 or elapsed >= self.settings.max_run_seconds:
            self.stop()
            raise ComputerUseContractError("run_budget")
        if self._operations >= self.settings.max_operations:
            self.stop()
            raise ComputerUseContractError("operation_budget")
        if ComputerUseOperation.OBSERVE not in self.scope.operations:
            raise ComputerUseContractError("operation_not_granted")
        if include_image and not images_allowed(self.settings, self.scope):
            raise ComputerUseContractError("images_not_allowed")

    @contextmanager
    def observation_sequence(self):
        """Reserve admission across a single action and its bounded read polling."""
        if self._busy or self._sequence_owner is not None:
            raise ComputerUseContractError("desktop_busy")
        owner = asyncio.current_task()
        self._sequence_owner = owner
        try:
            yield
        finally:
            self._sequence_owner = None

    def _begin(self) -> None:
        self._operations += 1
        self._busy = True

    def _finish(self, authority: Callable[[], None]) -> None:
        try:
            authority()
            elapsed = (self.clock.now() - self._started_at).total_seconds()
            if self._stopped or elapsed < 0 or elapsed >= self.settings.max_run_seconds:
                raise ComputerUseContractError("run_budget")
        except BaseException:
            self.stop()
            raise

    async def discover(
        self, *, authority: Callable[[], None], bundle_id: str | None = None
    ) -> DiscoverResult:
        self._admit(authority)
        allowed = {app.bundle_id for app in self.scope.apps}
        if bundle_id is not None and bundle_id not in allowed:
            raise ComputerUseContractError("app_not_granted")
        self._begin()
        # Discovery retires previous observations, even if the read then fails.
        self._targets.clear()
        self._observations.clear()
        try:
            result = await self.session.discover(
                DiscoverRequest(
                    authority=TRUSTED_COMPUTER_USE_AUTHORITY,
                    scope=self.scope,
                    run_session_id=self.run.run_session_id,
                    bundle_id=bundle_id,
                )
            )
            self._finish(authority)
            if len(result.targets) > MAX_DISCOVERED_TARGETS:
                raise ComputerUseContractError("target_budget")
            targets = {}
            for target in result.targets:
                if (
                    target.agent_run_id != self.scope.agent_run_id
                    or target.generation != self.scope.generation
                    or target.app.bundle_id not in allowed
                    or (bundle_id is not None and target.app.bundle_id != bundle_id)
                    or target.target_ref in targets
                    or (
                        self.scope.schema_version == 2
                        and not any(
                            item.app == target.app
                            and item.window_identity == target.window_identity
                            for item in self.scope.windows
                        )
                    )
                ):
                    raise ComputerUseContractError("subject_mismatch")
                targets[target.target_ref] = target
            self._targets = targets
            return result
        except asyncio.CancelledError:
            self.stop()
            raise
        finally:
            self._busy = False

    async def observe(
        self, target_ref: str, *, authority: Callable[[], None], include_image: bool | None = None
    ) -> ObservedWindow:
        include_image = (
            self.settings.mode is ComputerUseMode.HYBRID if include_image is None else include_image
        )
        self._admit(authority, include_image=include_image)
        target = self._targets.get(target_ref)
        if target is None:
            raise ComputerUseContractError("unknown_target")
        self._begin()
        self._observations.pop(target_ref, None)
        try:
            read = await self.session.observe(
                ObserveWindowRequest(
                    authority=TRUSTED_COMPUTER_USE_AUTHORITY,
                    scope=self.scope,
                    target=target,
                    delivery=self.scope.delivery,
                    include_image=include_image,
                ),
                settings=self.settings,
            )
            self._finish(authority)
            observation = read.observation
            if (
                observation.target_ref != target.target_ref
                or observation.agent_run_id != target.agent_run_id
                or observation.generation != target.generation
                or observation.bundle_id != target.app.bundle_id
                or observation.process_identity != target.process_identity
                or observation.window_identity != target.window_identity
                or observation.image is not None
            ):
                raise ComputerUseContractError("subject_mismatch")
            if include_image and read.image_error is not None:
                raise ComputerUseContractError(read.image_error)
            if include_image and read.capture is None:
                raise ComputerUseContractError(read.image_error or "image_missing")
            if read.capture is not None and (
                max(read.capture.width, read.capture.height) > self.settings.image_long_edge_px
            ):
                raise ComputerUseContractError("image_budget")
            if not include_image and read.capture is not None:
                raise ComputerUseContractError("images_not_allowed")
            self._observations[target_ref] = observation
            return read
        except asyncio.CancelledError:
            self.stop()
            raise
        finally:
            self._busy = False

    def target_for_observation(self, observation_id: str) -> str:
        """Resolve a current opaque target before its observation is consumed."""
        for observation in self._observations.values():
            if observation.observation_id == observation_id:
                return observation.target_ref
        raise ComputerUseContractError("stale_observation")

    def action_preview_target(self, observation_id: str) -> TargetRef | None:
        """Read only an already-published local target; never refresh or consume it."""
        if self._stopped:
            return None
        for observation in self._observations.values():
            if observation.observation_id == observation_id:
                age = (self.clock.now() - observation.captured_at).total_seconds()
                if 0 <= age < MAX_OBSERVATION_AGE_SECONDS:
                    return self._targets.get(observation.target_ref)
        return None

    def accept_published_observation(self, observation: Observation) -> None:
        """Attach only the safely published image to the current transient read."""
        current = self._observations.get(observation.target_ref)
        if current is None or observation.model_copy(update={"image": None}) != current:
            raise ComputerUseContractError("stale_observation")
        self._observations[observation.target_ref] = observation

    async def execute_one(
        self,
        observation_id: str,
        action: ComputerUseAction,
        *,
        authority: Callable[[], None],
    ) -> ActionOutcome:
        """Consume exactly one current observation; never refresh and replay an action."""
        try:
            self._admit(authority)
            observation = next(
                (
                    item
                    for item in self._observations.values()
                    if item.observation_id == observation_id
                ),
                None,
            )
            if observation is None:
                raise ComputerUseContractError("stale_observation")
            age = (self.clock.now() - observation.captured_at).total_seconds()
            if not 0 <= age < MAX_OBSERVATION_AGE_SECONDS:
                self._observations.pop(observation.target_ref, None)
                raise ComputerUseContractError("stale_observation")
            target = self._targets[observation.target_ref]
            request = ExecuteRequest(
                authority=TRUSTED_COMPUTER_USE_AUTHORITY,
                scope=self.scope,
                target=target,
                observation=observation,
                action=action,
                delivery=self.scope.delivery,
            )
            prepare_execute_request(request, settings=self.settings)
            if getattr(action, "x", None) is not None and observation.image is None:
                raise ComputerUseContractError("image_not_published")
        except ComputerUseContractError as exc:
            return outcome_for_rejection(str(exc))
        self._begin()
        # Consumption precedes native preflight/await, including refusal and cancellation.
        self._observations.pop(observation.target_ref, None)
        try:
            outcome = await self.session.execute_one(
                request, settings=self.settings, authority=authority
            )
            if outcome.delivery is not None and outcome.delivery is not self.scope.delivery:
                outcome = outcome.model_copy(
                    update={
                        "status": "unknown",
                        "error_code": "unexpected_delivery",
                    }
                )
            try:
                self._finish(authority)
            except ComputerUseContractError as exc:
                # Revocation/deadline after dispatch stops admission; effects survive.
                if outcome.error_code is None:
                    outcome = outcome.model_copy(update={"error_code": str(exc)})
            return outcome.model_copy(update={"before_observation_id": observation_id})
        except ComputerUseContractError as exc:
            return outcome_for_rejection(str(exc)).model_copy(
                update={"before_observation_id": observation_id}
            )
        except asyncio.CancelledError:
            self.stop()
            raise
        finally:
            self._busy = False
