"""Workspace-local feedback, deterministic Learning review, and paired evaluation facts."""

from datetime import datetime
from typing import Literal

from pydantic import Field, model_validator

from morrow.core.agent_definitions import Digest, OpaqueId, WorkspaceId
from morrow.core.models import ModelRef
from morrow.core.orchestration import OrchestrationPolicy, PlanningFacts, TaskType
from morrow.core.workflows.contracts import SlotName

FeedbackKind = Literal[
    "graph_edit",
    "removed_planner",
    "model_changed",
    "added_reviewer",
    "too_complex",
    "missing_exploration",
    "reviewer_useful",
    "reviewer_not_useful",
    "model_expensive",
    "prefer_template",
    "avoid_template",
]
Template = Literal["direct", "explore_implement_verify"]


class WorkflowFeedback(PlanningFacts):
    feedback_id: OpaqueId
    workspace_id: WorkspaceId
    subject_kind: Literal["draft", "run"]
    subject_id: OpaqueId
    sample_id: OpaqueId
    task_type: TaskType
    kind: FeedbackKind
    role: SlotName | None = None
    model: ModelRef | None = None
    template: Template | None = None
    before_hash: Digest | None = None
    after_hash: Digest | None = None
    created_at: datetime

    @model_validator(mode="after")
    def feedback_shape(self):
        if self.created_at.tzinfo is None:
            raise ValueError("feedback requires an aware timestamp")
        if (self.before_hash is None) != (self.after_hash is None):
            raise ValueError("edit hashes must be paired")
        if (self.kind in {"prefer_template", "avoid_template"}) != (self.template is not None):
            raise ValueError("template feedback requires a template")
        if self.kind == "model_changed" and (self.role is None or self.model is None):
            raise ValueError("model feedback requires an exact role and model")
        return self


class WorkflowPolicyCandidate(PlanningFacts):
    """A deterministic LearningReview result; acceptance uses the normal YAML owner."""

    candidate_id: OpaqueId
    workspace_id: WorkspaceId
    semantic_key: Digest
    task_type: TaskType
    feedback_kind: FeedbackKind
    evidence_ids: tuple[OpaqueId, ...] = Field(min_length=2, max_length=32)
    proposed_policy: OrchestrationPolicy
    expected_document_revision: int = Field(ge=0)
    expected_global_revision: int = Field(default=0, ge=0)
    status: Literal["proposed", "applying", "accepted", "rejected"] = "proposed"
    row_version: int = Field(default=1, ge=1)
    decision_command_id: OpaqueId | None = None
    created_at: datetime

    @model_validator(mode="after")
    def review_shape(self):
        if (
            self.proposed_policy.scope != "workspace"
            or self.proposed_policy.task_matcher != self.task_type
        ):
            raise ValueError("review policy scope mismatch")
        if self.proposed_policy.evidence != self.evidence_ids or len(set(self.evidence_ids)) < 2:
            raise ValueError("review evidence mismatch")
        if (self.status == "proposed") != (self.decision_command_id is None):
            raise ValueError("review decision identity mismatch")
        return self


class WorkflowEvaluation(PlanningFacts):
    evaluation_id: OpaqueId
    workspace_id: WorkspaceId
    task_type: TaskType
    task_digest: Digest
    kind: Literal["paired", "estimate"]
    multi_run_id: OpaqueId
    direct_run_id: OpaqueId | None = None
    direct_requests: int = Field(ge=0)
    multi_requests: int = Field(ge=0)
    direct_quality: int | None = Field(default=None, ge=0, le=4)
    multi_quality: int | None = Field(default=None, ge=0, le=4)
    assessment_source: Literal["user"] = "user"
    created_at: datetime

    @model_validator(mode="after")
    def paired_facts(self):
        if self.kind == "paired" and (
            self.direct_run_id is None or self.direct_quality is None or self.multi_quality is None
        ):
            raise ValueError("paired evaluation requires both runs and quality judgments")
        if self.kind == "estimate" and (
            self.direct_run_id is not None
            or self.direct_quality is not None
            or self.multi_quality is not None
        ):
            raise ValueError("an estimate is not a paired result")
        return self

    @property
    def benefit(self) -> bool:
        return self.kind == "paired" and (
            self.multi_quality > self.direct_quality
            or (
                self.multi_quality == self.direct_quality
                and self.multi_requests < self.direct_requests
            )
        )
