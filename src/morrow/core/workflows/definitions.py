"""Normalized source and immutable compiler output representation, with no IO."""

from __future__ import annotations

import unicodedata
from datetime import datetime
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator

from morrow.core.agent_definitions import (
    DefinitionId,
    Digest,
    OpaqueId,
    ToolRequirement,
    WorkspaceId,
)
from morrow.core.agent_runs import AgentDefinitionRef
from morrow.core.domain import (
    TASK_OUTCOME_ARTIFACT_MAX_REFS,
    canonical_json_bytes,
    refuse_secret_material,
    sha256_digest,
)
from morrow.core.models import ModelRef, ProtocolModel
from morrow.core.workflows.contracts import (
    InputBinding,
    NodeOutputRef,
    OutputContract,
    SlotName,
    TaskContract,
    TaskContractRef,
)

WorkflowRevisionId = Annotated[str, Field(pattern=r"^wrev_[A-Za-z0-9_-]+$")]


class WorkflowBudget(ProtocolModel):
    max_agent_generation_requests: int = Field(gt=0, strict=True)
    default_node_max_agent_generation_requests: int = Field(gt=0, strict=True)
    admission_timeout_seconds: float = Field(gt=0, allow_inf_nan=False)
    max_concurrency: int = Field(gt=0, strict=True)

    @field_validator("admission_timeout_seconds", mode="before")
    @classmethod
    def numeric_duration(cls, value):
        if type(value) not in {int, float}:
            raise ValueError("admission duration must be numeric")
        return value


class WorkflowEdge(ProtocolModel):
    from_node_id: SlotName
    to_node_id: SlotName


class AgentNodeSource(ProtocolModel):
    node_id: SlotName
    agent_definition_ref: AgentDefinitionRef
    task_contract: TaskContract
    input_bindings: tuple[InputBinding, ...] = Field(default=(), max_length=64)
    output_contracts: tuple[OutputContract, ...] = Field(min_length=1, max_length=64)
    access_mode: Literal["read", "write"]
    conversation_scope: Literal["isolated", "invoking_session"] = "isolated"
    tool_requirements: tuple[ToolRequirement, ...] | None = Field(default=None, max_length=128)
    max_agent_generation_requests: int | None = Field(default=None, gt=0, strict=True)

    @field_validator("input_bindings", "output_contracts", "tool_requirements")
    @classmethod
    def unique_names(cls, values, info):
        if values is None:
            return None
        key = {
            "input_bindings": "input_name",
            "output_contracts": "slot",
            "tool_requirements": "name",
        }[info.field_name]
        if len({getattr(value, key) for value in values}) != len(values):
            raise ValueError("node-local names must be unique")
        return tuple(sorted(values, key=lambda value: getattr(value, key)))


class AgentNode(AgentNodeSource):
    resolved_model_ref: ModelRef
    # Compiler-frozen merge of Definition and node declarations; the source
    # overlay stays in tool_requirements so any source edit changes the hash.
    resolved_tool_requirements: tuple[ToolRequirement, ...] = Field(default=(), max_length=128)
    declared_node_max_agent_generation_requests: int = Field(gt=0, strict=True)

    @field_validator("resolved_tool_requirements")
    @classmethod
    def unique_resolved_tools(cls, values):
        if len({item.name for item in values}) != len(values):
            raise ValueError("node-local names must be unique")
        return tuple(sorted(values, key=lambda item: item.name))


class WorkflowMetadata(ProtocolModel):
    workflow_definition_id: DefinitionId
    name: str = Field(min_length=1, max_length=128)
    description: str = Field(default="", max_length=2048)
    tags: tuple[SlotName, ...] = Field(default=(), max_length=32)
    origin: Literal["user", "builtin"] = "user"
    input_contract: TaskContractRef = Field(default_factory=TaskContractRef)
    required_outputs: tuple[NodeOutputRef, ...] = Field(
        min_length=1, max_length=TASK_OUTCOME_ARTIFACT_MAX_REFS
    )
    edges: tuple[WorkflowEdge, ...] = Field(default=(), max_length=1024)

    @field_validator("name", "description")
    @classmethod
    def normalized_text(cls, value, info):
        value = unicodedata.normalize("NFC", value)
        if (info.field_name == "name" and not value.strip()) or any(
            unicodedata.category(c) in {"Cc", "Cf"} and c not in "\n\r\t" for c in value
        ):
            raise ValueError("invalid Workflow metadata text")
        return value

    @field_validator("tags", "required_outputs", "edges")
    @classmethod
    def canonical_set(cls, value):
        if len(set(value)) != len(value):
            raise ValueError("Workflow references must be unique")
        return tuple(
            sorted(value, key=lambda v: str(v) if isinstance(v, str) else v.model_dump_json())
        )

    @model_validator(mode="after")
    def safe_metadata(self):
        if self.workflow_definition_id.startswith("builtin_") != (self.origin == "builtin"):
            raise ValueError("Workflow origin mismatch")
        payload = canonical_json_bytes(self.model_dump(mode="json"))
        if len(payload) > 256 * 1024:
            raise ValueError("Workflow definition exceeds its payload budget")
        refuse_secret_material(payload, label="Workflow", profile="workflow_value_sensitive")
        return self


class WorkflowDefinitionSource(WorkflowMetadata):
    default_budget: WorkflowBudget
    nodes: tuple[AgentNodeSource, ...] = Field(min_length=1, max_length=128)

    @field_validator("nodes")
    @classmethod
    def canonical_nodes(cls, values):
        if len({node.node_id for node in values}) != len(values):
            raise ValueError("Workflow node IDs must be unique")
        return tuple(sorted(values, key=lambda node: node.node_id))

    @property
    def content_hash(self):
        return sha256_digest(canonical_json_bytes(self.model_dump(mode="json")))


class WorkflowDefinitionDocument(ProtocolModel):
    schema_version: Literal[1] = 1
    revision: int = Field(default=0, ge=0, strict=True)
    definitions: tuple[WorkflowDefinitionSource, ...] = Field(default=(), max_length=128)

    @field_validator("schema_version", mode="before")
    @classmethod
    def strict_schema(cls, value):
        if type(value) is not int:
            raise ValueError("definition schema version must be an integer")
        return value

    @field_validator("definitions")
    @classmethod
    def user_definitions(cls, values):
        if len({v.workflow_definition_id for v in values}) != len(values) or any(
            v.origin != "user" for v in values
        ):
            raise ValueError("Workflow sources must have unique user IDs")
        return tuple(sorted(values, key=lambda v: v.workflow_definition_id))


class CompiledWorkflow(WorkflowMetadata):
    """Candidate representation consumed by the sole (Subplan 3) publication service."""

    nodes: tuple[AgentNode, ...] = Field(min_length=1, max_length=128)
    entry_nodes: tuple[SlotName, ...] = Field(min_length=1, max_length=128)
    terminal_nodes: tuple[SlotName, ...] = Field(min_length=1, max_length=128)
    budget: WorkflowBudget
    compiler_version: str = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def graph_references(self):
        nodes = {n.node_id: n for n in self.nodes}
        edges = {(e.from_node_id, e.to_node_id) for e in self.edges}
        if any(a not in nodes or b not in nodes or a == b for a, b in edges):
            raise ValueError("Workflow edge reference is invalid")
        if set(self.entry_nodes) != nodes.keys() - {b for _, b in edges} or set(
            self.terminal_nodes
        ) != nodes.keys() - {a for a, _ in edges}:
            raise ValueError("Workflow entry/terminal facts do not match its edges")
        outputs = {(n.node_id, slot.slot): slot for n in self.nodes for slot in n.output_contracts}
        for ref in self.required_outputs:
            slot = outputs.get((ref.node_id, ref.output_slot))
            if slot is None or not slot.required_for_node_completion:
                raise ValueError("export must reference a completion-required output")
        for node in self.nodes:
            for binding in node.input_bindings:
                if binding.source == "workflow_input":
                    continue
                ref = binding.node_output
                slot = outputs.get((ref.node_id, ref.output_slot))
                if slot is None or not slot.required_for_node_completion:
                    raise ValueError("binding must reference a completion-required output")
                if (ref.node_id, node.node_id) not in edges:
                    raise ValueError("node binding requires a same-direction edge")
                if (slot.kind, slot.version) != (binding.accepts.kind, binding.accepts.version):
                    raise ValueError("node binding contract mismatch")
        return self

    @field_validator("nodes")
    @classmethod
    def canonical_nodes(cls, values):
        return WorkflowDefinitionSource.canonical_nodes(values)

    @field_validator("entry_nodes", "terminal_nodes")
    @classmethod
    def canonical_ids(cls, values):
        if len(set(values)) != len(values):
            raise ValueError("node IDs must be unique")
        return tuple(sorted(values))


def compiled_content_hash(candidate: CompiledWorkflow) -> str:
    """One canonical digest owner, excluding operational identity and lineage."""
    body = {name: getattr(candidate, name) for name in CompiledWorkflow.model_fields}
    normalized = CompiledWorkflow(**body)
    return sha256_digest(canonical_json_bytes(normalized.model_dump(mode="json")))


class WorkflowRevision(CompiledWorkflow):
    workflow_revision_id: WorkflowRevisionId
    workspace_id: WorkspaceId
    # Published revisions use the positive Definition-head sequence. Detached,
    # run-local revisions use a negative sequence so they can never consume a
    # future published slot; zero belongs to neither namespace.
    revision: int = Field(strict=True)
    parent_workflow_revision_id: WorkflowRevisionId | None = None
    content_hash: Digest
    source_revision: int = Field(ge=0, strict=True)
    source_hash: Digest
    created_by: OpaqueId
    created_at: datetime

    @field_validator("revision")
    @classmethod
    def nonzero_revision_namespace(cls, value: int) -> int:
        if value == 0:
            raise ValueError("Workflow Revision number cannot be zero")
        return value

    @model_validator(mode="after")
    def immutable_hash(self):
        if self.content_hash != compiled_content_hash(self):
            raise ValueError("Workflow revision hash mismatch")
        if self.parent_workflow_revision_id == self.workflow_revision_id:
            raise ValueError("Workflow revision cannot parent itself")
        return self


class WorkflowDefinitionHead(ProtocolModel):
    workspace_id: WorkspaceId
    workflow_definition_id: DefinitionId
    workflow_revision_id: WorkflowRevisionId
    source_revision: int = Field(ge=0, strict=True)
    source_hash: Digest
    enabled: bool = Field(default=True, strict=True)
    row_version: int = Field(ge=1, strict=True)


class WorkflowRevisionRevocation(ProtocolModel):
    workspace_id: WorkspaceId
    workflow_revision_id: WorkflowRevisionId
    reason: str = Field(min_length=1, max_length=256)
    command_id: OpaqueId
    created_at: datetime

    @field_validator("reason")
    @classmethod
    def safe_reason(cls, value):
        value = " ".join(value.split())
        if not value:
            raise ValueError("revocation reason is required")
        refuse_secret_material(value, label="revocation reason", profile="workflow_value_sensitive")
        return value
