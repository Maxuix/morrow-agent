"""Trusted local selection staged before durable run subjects exist."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from morrow.core.computer_use import (
    MAX_APPS,
    ComputerUseAppIdentity,
    ComputerUseContractError,
    ComputerUseDelivery,
    ComputerUseImageShare,
    ComputerUseOperation,
    ComputerUseWindowBoundary,
    ComputerUseWindowIdentity,
    SelectedWindowScope,
)


class ComputerUseSelection(BaseModel):
    """Local interface input, never a model tool argument or stored grant."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    apps: tuple[ComputerUseAppIdentity, ...] = Field(min_length=1, max_length=MAX_APPS)
    windows: tuple[ComputerUseWindowIdentity, ...] = Field(default=(), max_length=100)
    operations: tuple[ComputerUseOperation, ...] = (
        ComputerUseOperation.OBSERVE,
        ComputerUseOperation.ACTION,
    )
    delivery: ComputerUseDelivery = ComputerUseDelivery.FOREGROUND
    image_share: ComputerUseImageShare = ComputerUseImageShare.NONE

    @model_validator(mode="after")
    def valid_range(self):
        if (
            len({app.bundle_id for app in self.apps}) != len(self.apps)
            or len(set(self.operations)) != len(self.operations)
            or ComputerUseOperation.OBSERVE not in self.operations
            or (
                self.windows
                and (
                    {item.app.bundle_id for item in self.windows}
                    != {app.bundle_id for app in self.apps}
                    or len({item.window_identity for item in self.windows}) != len(self.windows)
                )
            )
        ):
            raise ValueError("invalid_selection")
        return self

    def bind(self, *, workspace_id, task_run_id, agent_run_id, generation):
        if not self.windows:
            raise ComputerUseContractError("window_scope_required")
        scope = SelectedWindowScope(
            workspace_id=workspace_id,
            task_run_id=task_run_id,
            agent_run_id=agent_run_id,
            generation=generation,
            apps=self.apps,
            windows=tuple(sorted(self.windows, key=lambda item: item.window_identity)),
            operations=self.operations,
            window_boundary=ComputerUseWindowBoundary.WINDOW,
            delivery=self.delivery,
            image_share=self.image_share,
        )
        return scope


class ComputerObservationSurface(Protocol):
    """Stable object frozen tools hold, then bind once to a run service."""

    def display_target(self, observation_id: str, context: object): ...

    def action_preview(self, observation_id: str, action: object, context: object): ...

    def execution_for_context(self, context: object, **kwargs: object) -> str: ...

    async def discover(self, execution_id: str, *, bundle_id: str | None = None): ...

    async def observe_published(
        self,
        execution_id: str,
        target_ref: str,
        *,
        visuals: object,
        include_image: bool | None = None,
    ): ...

    async def execute_published(
        self, execution_id: str, observation_id: str, action: object, *, visuals: object
    ): ...

    def stop_admission(self) -> None: ...

    async def close(self) -> None: ...


@dataclass(frozen=True, slots=True, repr=False)
class LocalComputerUseRequest:
    selection: ComputerUseSelection
    session: object
    issuer: object = field(repr=False)

    def __repr__(self):
        return "LocalComputerUseRequest()"


class PendingComputerObservationService:
    """Frozen tools delegate only after local grant creation binds real subjects."""

    def __init__(self):
        self._service = None
        self._closed = False

    def assert_unbound(self):
        if self._closed or self._service is not None:
            raise ComputerUseContractError("execution_not_authorized")

    def bind(self, service):
        self.assert_unbound()
        self._service = service

    def _require_service(self):
        if self._closed or self._service is None:
            raise ComputerUseContractError("execution_not_authorized")
        return self._service

    def display_target(self, observation_id, context):
        return self._require_service().display_target(observation_id, context)

    def action_preview(self, observation_id, action, context):
        return self._require_service().action_preview(observation_id, action, context)

    def execution_for_context(self, context, **kwargs):
        return self._require_service().execution_for_context(context, **kwargs)

    async def discover(self, *args, **kwargs):
        return await self._require_service().discover(*args, **kwargs)

    async def observe_published(self, *args, **kwargs):
        return await self._require_service().observe_published(*args, **kwargs)

    async def execute_published(self, *args, **kwargs):
        return await self._require_service().execute_published(*args, **kwargs)

    def stop_admission(self):
        self._closed = True
        if self._service is not None:
            self._service.stop_admission()

    async def close(self):
        self.stop_admission()
        if self._service is not None:
            await self._service.close()
