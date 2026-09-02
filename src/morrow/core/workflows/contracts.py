"""Bounded Workflow payloads and exact slot/input references."""

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
BoundedLine = Annotated[str, Field(min_length=1, max_length=1024)]
ArtifactId = Annotated[str, Field(pattern=r"^art_[A-Za-z0-9_-]+$")]
ToolExecutionId = Annotated[str, Field(pattern=r"^tex_[A-Za-z0-9_-]+$")]

SUBMIT_NODE_RESULT_NAME = "submit_node_result"
SUBMIT_SCHEMA_VERSION = 1
CAPTURE_SCHEMA_VERSION = 1
TEXT_OUTPUT_KINDS = frozenset({"TextResult"})
SUBMISSION_OUTPUT_KINDS = frozenset({"EvidenceBundle", "ReviewReport"})
CAPTURE_OUTPUT_KINDS = frozenset({"ImplementationPatch", "TestReport"})
STRUCTURED_OUTPUT_KINDS = SUBMISSION_OUTPUT_KINDS | CAPTURE_OUTPUT_KINDS
RESULT_DRIVING_KIND = "ReviewReport"
MECHANISM_TOOL_NAMES = frozenset({SUBMIT_NODE_RESULT_NAME})

WorkflowContractKind = Literal[
    "TaskContract",
    "TextResult",
    "EvidenceBundle",
    "ImplementationPatch",
    "TestReport",
    "ReviewReport",
    "ChangeCapture",
]
NodeOutputKind = Literal[
    "TextResult",
    "EvidenceBundle",
    "ImplementationPatch",
    "TestReport",
    "ReviewReport",
]


class ContractRef(ProtocolModel):
    kind: WorkflowContractKind
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
    kind: NodeOutputKind = "TextResult"
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


class EvidenceBundle(ProtocolModel):
    """Explorer evidence; never a permission grant or a second transcript."""

    findings: tuple[BoundedLine, ...] = Field(default=(), max_length=32)
    source_refs: tuple[BoundedLine, ...] = Field(default=(), max_length=32)
    relevant_paths: tuple[BoundedLine, ...] = Field(default=(), max_length=64)
    uncertainties: tuple[BoundedLine, ...] = Field(default=(), max_length=32)

    @model_validator(mode="after")
    def safe_payload(self):
        _refuse_workflow_payload(self, label="EvidenceBundle", budget=16384)
        return self


class ImplementationPatch(ProtocolModel):
    """Coder change evidence assembled from durable capture refs, never parsed text."""

    changed_paths: tuple[BoundedLine, ...] = Field(default=(), max_length=256)
    change_refs: tuple[ArtifactId, ...] = Field(default=(), max_length=64)
    content_complete: bool = Field(strict=True)
    rationale: str = Field(default="", max_length=4096)
    omission_reason: str | None = Field(default=None, max_length=256)

    @model_validator(mode="after")
    def safe_payload(self):
        _refuse_workflow_payload(self, label="ImplementationPatch", budget=16384)
        return self


class TestReportItem(ProtocolModel):
    """One ValidationFact captured at handler completion."""

    validator_kind: Annotated[str, Field(min_length=1, max_length=64)]
    scope: Annotated[str, Field(min_length=1, max_length=512)]
    status: Literal["passed", "failed", "timeout", "cancelled", "inconclusive"]
    exit_code: int | None = Field(default=None, ge=0, le=255)
    evidence_summary: Annotated[str, Field(min_length=1, max_length=80)]
    output_ref: ArtifactId | None = None
    content_complete: bool = Field(strict=True)
    omission_reason: str | None = Field(default=None, max_length=256)
    tool_execution_id: ToolExecutionId


class TestReport(ProtocolModel):
    """Per-execution or aggregate validation evidence; missing output text is not failure."""

    items: tuple[TestReportItem, ...] = Field(default=(), max_length=64)
    content_complete: bool = Field(strict=True)
    omission_reason: str | None = Field(default=None, max_length=256)

    @model_validator(mode="after")
    def safe_payload(self):
        _refuse_workflow_payload(self, label="TestReport", budget=16384)
        return self


class ReviewReport(ProtocolModel):
    """Reviewer product evidence; a blocking verdict is not an execution failure."""

    verdict: Literal["approve", "request_changes", "block"]
    findings: tuple[BoundedLine, ...] = Field(default=(), max_length=32)
    severity: Literal["info", "warning", "blocking"] = "info"
    evidence_refs: tuple[BoundedLine, ...] = Field(default=(), max_length=32)
    required_changes: tuple[BoundedLine, ...] = Field(default=(), max_length=32)
    optional_notes: tuple[BoundedLine, ...] = Field(default=(), max_length=32)

    @property
    def blocking(self) -> bool:
        return self.verdict != "approve"

    @model_validator(mode="after")
    def safe_payload(self):
        _refuse_workflow_payload(self, label="ReviewReport", budget=16384)
        return self


class ChangeCapture(ProtocolModel):
    """Per-mutation capture payload published at handler completion; not a node contract."""

    schema_version: Literal[1] = 1
    path: BoundedLine
    operation: Annotated[str, Field(min_length=1, max_length=32)]
    status: Annotated[str, Field(min_length=1, max_length=32)]
    before_sha256: Digest | None = None
    after_sha256: Digest | None = None
    before_size: int | None = Field(default=None, ge=0)
    after_size: int | None = Field(default=None, ge=0)
    unified_diff: str | None = Field(default=None, max_length=1_048_576)
    content_complete: bool = Field(strict=True)
    omission_reason: str | None = Field(default=None, max_length=256)

    @model_validator(mode="after")
    def safe_payload(self):
        _refuse_workflow_payload(self, label="ChangeCapture", budget=1_048_576 + 2048)
        return self


class ArtifactBinding(ProtocolModel):
    name: SlotName
    artifact_id: ArtifactId
    contract: ContractRef


WorkflowPayload = (
    TaskContract | TextResult | EvidenceBundle | ImplementationPatch | TestReport | ReviewReport
)
NODE_OUTPUT_PAYLOAD_TYPES = (
    TextResult,
    EvidenceBundle,
    ImplementationPatch,
    TestReport,
    ReviewReport,
)


def _refuse_workflow_payload(model: ProtocolModel, *, label: str, budget: int) -> None:
    payload = canonical_json_bytes(model.model_dump(mode="json"))
    if len(payload) > budget:
        raise ValueError(f"{label} exceeds its payload budget")
    refuse_secret_material(payload, label=label, profile="workflow_value_sensitive")


def workflow_payload_excerpt(payload: object) -> str:
    """Bounded inspectable excerpt; never a second transcript."""

    if isinstance(payload, TaskContract):
        return payload.objective
    if isinstance(payload, TextResult):
        return payload.excerpt
    if isinstance(payload, EvidenceBundle):
        return "; ".join(payload.findings)[:4096] or "evidence"
    if isinstance(payload, ImplementationPatch):
        return (payload.rationale or ",".join(payload.changed_paths) or "patch")[:4096]
    if isinstance(payload, TestReport):
        if payload.items:
            return payload.items[0].evidence_summary
        return payload.omission_reason or "tests"
    if isinstance(payload, ReviewReport):
        return (f"{payload.verdict}: " + "; ".join(payload.findings))[:4096]
    if isinstance(payload, ChangeCapture):
        return f"{payload.operation} {payload.path}"[:4096]
    return type(payload).__name__


def parse_workflow_payload(kind: str, content: bytes):
    """Rehydrate one typed Workflow payload from durable bytes."""

    mapping = {
        "TaskContract": TaskContract,
        "TextResult": TextResult,
        "EvidenceBundle": EvidenceBundle,
        "ImplementationPatch": ImplementationPatch,
        "TestReport": TestReport,
        "ReviewReport": ReviewReport,
    }
    model = mapping.get(kind)
    if model is None:
        raise ValueError(f"unsupported Workflow contract kind {kind}")
    return model.model_validate_json(content)


def node_output_artifact_id(node_run_id: str, output_slot: str) -> str:
    """Stable byte-store identity for one NodeRun output slot."""
    return "art_" + sha256_digest(canonical_json_bytes([node_run_id, output_slot]))[:32]


def workflow_input_artifact_id(command_id: str) -> str:
    """Stable byte-store identity for the TaskContract one Start command publishes."""
    return "art_" + sha256_digest(canonical_json_bytes([command_id, "workflow_input"]))[:32]


def capture_artifact_id(
    tool_execution_id: str,
    role: str,
    schema_version: int = 1,
    *,
    path: str | None = None,
) -> str:
    """Stable byte-store identity for one ToolExecution capture role.

    ChangeCapture is per-path under one ToolExecution; pass ``path`` so two
    mutations do not collide. Validation reports omit path.
    """
    identity: list[object] = [tool_execution_id, role, schema_version]
    if path is not None:
        identity.append(path)
    return "art_" + sha256_digest(canonical_json_bytes(identity))[:32]


def node_submission_artifact_id(node_run_id: str) -> str:
    """Stable identity for the one valid structured submission of a NodeRun."""
    return (
        "art_" + sha256_digest(canonical_json_bytes([node_run_id, "node_result_submission"]))[:32]
    )
