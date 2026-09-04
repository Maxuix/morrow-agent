"""Bounded closure evidence and exact, durable global replan proposals."""

from datetime import datetime
from typing import Literal

from pydantic import Field, model_validator

from morrow.core.agent_definitions import WorkspaceId
from morrow.core.domain import canonical_json_bytes
from morrow.core.models import ProtocolModel
from morrow.core.orchestration import PlanningFacts
from morrow.core.workflows.contracts import SlotName, TaskContract
from morrow.core.workflows.patches import FutureGraphPatch


class ReplanRequest(PlanningFacts):
    """A leaf requests a future task correction, never supplies a graph or authority."""

    target_node_id: SlotName
    task_contract: TaskContract
    reason: Literal["new_evidence", "missing_dependency", "scope_correction"] = "new_evidence"
    depends_on: tuple[SlotName, ...] = Field(default=(), max_length=32)


class ReplanSignal(PlanningFacts):
    signal_id: str = Field(pattern=r"^rsig_[A-Za-z0-9_-]+$")
    workspace_id: WorkspaceId
    workflow_run_id: str = Field(pattern=r"^wrun_[A-Za-z0-9_-]+$")
    node_run_id: str = Field(pattern=r"^nrun_[A-Za-z0-9_-]+$")
    request: ReplanRequest
    created_at: datetime


class ReplanProposal(ProtocolModel):
    proposal_id: str = Field(pattern=r"^rprop_[A-Za-z0-9_-]+$")
    workspace_id: WorkspaceId
    patch: FutureGraphPatch
    signal_ids: tuple[str, ...] = Field(default=(), max_length=128)
    status: Literal["pending", "applied", "rejected", "conflict", "invalid"] = "pending"
    risk_level: Literal["low", "elevated"] = "elevated"
    risk_reasons: tuple[str, ...] = ()
    disposition_reason: str = "approval_only"
    policy_id: str = "default"
    policy_revision: int = 0
    auto_applied: bool = False
    child_run_id: str | None = None
    decided_by: str | None = None
    created_at: datetime
    decided_at: datetime | None = None
    row_version: int = Field(default=1, ge=1)

    @model_validator(mode="after")
    def bounded_proposal(self):
        if len(canonical_json_bytes(self.model_dump(mode="json"))) > 524288:
            raise ValueError("Replan proposal exceeds its payload budget")
        if self.patch.workspace_id != self.workspace_id:
            raise ValueError("Replan proposal workspace mismatch")
        if self.auto_applied and (self.status != "applied" or self.risk_level != "low"):
            raise ValueError("only applied low-risk proposals can be automatic")
        if (self.status == "applied") != (self.child_run_id is not None):
            raise ValueError("only applied proposals have a child")
        return self
