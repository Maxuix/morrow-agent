"""Narrow lifecycle and conversation-journal ports for the Operational Store."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Protocol, Self, TypeVar

from morrow.core.artifacts import ArtifactMetadata
from morrow.core.context import ContextCheckpoint, SessionLineage
from morrow.core.domain import (
    ArtifactReference,
    DurableAgentRun,
    DurableConversationRecord,
    DurableSession,
    DurableTaskRun,
    DurableTaskRunTransition,
    DurableTurn,
    TaskCommandReceipt,
    TaskOutcome,
    TaskRunStatus,
    TurnSubmitReceipt,
)
from morrow.core.execution import DurableApproval, DurableToolExecution
from morrow.core.mcp import (
    McpCatalogSnapshot,
    McpLaunchSnapshot,
    McpResultArtifactLink,
    McpServerDefinition,
    McpToolSnapshot,
)
from morrow.core.observability import (
    AgentRunObservation,
    AgentRunRetryProgress,
    AgentRunTerminalMetrics,
    ModelRequestObservation,
)
from morrow.core.permissions import CapabilityGrant, PermissionSnapshot
from morrow.core.recovery import RecoveryReceipt, RecoveryReport
from morrow.core.runtime_control import RuntimeControlEntry, RuntimeControlKind
from morrow.core.skills.context import SkillContextEntry
from morrow.core.skills.drafts import SkillDraft, SkillDraftValidationReport
from morrow.core.skills.selection import SkillSelection
from morrow.core.skills.usage import SkillUsage

T = TypeVar("T")


class TransactionalJournalPort(Protocol):
    """Run work against the same transaction-scoped journal implementation."""

    def supports_writes(self) -> bool: ...

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

    def create_task_run(
        self, workspace_id: str, task: DurableTaskRun, *, make_current: bool = False
    ) -> DurableTaskRun: ...

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

    def put_task_outcome(self, workspace_id: str, outcome: TaskOutcome) -> TaskOutcome: ...

    def get_task_outcome(self, workspace_id: str, outcome_id: str) -> TaskOutcome | None: ...

    def list_task_outcomes(
        self, workspace_id: str, task_run_id: str
    ) -> tuple[TaskOutcome, ...]: ...

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


class AgentRunObservabilityPort(Protocol):
    """Admission, settlement and safe inspection for AgentRun observations."""

    def admit_model_request(self, workspace_id: str, **kwargs) -> ModelRequestObservation: ...

    def settle_model_request(
        self, workspace_id: str, model_request_id: str, **kwargs
    ) -> ModelRequestObservation: ...

    def finalize_agent_run(self, workspace_id: str, **kwargs) -> AgentRunTerminalMetrics: ...

    def get_model_request(
        self, workspace_id: str, model_request_id: str
    ) -> ModelRequestObservation | None: ...

    def list_model_requests(
        self, workspace_id: str, agent_run_id: str
    ) -> tuple[ModelRequestObservation, ...]: ...

    def get_agent_run_terminal_metrics(
        self, workspace_id: str, agent_run_id: str
    ) -> AgentRunTerminalMetrics | None: ...

    def get_agent_run_observation(
        self, workspace_id: str, agent_run_id: str
    ) -> AgentRunObservation | None: ...

    def get_agent_run_retry_progress(
        self, workspace_id: str, agent_run_id: str
    ) -> AgentRunRetryProgress | None: ...

    def record_agent_run_retry_progress(
        self, workspace_id: str, **kwargs
    ) -> AgentRunRetryProgress: ...


class SkillRunJournalPort(Protocol):
    """v14 immutable Skill evidence attached to one AgentRun."""

    def put_skill_selection(
        self, workspace_id: str, selection: SkillSelection
    ) -> SkillSelection: ...

    def get_skill_selection(
        self, workspace_id: str, selection_id: str
    ) -> SkillSelection | None: ...

    def list_skill_selections(
        self, workspace_id: str, agent_run_id: str
    ) -> tuple[SkillSelection, ...]: ...

    def put_skill_context(
        self, workspace_id: str, context: SkillContextEntry
    ) -> SkillContextEntry: ...

    def get_skill_context(self, workspace_id: str, context_id: str) -> SkillContextEntry | None: ...

    def list_skill_contexts(
        self, workspace_id: str, agent_run_id: str
    ) -> tuple[SkillContextEntry, ...]: ...


class SkillDraftUsageJournalPort(Protocol):
    """v15 generated Draft, validation and observational Usage records."""

    def put_skill_draft(self, workspace_id: str, draft: SkillDraft) -> SkillDraft: ...

    def get_skill_draft(self, workspace_id: str, draft_id: str) -> SkillDraft | None: ...

    def get_skill_draft_by_candidate(
        self, workspace_id: str, candidate_id: str, *, latest: bool = True
    ) -> SkillDraft | None: ...

    def list_skill_drafts(
        self,
        workspace_id: str,
        *,
        candidate_id: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> tuple[SkillDraft, ...]: ...

    def save_skill_draft(
        self, workspace_id: str, draft: SkillDraft, *, expected_row_version: int
    ) -> SkillDraft: ...

    def put_skill_draft_validation(
        self, workspace_id: str, report: SkillDraftValidationReport
    ) -> SkillDraftValidationReport: ...

    def get_skill_draft_validation(
        self, workspace_id: str, draft_id: str, validation_id: str
    ) -> SkillDraftValidationReport | None: ...

    def list_skill_draft_validations(
        self, workspace_id: str, draft_id: str, *, limit: int = 32
    ) -> tuple[SkillDraftValidationReport, ...]: ...

    def put_skill_usage(self, workspace_id: str, usage: SkillUsage) -> SkillUsage: ...

    def get_skill_usage(self, workspace_id: str, usage_id: str) -> SkillUsage | None: ...

    def list_skill_usages(
        self,
        workspace_id: str,
        *,
        skill_id: str | None = None,
        version_id: str | None = None,
        agent_run_id: str | None = None,
        limit: int = 100,
    ) -> tuple[SkillUsage, ...]: ...


class McpCatalogJournalPort(Protocol):
    """v16 MCP desired-state, Catalog and reserved run-evidence surface."""

    def put_mcp_server(
        self, definition: McpServerDefinition, *, catalog: McpCatalogSnapshot | None = None
    ) -> McpServerDefinition: ...

    def get_mcp_server(
        self, scope: str, server_id: str, *, scope_id: str | None = None
    ) -> McpServerDefinition | None: ...

    def list_mcp_servers(
        self, scope: str, *, scope_id: str | None = None
    ) -> tuple[McpServerDefinition, ...]: ...

    def put_mcp_catalog(
        self, definition: McpServerDefinition, snapshot: McpCatalogSnapshot
    ) -> McpCatalogSnapshot: ...

    def get_mcp_catalog(
        self,
        scope: str,
        server_id: str,
        *,
        scope_id: str | None = None,
        revision: int | None = None,
    ) -> McpCatalogSnapshot | None: ...

    def put_mcp_launch_snapshot(self, snapshot: McpLaunchSnapshot) -> McpLaunchSnapshot: ...

    def list_mcp_launch_snapshots(
        self, workspace_id: str, agent_run_id: str
    ) -> tuple[McpLaunchSnapshot, ...]: ...

    def put_mcp_tool_snapshot(
        self, workspace_id: str, snapshot: McpToolSnapshot
    ) -> McpToolSnapshot: ...

    def list_mcp_tool_snapshots(
        self, workspace_id: str, agent_run_id: str
    ) -> tuple[McpToolSnapshot, ...]: ...

    def put_mcp_result_artifact_link(
        self, link: McpResultArtifactLink
    ) -> McpResultArtifactLink: ...

    def list_mcp_result_artifact_links(
        self, workspace_id: str, tool_execution_id: str
    ) -> tuple[McpResultArtifactLink, ...]: ...


class TurnSubmitReceiptPort(Protocol):
    def get_receipt(
        self, workspace_id: str, session_id: str, client_message_id: str
    ) -> TurnSubmitReceipt | None: ...

    def put_receipt(self, workspace_id: str, receipt: TurnSubmitReceipt) -> TurnSubmitReceipt: ...

    def update_receipt(
        self, workspace_id: str, receipt: TurnSubmitReceipt
    ) -> TurnSubmitReceipt: ...


class RuntimeControlJournalPort(Protocol):
    def get_runtime_control(
        self, workspace_id: str, session_id: str, client_message_id: str
    ) -> RuntimeControlEntry | None: ...

    def peek_runtime_control(
        self,
        workspace_id: str,
        session_id: str,
        *,
        kind: RuntimeControlKind,
    ) -> RuntimeControlEntry | None: ...

    def consume_runtime_control(
        self,
        workspace_id: str,
        session_id: str,
        client_message_id: str,
        *,
        consumed_at,
    ) -> RuntimeControlEntry: ...


class TurnLifecycleJournalPort(
    SessionLifecyclePort,
    AgentRunPort,
    AgentRunObservabilityPort,
    McpCatalogJournalPort,
    SkillRunJournalPort,
    SkillDraftUsageJournalPort,
    TurnSubmitReceiptPort,
    RuntimeControlJournalPort,
    TransactionalJournalPort,
    Protocol,
):
    """Atomic Session, Task, Turn, AgentRun, and submit-receipt surface."""


class SessionRestoreJournalPort(
    SessionLifecyclePort,
    ConversationJournalPort,
    AgentRunPort,
    SkillRunJournalPort,
    SkillDraftUsageJournalPort,
    Protocol,
):
    """Read surface needed to restore one in-process Session projection."""


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


class DurableToolJournalPort(
    TransactionalJournalPort,
    ToolExecutionJournalPort,
    ApprovalJournalPort,
    McpCatalogJournalPort,
    Protocol,
):
    """Atomic execution and approval surface for durable tool cycles."""


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
