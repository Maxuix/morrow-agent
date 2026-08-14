"""Ports consumed by application and runtime code."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

from morrow.core.models import (
    GlobalConfig,
    Handoff,
    Message,
    ModelEvent,
    ModelRef,
    Profile,
    StateLoadResult,
    StateWriteResult,
    WorkspaceIndex,
    WorkspaceResolution,
)


class ModelProvider(Protocol):
    async def stream(
        self, model: ModelRef, messages: list[Message]
    ) -> AsyncIterator[ModelEvent]: ...

    async def complete(self, model: ModelRef, messages: list[Message]) -> str: ...


class ProviderFactory(Protocol):
    def create(self, provider_id: str, config: Any, credential: str) -> ModelProvider: ...


class CredentialStore(Protocol):
    def get(self, ref: str) -> str | None: ...

    def set(self, ref: str, secret: str) -> None: ...

    def delete(self, ref: str) -> None: ...


class WorkspaceResolver(Protocol):
    def resolve(self, path: Path) -> WorkspaceResolution: ...


class GlobalConfigStore(Protocol):
    def load(self) -> StateLoadResult: ...

    def update(
        self,
        mutator: Callable[[GlobalConfig], GlobalConfig],
        expected_revision: int | None = None,
    ) -> StateWriteResult: ...


class WorkspaceIndexStore(Protocol):
    def load(self) -> StateLoadResult: ...

    def update(
        self,
        mutator: Callable[[WorkspaceIndex], WorkspaceIndex],
        expected_revision: int | None = None,
    ) -> StateWriteResult: ...

    def transact(
        self,
        mutator: Callable[[WorkspaceIndex], tuple[WorkspaceIndex | None, Any]],
    ) -> tuple[StateWriteResult, Any | None]: ...


class ProjectStateStore(Protocol):
    def load_preferences(self, workspace_id: str) -> StateLoadResult: ...

    def load_profile(self, workspace_id: str) -> StateLoadResult: ...

    def load_handoff(self, workspace_id: str) -> StateLoadResult: ...

    def load_preferences_backup(self, workspace_id: str) -> StateLoadResult: ...

    def load_profile_backup(self, workspace_id: str) -> StateLoadResult: ...

    def load_handoff_backup(self, workspace_id: str) -> StateLoadResult: ...

    def write_preferences(
        self, workspace_id: str, value: Any, expected_revision: int | None = None
    ) -> StateWriteResult: ...

    def write_profile(
        self, workspace_id: str, value: Profile, expected_revision: int | None = None
    ) -> StateWriteResult: ...

    def write_handoff(
        self, workspace_id: str, value: Handoff, expected_revision: int | None = None
    ) -> StateWriteResult: ...

    def clear_handoff(
        self, workspace_id: str, expected_revision: int | None = None
    ) -> StateWriteResult: ...

    def clear_profile(
        self, workspace_id: str, expected_revision: int | None = None
    ) -> StateWriteResult: ...

    def clear_preferences(
        self, workspace_id: str, expected_revision: int | None = None
    ) -> StateWriteResult: ...


class Clock(Protocol):
    def now(self) -> datetime: ...


class IdSource(Protocol):
    def new_id(self, prefix: str) -> str: ...


class Adapter(Protocol):
    adapter_id: str

    def create(self, config: Any, credential: str) -> ModelProvider: ...
