"""The two bounded v1 payloads and exact slot/input references."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator

from morrow.core.agent_definitions import Digest
from morrow.core.domain import (
    TaskOutcomeEvidenceRef,
    canonical_json_bytes,
    refuse_secret_material,
    sha256_digest,
)
from morrow.core.models import ProtocolModel

SlotName = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")]


class ContractRef(ProtocolModel):
    kind: Literal["TaskContract", "TextResult"]
    version: Literal[1] = 1

    @field_validator("version", mode="before")
    @classmethod
    def strict_version(cls, value):
        if type(value) is not int:
            raise ValueError("contract version must be an integer")
        return value


class TaskContractRef(ContractRef):
    kind: Literal["TaskContract"] = "TaskContract"


class NodeOutputRef(ProtocolModel):
    node_id: SlotName
    output_slot: SlotName


class WorkflowInputBinding(ProtocolModel):
    source: Literal["workflow_input"]
    input_name: SlotName
    accepts: TaskContractRef
    workflow_input: Literal["task"]


class NodeOutputBinding(ProtocolModel):
    source: Literal["node_output"]
    input_name: SlotName
    accepts: ContractRef
    node_output: NodeOutputRef


InputBinding = Annotated[WorkflowInputBinding | NodeOutputBinding, Field(discriminator="source")]


class OutputContract(ContractRef):
    kind: Literal["TextResult"] = "TextResult"
    slot: SlotName
    required_for_node_completion: bool = Field(default=True, strict=True)


class TaskContract(ProtocolModel):
    """Bounded input, never a permission grant or a copy of Session history."""

    objective: str = Field(min_length=1, max_length=4096)
    scope: tuple[str, ...] = Field(default=(), max_length=32)
    constraints: tuple[str, ...] = Field(default=(), max_length=32)
    source_refs: tuple[TaskOutcomeEvidenceRef, ...] = Field(default=(), max_length=32)

    @field_validator("objective")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("task objective must not be blank")
        return value

    @model_validator(mode="after")
    def safe_payload(self):
        payload = canonical_json_bytes(self.model_dump(mode="json"))
        if len(payload) > 16384:
            raise ValueError("TaskContract exceeds its payload budget")
        refuse_secret_material(payload, label="TaskContract", profile="workflow_value_sensitive")
        return self


class TextResult(ProtocolModel):
    """Reference to the owning Session's final Assistant, not a second transcript."""

    final_assistant_record_id: Annotated[str, Field(pattern=r"^rec_[A-Za-z0-9_-]+$")]
    final_assistant_sha256: Digest
    excerpt: str = Field(max_length=4096)
    content_complete: bool = Field(strict=True)

    @model_validator(mode="after")
    def safe_payload(self):
        refuse_secret_material(
            canonical_json_bytes(self.model_dump(mode="json")),
            label="TextResult",
            profile="workflow_value_sensitive",
        )
        return self


class ArtifactBinding(ProtocolModel):
    name: SlotName
    artifact_id: Annotated[str, Field(pattern=r"^art_[A-Za-z0-9_-]+$")]
    contract: ContractRef


def node_output_artifact_id(node_run_id: str, output_slot: str) -> str:
    """Stable byte-store identity for one NodeRun output slot."""
    return "art_" + sha256_digest(canonical_json_bytes([node_run_id, output_slot]))[:32]


def workflow_input_artifact_id(command_id: str) -> str:
    """Stable byte-store identity for the TaskContract one Start command publishes."""
    return "art_" + sha256_digest(canonical_json_bytes([command_id, "workflow_input"]))[:32]
