"""Bounded planning data, separate from executable source and stored wire hashes."""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import Field, model_validator

from morrow.core.agent_definitions import Digest, VersionId, WorkspaceId
from morrow.core.domain import canonical_json_bytes, refuse_secret_material, sha256_digest
from morrow.core.execution_selections import GenerationChoice, ModelChoice, SelectionSource
from morrow.core.models import (
    AttachmentRef,
    GenerationOptions,
    ModelRef,
    ProtocolModel,
    ReasoningEffort,
)
from morrow.core.workflows.contracts import SlotName, TaskContract
from morrow.core.workflows.definitions import WorkflowDefinitionSource, WorkflowRevisionId
from morrow.core.workflows.drafts import WorkflowDraftId

BindingId = Annotated[str, Field(pattern=r"^wplan_[A-Za-z0-9_-]+$")]
OperationId = Annotated[str, Field(pattern=r"^wop_[A-Za-z0-9_-]+$")]
SessionId = Annotated[str, Field(pattern=r"^ses_[A-Za-z0-9_-]+$")]
CommandId = Annotated[str, Field(pattern=r"^cmd_[A-Za-z0-9_-]+$", max_length=128)]
DecisionId = Annotated[str, Field(pattern=r"^wdec_[A-Za-z0-9_-]+$")]
TASK_PLAN_DEFINITION_PREFIX = "task_"


class PlanningValue(ProtocolModel):
    @model_validator(mode="after")
    def safe(self):
        payload = canonical_json_bytes(self.model_dump(mode="json"))
        if len(payload) > 512 * 1024:
            raise ValueError("planning payload exceeds limit")
        refuse_secret_material(payload, label="Planning", profile="workflow_value_sensitive")
        for value in self.__dict__.values():
            if isinstance(value, datetime) and value.utcoffset() is None:
                raise ValueError("planning timestamp must be timezone-aware")
        return self


class PlanNode(PlanningValue):
    node_id: SlotName
    title: str = Field(min_length=1, max_length=128)
    task: str = Field(min_length=1, max_length=4096)
    agent: str = Field(pattern=r"^(preset:(general|explore|review)|custom:[a-z][a-z0-9_-]{0,63})$")
    responsibility: Literal["implementation", "research", "review", "synthesis", "general"]
    depends_on: tuple[SlotName, ...] = Field(default=(), max_length=16)
    completion: tuple[str, ...] = Field(min_length=1, max_length=16)

    @model_validator(mode="after")
    def bounded_completion(self):
        if any(not x.strip() or len(x) > 512 for x in self.completion):
            raise ValueError("completion criteria must be bounded nonempty text")
        if not self.task.strip() or not self.title.strip():
            raise ValueError("node task and title are required")
        return self


class PlanSpec(PlanningValue):
    """Ephemeral model response. Never another editable persisted DAG."""

    nodes: tuple[PlanNode, ...] = Field(min_length=1, max_length=16)
    deliverables: tuple[SlotName, ...] = Field(min_length=1, max_length=16)

    @model_validator(mode="after")
    def graph(self):
        ids = {n.node_id for n in self.nodes}
        if len(ids) != len(self.nodes) or not set(self.deliverables) <= ids:
            raise ValueError("duplicate node or missing delivery")
        pending = {n.node_id: set(n.depends_on) for n in self.nodes}
        if any(not parents <= ids for parents in pending.values()):
            raise ValueError("dependency is missing")
        while pending:
            ready = {identity for identity, parents in pending.items() if not parents}
            if not ready:
                raise ValueError("dependency cycle")
            pending = {k: v - ready for k, v in pending.items() if k not in ready}
        return self


class PlanningContextRef(PlanningValue):
    conversation_position: int = Field(ge=0, strict=True)
    attachments: tuple[AttachmentRef, ...] = Field(default=(), max_length=8)
    constraints: tuple[str, ...] = Field(default=(), max_length=32)
    settings_revision: int = Field(default=0, ge=0, strict=True)
    model: ModelRef
    generation: GenerationOptions = Field(default_factory=GenerationOptions)
    settings_digest: Digest


class PlanningBinding(PlanningValue):
    planning_binding_id: BindingId
    workspace_id: WorkspaceId
    session_id: SessionId
    origin_interaction_id: str = Field(min_length=1, max_length=128)
    mode: Literal["initial", "change", "repair"] = "initial"
    parent_run_id: str | None = None
    parent_revision_id: str | None = None
    current_draft_id: WorkflowDraftId | None = None
    artifact_ids: tuple[str, ...] = Field(default=(), max_length=64)
    context_ref: PlanningContextRef
    status: Literal["active", "closed", "superseded"] = "active"
    row_version: int = Field(ge=1, strict=True)
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def parents(self):
        if (self.mode == "initial" and (self.parent_run_id or self.parent_revision_id)) or (
            self.mode != "initial" and not (self.parent_run_id and self.parent_revision_id)
        ):
            raise ValueError("planning parent provenance mismatch")
        return self


class NodePlanningMetadata(PlanningValue):
    title: str = Field(min_length=1, max_length=128)
    responsibility: Literal["implementation", "research", "review", "synthesis", "general"]
    agent_selection: str = Field(max_length=80)
    model_choice: ModelChoice | None = None
    generation_choice: GenerationChoice | None = None
    x: float | None = Field(default=None, allow_inf_nan=False)
    y: float | None = Field(default=None, allow_inf_nan=False)


class DraftVersion(PlanningValue):
    workspace_id: WorkspaceId
    draft_id: WorkflowDraftId
    version: int = Field(ge=1, strict=True)
    source: WorkflowDefinitionSource
    source_hash: Digest
    node_metadata: dict[str, NodePlanningMetadata] = Field(default_factory=dict, max_length=16)
    execution_selections: dict[str, dict] = Field(default_factory=dict, max_length=16)
    created_from: Literal["llm_generate", "llm_revise", "manual_edit", "system_repair"]
    command_id: CommandId
    summary: str = Field(default="", max_length=2048)
    validation_digest: Digest
    created_at: datetime

    @model_validator(mode="after")
    def valid_hash(self):
        if self.source_hash != self.source.content_hash:
            raise ValueError("draft version source hash mismatch")
        return self


class PlanningOperation(PlanningValue):
    planning_operation_id: OperationId
    workspace_id: WorkspaceId
    session_id: SessionId
    planning_binding_id: BindingId
    operation: Literal["generate", "revise", "validate", "repair"]
    command_id: CommandId
    request_digest: Digest
    accepted_request_json: str | None = Field(default=None, max_length=65536)
    base_draft_id: WorkflowDraftId | None = None
    base_draft_version: int = Field(default=0, ge=0, strict=True)
    status: Literal["queued", "running", "succeeded", "failed", "cancelled", "expired"] = "queued"
    result_draft_id: WorkflowDraftId | None = None
    result_draft_version: int | None = Field(default=None, ge=1, strict=True)
    candidate_source: WorkflowDefinitionSource | None = None
    error_code: Literal["invalid", "stale", "unavailable", "needs_recovery"] | None = None
    diagnostics: tuple[str, ...] = Field(default=(), max_length=32)
    cancelled_at: datetime | None = None
    cancel_reason: Literal["user", "shutdown"] | None = None
    row_version: int = Field(ge=1, strict=True)
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def result(self):
        if (self.status == "succeeded") != (
            self.result_draft_id is not None and self.result_draft_version is not None
        ):
            raise ValueError("planning result reference mismatch")
        if any(len(x) > 512 for x in self.diagnostics):
            raise ValueError("planning diagnostic too large")
        return self


class PlanWorkflowRequest(PlanningValue):
    command_id: CommandId
    session_id: SessionId
    origin_interaction_id: str = Field(min_length=1, max_length=128)
    task: TaskContract
    attachments: tuple[AttachmentRef, ...] = Field(default=(), max_length=8)
    planning_binding_id: BindingId | None = None
    base_draft_version: int = Field(default=0, ge=0, strict=True)
    operation: Literal["generate", "revise", "repair"] = "generate"

    @property
    def digest(self):
        return sha256_digest(canonical_json_bytes(self.model_dump(mode="json")))


class PlanNodeEdit(PlanningValue):
    command_id: CommandId
    binding_id: BindingId
    expected_version: int = Field(ge=1, strict=True)
    action: Literal["add", "replace", "remove", "restore", "layout", "dependencies"]
    node_id: SlotName | None = None
    node: PlanNode | None = None
    depends_on: tuple[SlotName, ...] = ()
    restore_version: int | None = Field(default=None, ge=1, strict=True)
    x: float | None = Field(default=None, allow_inf_nan=False)
    y: float | None = Field(default=None, allow_inf_nan=False)
    confirm_impact: bool = False

    @model_validator(mode="after")
    def edit_payload(self):
        if self.action in {"add", "replace"} and self.node is None:
            raise ValueError("node edit requires a node")
        if self.action in {"remove", "layout", "dependencies"} and self.node_id is None:
            raise ValueError("node edit requires an identity")
        if self.action == "restore" and self.restore_version is None:
            raise ValueError("restore requires a historical version")
        return self


class FrozenNodeSelection(PlanningValue):
    """Per-node values frozen at start; never mixed into compiled source hashes."""

    node_id: SlotName
    version_id: VersionId
    content_hash: Digest
    resolved_model: ModelRef
    resolved_generation: ReasoningEffort | None = None
    model_source: SelectionSource
    generation_source: SelectionSource


class PlanDecision(PlanningValue):
    """The only durable start/change authorization fact. LLM output cannot write this."""

    plan_decision_id: DecisionId
    workspace_id: WorkspaceId
    session_id: SessionId
    command_id: CommandId
    decision: Literal[
        "start",
        "accept_change",
        "save_candidate",
        "reject_change",
        "resume",
        "discard",
        "cancel_generation",
    ]
    action_source: Literal["button", "chat_command"]
    interaction_id: str = Field(min_length=1, max_length=128)
    draft_id: WorkflowDraftId | None = None
    draft_version: int | None = Field(default=None, ge=1, strict=True)
    parent_run_id: str | None = None
    visible_version_binding: Digest
    execution_digest: Digest
    result_id: str | None = None
    created_at: datetime

    @model_validator(mode="after")
    def subject(self):
        if self.decision == "start" and (self.draft_id is None or self.draft_version is None):
            raise ValueError("start decision requires an exact draft version")
        if self.decision == "start" and self.parent_run_id is not None:
            raise ValueError("initial start cannot carry a parent run")
        return self


class TaskPlanProvenance(PlanningValue):
    """Server-verified origin for a parentless run-local Revision."""

    workspace_id: WorkspaceId
    workflow_revision_id: WorkflowRevisionId
    origin: Literal["initial", "repair"]
    planning_binding_id: BindingId
    draft_id: WorkflowDraftId
    draft_version: int = Field(ge=1, strict=True)
    plan_decision_id: DecisionId
    root_task_run_id: str = Field(pattern=r"^task_[A-Za-z0-9_-]+$")
    context_digest: Digest
    settings_digest: Digest
    frozen_selections: tuple[FrozenNodeSelection, ...] = Field(min_length=1, max_length=16)
    repair_of_run_id: str | None = None
    repair_of_revision_id: WorkflowRevisionId | None = None
    created_at: datetime
    command_id: CommandId

    @model_validator(mode="after")
    def origin_fields(self):
        ids = tuple(item.node_id for item in self.frozen_selections)
        if len(ids) != len(set(ids)):
            raise ValueError("frozen selections must be unique per node")
        repairing = self.repair_of_run_id is not None or self.repair_of_revision_id is not None
        if self.origin == "initial" and repairing:
            raise ValueError("initial provenance cannot name a repair source")
        if self.origin == "repair" and not (self.repair_of_run_id and self.repair_of_revision_id):
            raise ValueError("repair provenance requires an explicit source run and revision")
        return self


class StartWorkflowPlanRequest(PlanningValue):
    command_id: CommandId
    session_id: SessionId
    draft_id: WorkflowDraftId
    draft_version: int = Field(ge=1, strict=True)
    execution_digest: Digest
    action_source: Literal["button", "chat_command"]
    interaction_id: str = Field(min_length=1, max_length=128)
    root_task_run_id: str | None = None
    expected_root_row_version: int | None = Field(default=None, ge=0, strict=True)


class PauseWorkflowPlanRequest(PlanningValue):
    command_id: CommandId
    session_id: SessionId
    expected_run_row_version: int | None = Field(default=None, ge=1, strict=True)


class PausePlanningGenerationRequest(PlanningValue):
    """Pause a non-terminal planning generation at a durable safe point."""

    command_id: CommandId
    session_id: SessionId
    planning_operation_id: OperationId
    expected_row_version: int | None = Field(default=None, ge=1, strict=True)


class ResumePlanningGenerationRequest(PlanningValue):
    """Resume one paused planning generation; never revives a terminal operation."""

    command_id: CommandId
    session_id: SessionId
    planning_operation_id: OperationId
    expected_row_version: int | None = Field(default=None, ge=1, strict=True)


class PrepareWorkflowChangeRequest(PlanningValue):
    command_id: CommandId
    session_id: SessionId
    origin_interaction_id: str = Field(min_length=1, max_length=128)
    action_source: Literal["button", "chat_command"]
    expected_run_row_version: int | None = Field(default=None, ge=1, strict=True)


class ApplyWorkflowChangeRequest(PlanningValue):
    command_id: CommandId
    session_id: SessionId
    decision: Literal["accept_change", "save_candidate", "reject_change"]
    action_source: Literal["button", "chat_command"]
    interaction_id: str = Field(min_length=1, max_length=128)
    candidate_digest: Digest
    expected_parent_row_version: int = Field(ge=1, strict=True)


class ResumeWorkflowPlanRequest(PlanningValue):
    command_id: CommandId
    session_id: SessionId
    expected_run_row_version: int | None = Field(default=None, ge=1, strict=True)


class PrepareWorkflowRepairRequest(PlanningValue):
    command_id: CommandId
    session_id: SessionId
    origin_interaction_id: str = Field(min_length=1, max_length=128)
    action_source: Literal["button", "chat_command"]


def visible_version_digest(draft_id: str, version: int) -> str:
    return sha256_digest(canonical_json_bytes({"draft_id": draft_id, "version": version}))


def change_candidate_digest(
    *,
    source_hash: str,
    past_node_ids: tuple[str, ...],
    parent_revision_id: str,
    parent_row_version: int,
) -> str:
    return sha256_digest(
        canonical_json_bytes(
            {
                "source_hash": source_hash,
                "past_node_ids": list(past_node_ids),
                "parent_revision_id": parent_revision_id,
                "parent_row_version": parent_row_version,
            }
        )
    )


def task_plan_execution_digest(
    *,
    source_hash: str,
    frozen_selections: tuple[FrozenNodeSelection, ...],
    settings_digest: str,
    context_digest: str,
) -> str:
    return sha256_digest(
        canonical_json_bytes(
            {
                "source_hash": source_hash,
                "frozen_selections": [item.model_dump(mode="json") for item in frozen_selections],
                "settings_digest": settings_digest,
                "context_digest": context_digest,
            }
        )
    )
