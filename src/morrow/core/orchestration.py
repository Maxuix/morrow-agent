"""Bounded planning facts and explicit user policy; no runtime or registry ownership."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, model_validator

from morrow.core.agent_definitions import DefinitionId, Digest, OpaqueId
from morrow.core.domain import canonical_json_bytes, refuse_secret_material
from morrow.core.models import ModelRef, ProtocolModel
from morrow.core.workflows.contracts import SlotName, TaskContract
from morrow.core.workflows.definitions import WorkflowBudget

TaskType = Literal["implementation", "refactor", "research", "explanation", "diagnosis", "general"]
Level = Literal["low", "medium", "high"]
SafeLine = Annotated[str, Field(min_length=1, max_length=4096)]


class PlanningFacts(ProtocolModel):
    @model_validator(mode="after")
    def safe_facts(self):
        payload = canonical_json_bytes(self.model_dump(mode="json"))
        if len(payload) > 32768:
            raise ValueError("planning facts exceed their byte budget")
        refuse_secret_material(payload, label="planning facts", profile="workflow_value_sensitive")
        return self


class TaskClassification(PlanningFacts):
    """The entire model output schema. No graph, permissions, budget or reasoning slot."""

    task_type: TaskType = "general"
    expected_scope: tuple[SafeLine, ...] = Field(default=(), max_length=32)
    number_of_areas: int = Field(default=1, ge=1, le=32, strict=True)
    requires_code_write: bool = Field(default=False, strict=True)
    requires_research: bool = Field(default=False, strict=True)
    review_value: Level = "low"
    parallelizable_read_work: bool = Field(default=False, strict=True)
    ambiguity: Level = "low"
    risk_level: Level = "low"
    expected_duration_class: Literal["short", "medium", "long"] = "short"


class TaskFeatures(TaskClassification):
    user_requested_roles: tuple[SlotName, ...] = Field(default=(), max_length=16)
    user_excluded_roles: tuple[SlotName, ...] = Field(default=(), max_length=16)
    workspace_constraints: tuple[SafeLine, ...] = Field(default=(), max_length=32)


class TaskBrief(PlanningFacts):
    """One bounded, deterministic read-only Scout observation, never file contents."""

    project_markers: tuple[SafeLine, ...] = Field(default=(), max_length=16)
    observed_entries: int = Field(default=0, ge=0, le=128, strict=True)
    truncated: bool = False


class OrchestrationPolicy(PlanningFacts):
    policy_id: DefinitionId = "default"
    scope: Literal["global", "workspace"] = "global"
    task_matcher: TaskType | Literal["*"] = "*"
    preferred_template: Literal["direct", "explore_implement_verify"] | None = None
    excluded_templates: tuple[Literal["direct", "explore_implement_verify"], ...] = ()
    required_roles: tuple[SlotName, ...] = Field(default=(), max_length=16)
    excluded_roles: tuple[SlotName, ...] = Field(default=(), max_length=16)
    model_preferences_by_role: dict[SlotName, ModelRef] = Field(default_factory=dict, max_length=16)
    budget_limits: WorkflowBudget | None = None
    review_requirement: Literal["adaptive", "required", "skip"] = "adaptive"
    multi_agent: bool = Field(default=True, strict=True)
    parallelism_limit: Literal[1] = 1
    auto_run_mode: Literal["approval_only", "allow_promoted"] = "approval_only"
    auto_replan_mode: Literal["approval_only", "allow_low_risk"] = "approval_only"
    source: Literal["builtin", "user"] = "user"
    evidence: tuple[OpaqueId, ...] = Field(default=(), max_length=32)
    status: Literal["active", "disabled"] = "active"
    revision: int = Field(default=0, ge=0, strict=True)

    @model_validator(mode="after")
    def consistent(self):
        if set(self.required_roles) & set(self.excluded_roles):
            raise ValueError("required and excluded roles conflict")
        if self.preferred_template in self.excluded_templates:
            raise ValueError("preferred and excluded templates conflict")
        if self.budget_limits is not None and self.budget_limits.max_concurrency != 1:
            raise ValueError("planning currently supports serial execution only")
        if self.review_requirement == "required" and "reviewer" in self.excluded_roles:
            raise ValueError("required review conflicts with excluded reviewer")
        if self.review_requirement == "skip" and "reviewer" in self.required_roles:
            raise ValueError("required reviewer conflicts with skip review")
        return self


class GraphPlanningRequest(PlanningFacts):
    draft_id: str = Field(pattern=r"^wdraft_[A-Za-z0-9_-]{1,96}$")
    workflow_definition_id: DefinitionId
    name: str = Field(default="Task Workflow", min_length=1, max_length=128)
    task: TaskContract
    requested_roles: tuple[SlotName, ...] = Field(default=(), max_length=16)
    excluded_roles: tuple[SlotName, ...] = Field(default=(), max_length=16)
    budget: WorkflowBudget | None = None
    use_model: bool = Field(default=True, strict=True)
    scout: bool = Field(default=False, strict=True)

    @model_validator(mode="after")
    def legal_request(self):
        if self.workflow_definition_id.startswith("builtin_"):
            raise ValueError("planner requires a user Workflow definition ID")
        if set(self.requested_roles) & set(self.excluded_roles):
            raise ValueError("requested and excluded roles conflict")
        if self.budget is not None and self.budget.max_concurrency != 1:
            raise ValueError("planning currently supports serial execution only")
        return self


class PlannerExplanation(PlanningFacts):
    mode: Literal["direct", "multi", "needs_input"]
    reasons: tuple[SafeLine, ...] = Field(max_length=32)
    starting_point: Literal["direct", "grammar", "explore_implement_verify"]
    node_count: int = Field(ge=0, le=16)
    writing_nodes: tuple[SlotName, ...] = Field(default=(), max_length=16)
    models: tuple[ModelRef, ...] = Field(default=(), max_length=16)
    budget: WorkflowBudget
    concurrency: Literal[1] = 1
    auto_run_eligible: bool = False
    auto_run_reason: Literal["approval_only", "paired_evidence_missing", "paired_benefit"] = (
        "approval_only"
    )


class PlannerMetadata(PlanningFacts):
    request_digest: Digest
    source_hash: Digest
    features: TaskFeatures
    brief: TaskBrief | None = None
    policy_id: DefinitionId
    policy_scope: Literal["global", "workspace"]
    policy_revision: int = Field(ge=0)
    classification: Literal["local", "model", "unavailable", "invalid"]
    explanation: PlannerExplanation
    diagnostics: tuple[SafeLine, ...] = Field(default=(), max_length=16)
