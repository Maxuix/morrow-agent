"""Ports consumed by application and runtime code."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

from morrow.core.artifacts import ArtifactMetadata
from morrow.core.models import (
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
from morrow.core.preference_documents import GlobalConfig
from morrow.core.preference_models import PreferenceOperation
from morrow.core.preference_persistence_models import (
    PreferenceEvidence,
    PreferenceProposal,
    PreferenceProposalStatus,
    PreferenceReviewJob,
    PreferenceReviewJobStatus,
    PreferenceWriteBatch,
    PreferenceWriteBatchStatus,
)


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


class PreferencePersistencePort(Protocol):
    """Bounded v13 persistence surface; YAML remains the Active authority."""

    def put_preference_job_with_evidence(
        self, workspace_id: str, job: PreferenceReviewJob, evidence: PreferenceEvidence
    ) -> tuple[PreferenceReviewJob, PreferenceEvidence]: ...

    def put_preference_review_job(
        self, workspace_id: str, job: PreferenceReviewJob
    ) -> PreferenceReviewJob: ...

    def put_preference_evidence(
        self, workspace_id: str, evidence: PreferenceEvidence
    ) -> PreferenceEvidence: ...

    def get_preference_evidence_for_job(
        self, workspace_id: str, job_id: str
    ) -> PreferenceEvidence | None: ...

    def get_preference_review_job(
        self, workspace_id: str, job_id: str
    ) -> PreferenceReviewJob | None: ...

    def get_preference_review_job_for_turn(
        self, workspace_id: str, turn_id: str, *, review_version: int = 1
    ) -> PreferenceReviewJob | None: ...

    def list_claimable_preference_review_jobs(
        self, workspace_id: str, *, limit: int = 100
    ) -> tuple[PreferenceReviewJob, ...]: ...

    def save_preference_review_job(
        self,
        workspace_id: str,
        job: PreferenceReviewJob,
        *,
        expected_row_version: int,
    ) -> PreferenceReviewJob: ...

    def claim_preference_review_job(
        self,
        workspace_id: str,
        job_id: str,
        *,
        expected_row_version: int,
        lease_id: str,
        lease_expires_at: datetime,
        started_at: datetime,
    ) -> PreferenceReviewJob: ...

    def list_preference_review_jobs(
        self,
        workspace_id: str,
        *,
        status: PreferenceReviewJobStatus | None = None,
        limit: int = 100,
    ) -> tuple[PreferenceReviewJob, ...]: ...

    def put_preference_proposal(
        self, workspace_id: str, proposal: PreferenceProposal
    ) -> PreferenceProposal: ...

    def get_preference_proposal(
        self, workspace_id: str, proposal_id: str
    ) -> PreferenceProposal | None: ...

    def list_preference_proposals(
        self,
        workspace_id: str,
        *,
        status: PreferenceProposalStatus | None = None,
        job_id: str | None = None,
        limit: int = 100,
    ) -> tuple[PreferenceProposal, ...]: ...

    def has_preference_proposal_fingerprint(
        self,
        workspace_id: str,
        fingerprint: str,
        *,
        status: PreferenceProposalStatus | None = None,
    ) -> bool: ...

    def finalize_preference_proposals(
        self,
        workspace_id: str,
        proposal_ids: tuple[str, ...],
        *,
        command_id: str,
        operations: tuple[PreferenceOperation, ...],
        resolved_at: datetime,
    ) -> tuple[PreferenceProposal, ...]: ...

    def save_preference_proposal(
        self,
        workspace_id: str,
        proposal: PreferenceProposal,
        *,
        expected_row_version: int,
    ) -> PreferenceProposal: ...

    def put_preference_write_batch(
        self, workspace_id: str, batch: PreferenceWriteBatch
    ) -> PreferenceWriteBatch: ...

    def get_preference_write_batch(
        self, workspace_id: str, batch_id: str
    ) -> PreferenceWriteBatch | None: ...

    def get_preference_write_batch_by_command(
        self, workspace_id: str, command_id: str
    ) -> PreferenceWriteBatch | None: ...

    def save_preference_write_batch(
        self,
        workspace_id: str,
        batch: PreferenceWriteBatch,
        *,
        expected_row_version: int,
    ) -> PreferenceWriteBatch: ...

    def list_preference_write_batches(
        self,
        workspace_id: str,
        *,
        status: PreferenceWriteBatchStatus | None = None,
        limit: int = 100,
    ) -> tuple[PreferenceWriteBatch, ...]: ...


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

    def read(self, metadata: ArtifactMetadata, *, max_bytes: int, start_byte: int = 0) -> bytes: ...


class Adapter(Protocol):
    adapter_id: str

    def create(self, config: Any, credential: str) -> ModelProvider: ...
