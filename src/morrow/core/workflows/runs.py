"""Durable state and attribution only; readiness belongs to the later Scheduler."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator

from morrow.core.agent_definitions import WorkspaceId
from morrow.core.models import ProtocolModel
from morrow.core.workflows.contracts import ArtifactBinding, SlotName
from morrow.core.workflows.definitions import WorkflowBudget, WorkflowRevisionId

WorkflowRunId = Annotated[str, Field(pattern=r"^wrun_[A-Za-z0-9_-]+$")]
NodeRunId = Annotated[str, Field(pattern=r"^nrun_[A-Za-z0-9_-]+$")]


class WorkflowStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    BLOCKED = "blocked"

    @property
    def terminal(self):
        return self in {self.COMPLETED, self.FAILED, self.CANCELLED}


def validate_run_transition(current: WorkflowStatus, target: WorkflowStatus) -> None:
    legal = {
        WorkflowStatus.QUEUED: {
            WorkflowStatus.RUNNING,
            WorkflowStatus.FAILED,
            WorkflowStatus.CANCELLED,
        },
        WorkflowStatus.RUNNING: {
            WorkflowStatus.COMPLETED,
            WorkflowStatus.FAILED,
            WorkflowStatus.CANCELLED,
            WorkflowStatus.BLOCKED,
        },
        WorkflowStatus.BLOCKED: {
            WorkflowStatus.RUNNING,
            WorkflowStatus.FAILED,
            WorkflowStatus.CANCELLED,
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
    admission_deadline_at: datetime
    input_artifacts: tuple[ArtifactBinding, ...] = Field(min_length=1, max_length=1)
    result_status: Literal["succeeded", "needs_revision"] | None = None
    pending_terminal_intent: Literal["user_cancel"] | None = None

    @model_validator(mode="after")
    def workflow_facts(self):
        if (self.status == WorkflowStatus.COMPLETED) != (self.result_status is not None):
            raise ValueError("only completed Workflows have a result status")
        binding = self.input_artifacts[0]
        if binding.name != "task" or binding.contract.kind != "TaskContract":
            raise ValueError("Workflow input must bind TaskContract@1 as task")
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

    @field_validator("attempt", mode="before")
    @classmethod
    def strict_attempt(cls, value):
        if type(value) is not int:
            raise ValueError("Node attempt must be an integer")
        return value

    @model_validator(mode="after")
    def admission_facts(self):
        refs = (
            self.conversation_session_id,
            self.leaf_task_run_id,
            self.agent_run_id,
            self.effective_node_generation_request_cap,
        )
        if any(v is not None for v in refs) != all(v is not None for v in refs):
            raise ValueError("Node admission references must be bound together")
        if self.status == WorkflowStatus.QUEUED and any(v is not None for v in refs):
            raise ValueError("queued Node cannot have admission references")
        if self.status in {
            WorkflowStatus.RUNNING,
            WorkflowStatus.COMPLETED,
            WorkflowStatus.BLOCKED,
        } and any(v is None for v in refs):
            raise ValueError("admitted Node requires leaf references")
        return self
