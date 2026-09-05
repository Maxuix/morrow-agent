"""Durable state and attribution only; readiness belongs to the later Scheduler."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator

from morrow.core.agent_definitions import WorkspaceId
from morrow.core.domain import CLIENT_MESSAGE_ID_PATTERN
from morrow.core.models import ProtocolModel
from morrow.core.workflows.contracts import ArtifactBinding, ContractRef, SlotName
from morrow.core.workflows.definitions import WorkflowBudget, WorkflowRevisionId

WorkflowRunId = Annotated[str, Field(pattern=r"^wrun_[A-Za-z0-9_-]+$")]
NodeRunId = Annotated[str, Field(pattern=r"^nrun_[A-Za-z0-9_-]+$")]


class WorkflowExecutionNode(ProtocolModel):
    """One immutable member of a run's compiler-closed execution set."""

    workflow_run_id: WorkflowRunId
    node_id: SlotName
    topology_ordinal: int = Field(ge=0, strict=True)
    inclusion_reason: Literal["initial", "retained_future", "failed_retry"]


class WorkflowArtifactImport(ProtocolModel):
    """A child-run reference to one immutable output produced by its lineage."""

    workflow_run_id: WorkflowRunId
    source_workflow_run_id: WorkflowRunId
    source_node_run_id: NodeRunId
    source_node_id: SlotName
    output_slot: SlotName
    artifact_id: Annotated[str, Field(pattern=r"^art_[A-Za-z0-9_-]+$")]
    contract: ContractRef
    inherited_at: datetime


class WorkflowStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    BLOCKED = "blocked"
    DRAINING = "draining"
    PAUSED = "paused"
    SUPERSEDED = "superseded"

    @property
    def terminal(self):
        return self in {self.COMPLETED, self.FAILED, self.CANCELLED, self.SUPERSEDED}


def validate_run_transition(current: WorkflowStatus, target: WorkflowStatus) -> None:
    legal = {
        WorkflowStatus.QUEUED: {
            WorkflowStatus.RUNNING,
            WorkflowStatus.FAILED,
            WorkflowStatus.CANCELLED,
            WorkflowStatus.PAUSED,
        },
        WorkflowStatus.RUNNING: {
            WorkflowStatus.COMPLETED,
            WorkflowStatus.FAILED,
            WorkflowStatus.CANCELLED,
            WorkflowStatus.BLOCKED,
            WorkflowStatus.DRAINING,
            WorkflowStatus.PAUSED,
        },
        WorkflowStatus.BLOCKED: {
            WorkflowStatus.RUNNING,
            WorkflowStatus.FAILED,
            WorkflowStatus.CANCELLED,
            WorkflowStatus.DRAINING,
            WorkflowStatus.PAUSED,
            WorkflowStatus.SUPERSEDED,
        },
        WorkflowStatus.DRAINING: {
            WorkflowStatus.RUNNING,
            WorkflowStatus.PAUSED,
            WorkflowStatus.BLOCKED,
            WorkflowStatus.COMPLETED,
            WorkflowStatus.FAILED,
            WorkflowStatus.CANCELLED,
            WorkflowStatus.SUPERSEDED,
        },
        WorkflowStatus.PAUSED: {
            WorkflowStatus.RUNNING,
            WorkflowStatus.SUPERSEDED,
        },
    }
    if target not in legal.get(current, set()):
        raise ValueError("illegal Workflow/Node state transition")


class RunState(ProtocolModel):
    workspace_id: WorkspaceId
    status: WorkflowStatus = WorkflowStatus.QUEUED
    row_version: int = Field(default=1, ge=1, strict=True)
    started_at: datetime | None = None
    completed_at: datetime | None = None

    @model_validator(mode="after")
    def state_facts(self):
        if self.status.terminal != (self.completed_at is not None):
            raise ValueError("terminal state requires exactly one completion timestamp")
        if self.status == WorkflowStatus.RUNNING and self.started_at is None:
            raise ValueError("running state requires an admission timestamp")
        return self


class WorkflowRun(RunState):
    workflow_run_id: WorkflowRunId
    workflow_revision_id: WorkflowRevisionId
    root_task_run_id: Annotated[str, Field(pattern=r"^task_[A-Za-z0-9_-]+$")]
    budget_snapshot: WorkflowBudget
    admission_deadline_at: datetime | None = None
    input_artifacts: tuple[ArtifactBinding, ...] = Field(min_length=1, max_length=1)
    result_status: Literal["succeeded", "needs_revision"] | None = None
    pending_terminal_intent: Literal["user_cancel"] | None = None
    invoking_client_message_id: str | None = None
    invoking_root_row_version: int | None = Field(default=None, ge=1, strict=True)
    # The one durable orthogonal Pause fact: never a process-local flag, never
    # inherited by a handoff child, and mutated only under row-version OCC.
    pause_requested: bool = False
    run_relation: Literal["initial", "continuation", "rerun"] = "initial"
    lineage_budget_root_run_id: WorkflowRunId | None = None
    parent_run_id: WorkflowRunId | None = None
    superseded_reason: Literal["continued_by_patch"] | None = None

    @property
    def effective_lineage_budget_root_run_id(self) -> str:
        """Legacy rows predate the lineage column; an initial run is its own root."""

        return self.lineage_budget_root_run_id or self.workflow_run_id

    @field_validator("invoking_client_message_id")
    @classmethod
    def valid_client_message_id(cls, value: str | None) -> str | None:
        if value is not None and not CLIENT_MESSAGE_ID_PATTERN.match(value):
            raise ValueError("client_message_id must be a bounded opaque command field")
        return value

    @model_validator(mode="after")
    def workflow_facts(self):
        if (self.status == WorkflowStatus.COMPLETED) != (self.result_status is not None):
            raise ValueError("only completed Workflows have a result status")
        binding = self.input_artifacts[0]
        if binding.name != "task" or binding.contract.kind != "TaskContract":
            raise ValueError("Workflow input must bind TaskContract@1 as task")
        if (self.invoking_client_message_id is None) != (self.invoking_root_row_version is None):
            raise ValueError("Direct Workflow binding facts must be present together")
        if self.run_relation == "initial":
            if self.parent_run_id is not None:
                raise ValueError("an initial Workflow run has no parent")
            if self.effective_lineage_budget_root_run_id != self.workflow_run_id:
                raise ValueError("an initial Workflow run is its own lineage budget root")
        elif self.parent_run_id is None or self.lineage_budget_root_run_id is None:
            raise ValueError("a continuation/rerun Workflow run requires its lineage facts")
        if (self.status is WorkflowStatus.SUPERSEDED) != (self.superseded_reason is not None):
            raise ValueError("only a superseded Workflow records its supersession reason")
        if (
            self.pause_requested
            and not self.status.terminal
            and self.status
            not in {WorkflowStatus.DRAINING, WorkflowStatus.PAUSED, WorkflowStatus.BLOCKED}
        ):
            raise ValueError("pause_requested requires a draining, paused or blocked state")
        if self.status in {WorkflowStatus.DRAINING, WorkflowStatus.PAUSED}:
            if not self.pause_requested:
                raise ValueError("a draining/paused Workflow keeps the pause fact")
            if self.started_at is None:
                raise ValueError("a draining/paused Workflow requires an admission timestamp")
        return self


class NodeRun(RunState):
    node_run_id: NodeRunId
    workflow_run_id: WorkflowRunId
    node_id: SlotName
    attempt: Literal[1] = 1
    conversation_session_id: Annotated[str, Field(pattern=r"^ses_[A-Za-z0-9_-]+$")] | None = None
    leaf_task_run_id: Annotated[str, Field(pattern=r"^task_[A-Za-z0-9_-]+$")] | None = None
    agent_run_id: Annotated[str, Field(pattern=r"^arun_[A-Za-z0-9_-]+$")] | None = None
    effective_node_generation_request_cap: int | None = Field(default=None, gt=0, strict=True)
    parallel_read_digest: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")] | None = None

    @field_validator("attempt", mode="before")
    @classmethod
    def strict_attempt(cls, value):
        if type(value) is not int:
            raise ValueError("Node attempt must be an integer")
        return value

    @model_validator(mode="after")
    def admission_facts(self):
        ownership_refs = (
            self.conversation_session_id,
            self.leaf_task_run_id,
            self.agent_run_id,
        )
        if any(v is not None for v in ownership_refs) != all(v is not None for v in ownership_refs):
            raise ValueError("Node admission references must be bound together")
        if self.status == WorkflowStatus.QUEUED and (
            any(v is not None for v in ownership_refs)
            or self.effective_node_generation_request_cap is not None
            or self.parallel_read_digest is not None
        ):
            raise ValueError("queued Node cannot have admission references")
        if self.status in {
            WorkflowStatus.RUNNING,
            WorkflowStatus.COMPLETED,
            WorkflowStatus.BLOCKED,
        } and any(v is None for v in ownership_refs):
            raise ValueError("admitted Node requires leaf references")
        return self
