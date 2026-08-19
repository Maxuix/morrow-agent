"""Ports consumed by application and runtime code."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

from morrow.core.artifacts import ArtifactMetadata
from morrow.core.models import (
    GlobalConfig,
    Message,
    ModelEvent,
    ModelRef,
    Profile,
    StateLoadResult,
    StateWriteResult,
    ToolApprovalDecision,
    ToolApprovalRequest,
    ToolDefinition,
    WorkspaceIndex,
    WorkspaceResolution,
)
from morrow.core.permissions import CapabilityGrant, PermissionSnapshot


class ModelProvider(Protocol):
    async def stream(
        self,
        model: ModelRef,
        messages: list[Message],
        tools: tuple[ToolDefinition, ...] = (),
    ) -> AsyncIterator[ModelEvent]: ...

    async def complete(self, model: ModelRef, messages: list[Message]) -> str: ...


class ApprovalPort(Protocol):
    """Asynchronous local approval boundary for generic tool execution."""

    async def request(self, request: ToolApprovalRequest) -> ToolApprovalDecision: ...


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

    def load_preferences_backup(self, workspace_id: str) -> StateLoadResult: ...

    def load_profile_backup(self, workspace_id: str) -> StateLoadResult: ...

    def write_preferences(
        self, workspace_id: str, value: Any, expected_revision: int | None = None
    ) -> StateWriteResult: ...

    def write_profile(
        self, workspace_id: str, value: Profile, expected_revision: int | None = None
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


class CapabilityGrantPort(Protocol):
    """Run-bound grant persistence; implementations must be workspace-scoped."""

    def put_capability_grant(
        self, workspace_id: str, grant: CapabilityGrant
    ) -> CapabilityGrant: ...

    def get_capability_grant(self, workspace_id: str, grant_id: str) -> CapabilityGrant | None: ...

    def list_capability_grants(
        self, workspace_id: str, *, agent_run_id: str | None = None
    ) -> tuple[CapabilityGrant, ...]: ...

    def save_capability_grant(
        self,
        workspace_id: str,
        grant: CapabilityGrant,
        *,
        expected_row_version: int,
    ) -> CapabilityGrant: ...


class PermissionSnapshotPort(Protocol):
    """Immutable permission evidence frozen once for one foreground AgentRun."""

    def get_permission_snapshot(
        self, workspace_id: str, permission_snapshot_id: str
    ) -> PermissionSnapshot | None: ...

    def get_permission_snapshot_for_run(
        self, workspace_id: str, agent_run_id: str
    ) -> PermissionSnapshot | None: ...

    def list_permission_snapshots(
        self, workspace_id: str, *, agent_run_id: str | None = None
    ) -> tuple[PermissionSnapshot, ...]: ...


class ArtifactByteStorePort(Protocol):
    """Managed byte publication; callers never provide a filesystem path."""

    def publish(self, metadata: ArtifactMetadata, content: bytes, *, faults=None) -> Path: ...

    def verify(self, metadata: ArtifactMetadata) -> None: ...

    def read(self, metadata: ArtifactMetadata, *, max_bytes: int) -> bytes: ...


class Adapter(Protocol):
    adapter_id: str

    def create(self, config: Any, credential: str) -> ModelProvider: ...
