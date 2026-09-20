"""Bounded closure evidence and exact, durable global replan proposals."""

from datetime import datetime
from typing import Literal

from pydantic import Field, model_validator

from morrow.core.agent_definitions import WorkspaceId
from morrow.core.domain import canonical_json_bytes
from morrow.core.models import ProtocolModel
from morrow.core.orchestration import PlanningFacts
from morrow.core.workflows.contracts import ArtifactId, SlotName
from morrow.core.workflows.patches import FutureGraphPatch

REPLAN_REQUEST_SCHEMA_VERSION = 2


class ReplanEvidenceRef(ProtocolModel):
    """Already-durable evidence the leaf observed. Never inlined bytes or a graph."""

    kind: Literal["artifact", "node_output", "submitted_slot"]
    artifact_id: ArtifactId | None = None
    node_id: SlotName | None = None
    slot: SlotName | None = None
    note: str = Field(default="", max_length=512)

    @model_validator(mode="after")
    def matching_shape(self):
        if self.kind == "artifact":
            if self.artifact_id is None:
                raise ValueError("artifact evidence requires artifact_id")
        elif self.kind == "node_output":
            if self.node_id is None or self.slot is None:
                raise ValueError("node_output evidence requires node_id and slot")
        elif self.slot is None:
            raise ValueError("submitted_slot evidence requires slot")
        return self


class AffectedTaskFact(ProtocolModel):
    """Which remaining work the new evidence may invalidate. Never a rewrite."""

    node_id: SlotName | None = None
    summary: str = Field(min_length=1, max_length=1024)
    impact: Literal["invalidate", "missing_dependency", "scope_change"] = "invalidate"

    @model_validator(mode="after")
    def nonempty_summary(self):
        if not self.summary.strip():
            raise ValueError("affected task fact summary must not be blank")
        return self


class ReplanRequest(PlanningFacts):
    """A leaf requests a future task correction, never supplies a graph or authority."""

    schema_version: Literal[REPLAN_REQUEST_SCHEMA_VERSION] = REPLAN_REQUEST_SCHEMA_VERSION
    reason: Literal["new_evidence", "missing_dependency", "scope_correction"] = "new_evidence"
    evidence_refs: tuple[ReplanEvidenceRef, ...] = Field(default=(), max_length=32)
    affected_facts: tuple[AffectedTaskFact, ...] = Field(default=(), max_length=32)

    @model_validator(mode="after")
    def current_shape(self):
        if not (self.evidence_refs or self.affected_facts):
            raise ValueError("replan signals require evidence or affected facts")
        return self


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
