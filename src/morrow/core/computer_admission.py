"""Pure desktop admission. These values are the only way to reach a device port."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol

from morrow.core.computer_actions import (
    ActionOutcome,
    ExecuteRequest,
    PreparedComputerAction,
    prepare_execute_request,
)
from morrow.core.computer_use import (
    CloseRunSessionRequest,
    ComputerUseContractError,
    ComputerUseImageShare,
    ComputerUseOperation,
    ComputerUseRuntimeStatus,
    ComputerUseWindowIdentity,
    DiscoverRequest,
    DiscoverResult,
    LocalComputerUseCandidates,
    ObservedWindow,
    ObserveWindowRequest,
    OpenRunSessionRequest,
    RunSession,
    images_allowed,
    reject_untrusted_computer_use_authority,
    scope_grants_window,
)
from morrow.core.runtime_policy import ComputerUseSettings

_ADMITTED = object()


@dataclass(frozen=True, slots=True)
class AdmittedDiscover:
    request: DiscoverRequest
    _token: object = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        if self._token is not _ADMITTED:
            raise ComputerUseContractError("rejected_action")


@dataclass(frozen=True, slots=True)
class AdmittedObserve:
    request: ObserveWindowRequest
    settings: ComputerUseSettings
    _token: object = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        if self._token is not _ADMITTED:
            raise ComputerUseContractError("rejected_action")


@dataclass(frozen=True, slots=True)
class AdmittedExecute:
    request: ExecuteRequest
    prepared: PreparedComputerAction
    _token: object = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        if self._token is not _ADMITTED:
            raise ComputerUseContractError("rejected_action")


def admit_discover(request: DiscoverRequest) -> AdmittedDiscover:
    """Check authority, app, selected scope, and observe permission. No device call."""

    reject_untrusted_computer_use_authority(request.authority)
    if request.bundle_id is not None and request.bundle_id not in {
        item.bundle_id for item in request.scope.apps
    }:
        raise ComputerUseContractError("app_not_granted")
    if ComputerUseOperation.OBSERVE not in request.scope.operations:
        raise ComputerUseContractError("operation_not_granted")
    return AdmittedDiscover(request, _token=_ADMITTED)


def admit_observe(
    request: ObserveWindowRequest,
    *,
    settings: ComputerUseSettings,
) -> AdmittedObserve:
    """Check the frozen observe request. The caller still revalidates before dispatch."""

    resolved = settings
    reject_untrusted_computer_use_authority(request.authority)
    if request.target.agent_run_id != request.scope.agent_run_id:
        raise ComputerUseContractError("subject_mismatch")
    if request.target.generation != request.scope.generation:
        raise ComputerUseContractError("stale_observation")
    if request.target.app.bundle_id not in {item.bundle_id for item in request.scope.apps}:
        raise ComputerUseContractError("app_not_granted")
    if not scope_grants_window(request.scope, request.target.app, request.target.window_identity):
        raise ComputerUseContractError("window_not_granted")
    if ComputerUseOperation.OBSERVE not in request.scope.operations:
        raise ComputerUseContractError("operation_not_granted")
    if request.delivery is not request.scope.delivery:
        raise ComputerUseContractError("delivery_not_granted")
    if request.include_image and not images_allowed(resolved, request.scope):
        if request.scope.image_share is not ComputerUseImageShare.CONTROLLED_WINDOW:
            raise ComputerUseContractError("image_share_not_granted")
        raise ComputerUseContractError("images_not_allowed")
    return AdmittedObserve(request, resolved, _token=_ADMITTED)


def admit_execute(
    request: ExecuteRequest,
    *,
    settings: ComputerUseSettings | None = None,
) -> AdmittedExecute:
    """Map one action. Image publication is required only after a coordinate maps."""

    prepared = prepare_execute_request(request, settings=settings)
    if prepared.window_point is not None and request.observation.image is None:
        raise ComputerUseContractError("image_not_published")
    return AdmittedExecute(request, prepared, _token=_ADMITTED)


class ComputerUseSessionPort(Protocol):
    """Run-bound asynchronous device surface, with no SDK objects or native ids."""

    async def discover(self, admitted: AdmittedDiscover) -> DiscoverResult: ...

    async def observe(self, admitted: AdmittedObserve) -> ObservedWindow: ...

    async def execute_one(
        self,
        admitted: AdmittedExecute,
        *,
        authority: Callable[[], None],
    ) -> ActionOutcome: ...

    def invalidate(self) -> None: ...


class ComputerUseLifecyclePort(Protocol):
    """Async lifecycle on the runtime owner; no native handle crosses this port."""

    @property
    def runtime_status(self) -> ComputerUseRuntimeStatus: ...

    @property
    def shutdown_pending(self) -> bool: ...

    def stop_admission(self) -> None: ...

    async def discover_local_candidates(
        self, settings: ComputerUseSettings, *, authority: str
    ) -> LocalComputerUseCandidates: ...

    def select_local_candidates(
        self, candidate_ids: tuple[str, ...], *, authority: str
    ) -> tuple[ComputerUseWindowIdentity, ...]: ...

    async def open_run_session(self, request: OpenRunSessionRequest) -> RunSession: ...

    async def close_run_session(self, request: CloseRunSessionRequest) -> None: ...

    def session_for(self, run: RunSession) -> ComputerUseSessionPort: ...

    async def shutdown(self) -> None: ...
