"""Narrow lifecycle and conversation-journal ports for the Operational Store."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Protocol, Self, TypeVar

from morrow.core.application import ApplicationCommandReceipt, ApplicationEvent
from morrow.core.artifacts import ArtifactMetadata
from morrow.core.context import ContextCheckpoint, SessionLineage
from morrow.core.domain import (
    ArtifactReference,
    DurableAgentRun,
    DurableConversationRecord,
    DurableSession,
    DurableTaskOutcome,
    DurableTaskRun,
    DurableTaskRunTransition,
    DurableTurn,
    TaskCommandReceipt,
    TaskRunStatus,
    TurnSubmitReceipt,
)
from morrow.core.execution import DurableApproval, DurableToolExecution
from morrow.core.permissions import CapabilityGrant, PermissionSnapshot
from morrow.core.recovery import RecoveryReceipt, RecoveryReport

T = TypeVar("T")


class TransactionalJournalPort(Protocol):
    """Run work against the same transaction-scoped journal implementation."""

    def transact(self, work: Callable[[Self], T]) -> T: ...


class SessionLifecyclePort(Protocol):
    def create_session(
        self, session: DurableSession, *, task: DurableTaskRun | None = None
    ) -> DurableSession: ...

    def get_session(self, workspace_id: str, session_id: str) -> DurableSession | None: ...

    def list_sessions(self, workspace_id: str) -> tuple[DurableSession, ...]: ...

    def save_session(self, workspace_id: str, session: DurableSession) -> DurableSession: ...

    def get_session_lineage(self, workspace_id: str, session_id: str) -> SessionLineage | None: ...

    def create_fork_session(
        self, session: DurableSession, *, lineage: SessionLineage
    ) -> DurableSession: ...

    def get_task_run(self, workspace_id: str, task_run_id: str) -> DurableTaskRun | None: ...

    def create_task_run(self, workspace_id: str, task: DurableTaskRun) -> DurableTaskRun: ...

    def list_task_runs(self, workspace_id: str, session_id: str) -> tuple[DurableTaskRun, ...]: ...

    def list_task_turns(self, workspace_id: str, task_run_id: str) -> tuple[DurableTurn, ...]: ...

    def list_session_turns(self, workspace_id: str, session_id: str) -> tuple[DurableTurn, ...]: ...

    def transition_task_run(
        self,
        workspace_id: str,
        task_run_id: str,
        *,
        target: TaskRunStatus,
        transition: DurableTaskRunTransition,
        expected_row_version: int,
    ) -> DurableTaskRun: ...

    def list_task_transitions(
        self, workspace_id: str, task_run_id: str
    ) -> tuple[DurableTaskRunTransition, ...]: ...

    def put_task_outcome(
        self, workspace_id: str, outcome: DurableTaskOutcome
    ) -> DurableTaskOutcome: ...

    def get_task_outcome(self, workspace_id: str, outcome_id: str) -> DurableTaskOutcome | None: ...

    def list_task_outcomes(
        self, workspace_id: str, task_run_id: str
    ) -> tuple[DurableTaskOutcome, ...]: ...

    def get_task_command_receipt(
        self, workspace_id: str, command_id: str
    ) -> TaskCommandReceipt | None: ...

    def put_task_command_receipt(
        self, workspace_id: str, receipt: TaskCommandReceipt
    ) -> TaskCommandReceipt: ...


class ConversationJournalPort(Protocol):
    def append_records(
        self, workspace_id: str, records: Sequence[DurableConversationRecord]
    ) -> DurableSession: ...

    def load_records(
        self, workspace_id: str, session_id: str
    ) -> tuple[DurableConversationRecord, ...]: ...

    def load_effective_records(
        self, workspace_id: str, session_id: str
    ) -> tuple[DurableConversationRecord, ...]: ...

    def put_context_checkpoint(
        self, workspace_id: str, checkpoint: ContextCheckpoint
    ) -> ContextCheckpoint: ...

    def get_context_checkpoint(
        self, workspace_id: str, checkpoint_id: str
    ) -> ContextCheckpoint | None: ...

    def list_context_checkpoints(
        self, workspace_id: str, session_id: str, *, task_run_id: str | None = None
    ) -> tuple[ContextCheckpoint, ...]: ...


class ApplicationEventPort(Protocol):
    def get_application_event(
        self, workspace_id: str, event_id: str
    ) -> ApplicationEvent | None: ...

    def list_application_events(
        self, workspace_id: str, *, after_cursor: int = 0, limit: int = 100
    ) -> tuple[ApplicationEvent, ...]: ...

    def put_application_event(
        self, workspace_id: str, event: ApplicationEvent
    ) -> ApplicationEvent: ...

    def put_application_event_in_txn(
        self, workspace_id: str, event: ApplicationEvent
    ) -> ApplicationEvent: ...

    def get_application_command_receipt(
        self, workspace_id: str, command_id: str
    ) -> ApplicationCommandReceipt | None: ...

    def put_application_command_receipt(
        self, workspace_id: str, receipt: ApplicationCommandReceipt
    ) -> ApplicationCommandReceipt: ...

    def put_application_command_receipt_in_txn(
        self, workspace_id: str, receipt: ApplicationCommandReceipt
    ) -> ApplicationCommandReceipt: ...


class AgentRunPort(Protocol):
    def create_turn(self, workspace_id: str, turn: DurableTurn) -> DurableTurn: ...

    def get_turn(self, workspace_id: str, turn_id: str) -> DurableTurn | None: ...

    def create_agent_run(self, workspace_id: str, run: DurableAgentRun) -> DurableAgentRun: ...

    def get_agent_run(self, workspace_id: str, agent_run_id: str) -> DurableAgentRun | None: ...

    def list_session_agent_runs(
        self, workspace_id: str, session_id: str
    ) -> tuple[DurableAgentRun, ...]: ...

    def get_permission_snapshot(
        self, workspace_id: str, permission_snapshot_id: str
    ) -> PermissionSnapshot | None: ...

    def get_permission_snapshot_for_run(
        self, workspace_id: str, agent_run_id: str
    ) -> PermissionSnapshot | None: ...

    def list_permission_snapshots(
        self, workspace_id: str, *, agent_run_id: str | None = None
    ) -> tuple[PermissionSnapshot, ...]: ...

    def freeze_agent_run_permission_snapshot(
        self,
        workspace_id: str,
        agent_run_id: str,
        permission_snapshot: PermissionSnapshot,
    ) -> DurableAgentRun: ...


class TurnSubmitReceiptPort(Protocol):
    def get_receipt(
        self, workspace_id: str, session_id: str, client_message_id: str
    ) -> TurnSubmitReceipt | None: ...

    def put_receipt(self, workspace_id: str, receipt: TurnSubmitReceipt) -> TurnSubmitReceipt: ...


class ToolExecutionJournalPort(Protocol):
    def put_execution(
        self, workspace_id: str, execution: DurableToolExecution
    ) -> DurableToolExecution: ...

    def get_execution(
        self, workspace_id: str, tool_execution_id: str
    ) -> DurableToolExecution | None: ...

    def list_executions(
        self, workspace_id: str, *, agent_run_id: str
    ) -> tuple[DurableToolExecution, ...]: ...

    def list_session_executions(
        self, workspace_id: str, session_id: str
    ) -> tuple[DurableToolExecution, ...]: ...

    def list_task_executions(
        self, workspace_id: str, task_run_id: str
    ) -> tuple[DurableToolExecution, ...]: ...

    def save_execution(
        self,
        workspace_id: str,
        execution: DurableToolExecution,
        *,
        expected_row_version: int,
    ) -> DurableToolExecution: ...

    def list_executions_for_grant(
        self, workspace_id: str, grant_id: str
    ) -> tuple[DurableToolExecution, ...]: ...


class ApprovalJournalPort(Protocol):
    def put_approval(self, workspace_id: str, approval: DurableApproval) -> DurableApproval: ...

    def get_approval(self, workspace_id: str, approval_id: str) -> DurableApproval | None: ...

    def get_approval_for_execution(
        self, workspace_id: str, tool_execution_id: str
    ) -> DurableApproval | None: ...

    def save_approval(
        self,
        workspace_id: str,
        approval: DurableApproval,
        *,
        expected_row_version: int,
    ) -> DurableApproval: ...

    def list_approvals_for_grant(
        self, workspace_id: str, grant_id: str
    ) -> tuple[DurableApproval, ...]: ...


class CapabilityGrantJournalPort(AgentRunPort, Protocol):
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


class RunPermissionJournalPort(
    CapabilityGrantJournalPort,
    ApprovalJournalPort,
    ToolExecutionJournalPort,
    Protocol,
):
    """Permission evidence surface required by one durable AgentRun."""


class RecoveryJournalPort(ToolExecutionJournalPort, TransactionalJournalPort, Protocol):
    def put_report(self, workspace_id: str, report: RecoveryReport) -> RecoveryReport: ...

    def get_open_report(self, workspace_id: str, session_id: str) -> RecoveryReport | None: ...

    def save_report(self, workspace_id: str, report: RecoveryReport) -> RecoveryReport: ...

    def get_recovery_receipt(
        self, workspace_id: str, session_id: str, command_id: str
    ) -> RecoveryReceipt | None: ...

    def put_recovery_receipt(
        self, workspace_id: str, receipt: RecoveryReceipt
    ) -> RecoveryReceipt: ...

    def get_report(self, workspace_id: str, report_id: str) -> RecoveryReport | None: ...

    def list_recovery_reports(
        self, workspace_id: str, session_id: str
    ) -> tuple[RecoveryReport, ...]: ...


class ArtifactMetadataJournalPort(Protocol):
    """SQLite-side authority for Artifact identity, state, and references."""

    def reserve_artifact(
        self, workspace_id: str, metadata: ArtifactMetadata
    ) -> ArtifactMetadata: ...

    def get_artifact(self, workspace_id: str, artifact_id: str) -> ArtifactMetadata | None: ...

    def list_artifacts(
        self,
        workspace_id: str,
        *,
        session_id: str | None = None,
        task_run_id: str | None = None,
    ) -> tuple[ArtifactMetadata, ...]: ...

    def save_artifact(
        self,
        workspace_id: str,
        metadata: ArtifactMetadata,
        *,
        expected_row_version: int,
    ) -> ArtifactMetadata: ...

    def artifact_bytes_for_task(self, workspace_id: str, task_run_id: str) -> int: ...

    def list_artifact_references(
        self, workspace_id: str, artifact_id: str
    ) -> tuple[ArtifactReference, ...]: ...


class ArtifactJournalPort(ArtifactMetadataJournalPort, ToolExecutionJournalPort, Protocol):
    """Artifact metadata plus execution references used by ArtifactService."""


class TaskJournalPort(
    SessionLifecyclePort, ToolExecutionJournalPort, TransactionalJournalPort, Protocol
):
    """Task lifecycle and outcome transaction surface."""


class CheckpointJournalPort(
    SessionLifecyclePort,
    ConversationJournalPort,
    ArtifactMetadataJournalPort,
    ToolExecutionJournalPort,
    Protocol,
):
    """Read/commit surface required by checkpoint and fork services."""
