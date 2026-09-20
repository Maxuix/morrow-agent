"""Internal submit_node_result mechanism tool for structured Workflow outputs.

Never granted by definitions, prompts, Skills or Artifacts. Ordinary Direct
never receives it. Schema violations are in-loop tool errors the model can
correct; a conflicting second submission is refused without overwrite.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from morrow.core.capabilities import (
    OperationIntent,
    OperationKind,
    ToolCallContext,
    ToolHandlerOutcome,
)
from morrow.core.domain import canonical_json_bytes, sha256_digest
from morrow.core.execution import tool_declaration
from morrow.core.models import ToolEffect
from morrow.core.workflows.contracts import (
    DELIVERY_MAX_ITEMS,
    SUBMIT_NODE_RESULT_NAME,
    SUBMIT_SCHEMA_V1,
    SUBMIT_SCHEMA_VERSION,
    DeliverableRequest,
    EvidenceBundle,
    OutputContract,
    PlanArtifact,
    ReviewReport,
    SlotName,
    SynthesisReport,
)
from morrow.core.workflows.replan import ReplanRequest
from morrow.runtime.policy import ToolApproval, ToolExecutionPolicy
from morrow.runtime.tool_arguments import CuratedArgumentError
from morrow.runtime.tools import (
    RegisteredTool,
    ToolErrorCode,
    ToolExecutionError,
    make_tool,
)

_PAYLOADS = {
    "EvidenceBundle": EvidenceBundle,
    "PlanArtifact": PlanArtifact,
    "ReviewReport": ReviewReport,
    "SynthesisReport": SynthesisReport,
}


class SubmitNodeResultArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal[1] = 1
    outputs: dict[str, dict[str, Any]] = Field(default_factory=dict, max_length=64)
    replan: ReplanRequest | None = None

    @model_validator(mode="after")
    def nonempty_submission(self):
        if not self.outputs and self.replan is None:
            raise CuratedArgumentError(
                "empty_submission",
                "提交被拒绝：本节点声明了结构化输出槽位，请按工具说明提交全部必需槽位；"
                "空 outputs 永远无法结束节点。",
            )
        return self

    summary: str = Field(default="", max_length=1024)
    evidence_refs: tuple[str, ...] = Field(default=(), max_length=32)


class ReplanOnlySubmitNodeResultArguments(BaseModel):
    """Completion protocol for nodes that declare no structured output slots.

    Such nodes complete through the final assistant reply, which the runtime
    captures as the TextResult; this tool exists only for replan requests.
    The declared-but-empty ``outputs`` slot keeps the curated rejection on the
    pydantic pass: a JSON ``required`` entry for ``replan`` would answer the
    incident's empty submission with a generic missing-field error instead of
    the reviewed completion-protocol correction.
    """

    model_config = ConfigDict(
        extra="forbid", frozen=True, strict=True, title="SubmitNodeResultArguments"
    )

    schema_version: Literal[1] = 1
    outputs: dict[str, dict[str, Any]] = Field(default_factory=dict, max_length=64)
    replan: ReplanRequest | None = None

    @model_validator(mode="after")
    def replan_only_submission(self):
        if self.outputs:
            raise CuratedArgumentError(
                "undeclared_slot_submission",
                "提交被拒绝：本节点没有声明的结构化输出槽位，outputs 必须留空。"
                "本节点通过最终答复完成；本工具只用于提交重规划请求。",
            )
        if self.replan is None:
            raise CuratedArgumentError(
                "empty_submission",
                "提交被拒绝：本节点通过最终答复完成。请直接把最终报告作为普通回复发送，"
                "运行时会自动生成节点结果；本工具只用于提交重规划请求，"
                "调用时必须携带有效的 replan 字段。",
            )
        return self

    summary: str = Field(default="", max_length=1024)
    evidence_refs: tuple[str, ...] = Field(default=(), max_length=32)


def _bounded_deliverables(value: dict) -> dict:
    """Bound one registration map and reject duplicate canonical paths per slot."""

    total = 0
    for slot, items in value.items():
        paths = [item.path for item in items]
        if len(set(paths)) != len(paths):
            raise ValueError(f"slot {slot} registers the same path more than once")
        total += len(items)
    if not value:
        return value
    if total > DELIVERY_MAX_ITEMS:
        raise ValueError(f"a submission registers at most {DELIVERY_MAX_ITEMS} files")
    return value


class SubmitNodeResultArgumentsV2(BaseModel):
    """Submission protocol v2: declared structured slots plus explicit files.

    ``deliverables`` maps one *declared* output slot to the workspace files
    that slot delivers. The model only names paths and optional labels; the
    server derives name, MIME, size, hash and Artifact identity.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal[2] = 2
    outputs: dict[str, dict[str, Any]] = Field(default_factory=dict, max_length=64)
    deliverables: dict[SlotName, tuple[DeliverableRequest, ...]] = Field(
        default_factory=dict, max_length=DELIVERY_MAX_ITEMS
    )
    replan: ReplanRequest | None = None

    @field_validator("deliverables")
    @classmethod
    def bounded_deliverables(cls, value):
        return _bounded_deliverables(value)

    @model_validator(mode="after")
    def nonempty_submission(self):
        if not self.outputs and not self.deliverables and self.replan is None:
            raise CuratedArgumentError(
                "empty_submission",
                "提交被拒绝：请提交全部必需槽位，或登记至少一个交付文件；"
                "空 outputs 且无 deliverables 永远无法结束节点。",
            )
        return self

    summary: str = Field(default="", max_length=1024)
    evidence_refs: tuple[str, ...] = Field(default=(), max_length=32)


class ReplanOnlySubmitNodeResultArgumentsV2(BaseModel):
    """Protocol v2 for nodes that declare no structured output slots.

    Such nodes complete through the final assistant reply, which the runtime
    captures as the TextResult. This tool exists to register delivered files
    and to request replanning; it can never complete the node by itself.
    """

    model_config = ConfigDict(
        extra="forbid", frozen=True, strict=True, title="SubmitNodeResultArguments"
    )

    schema_version: Literal[2] = 2
    outputs: dict[str, dict[str, Any]] = Field(default_factory=dict, max_length=64)
    deliverables: dict[SlotName, tuple[DeliverableRequest, ...]] = Field(
        default_factory=dict, max_length=DELIVERY_MAX_ITEMS
    )
    replan: ReplanRequest | None = None

    @field_validator("deliverables")
    @classmethod
    def bounded_deliverables(cls, value):
        return _bounded_deliverables(value)

    @model_validator(mode="after")
    def replan_only_submission(self):
        if self.outputs:
            raise CuratedArgumentError(
                "undeclared_slot_submission",
                "提交被拒绝：本节点没有声明的结构化输出槽位，outputs 必须留空。"
                "交付文件请登记在 deliverables；本节点通过最终答复完成。",
            )
        if not self.deliverables and self.replan is None:
            raise CuratedArgumentError(
                "empty_submission",
                "提交被拒绝：本节点通过最终答复完成。请直接把最终报告作为普通回复发送，"
                "运行时会自动生成节点结果；本工具只用于登记交付文件或提交重规划请求，"
                "调用时必须携带至少一个 deliverables 或有效的 replan 字段。",
            )
        return self

    summary: str = Field(default="", max_length=1024)
    evidence_refs: tuple[str, ...] = Field(default=(), max_length=32)


def _deliverables_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "maxProperties": DELIVERY_MAX_ITEMS,
        "additionalProperties": {
            "type": "array",
            "minItems": 1,
            "maxItems": DELIVERY_MAX_ITEMS,
            "items": DeliverableRequest.model_json_schema(),
        },
    }


def submit_node_result_provider_schema(
    output_contracts: Iterable[OutputContract],
    *,
    protocol_version: int = SUBMIT_SCHEMA_VERSION,
) -> dict[str, Any]:
    """Project one node's exact structured output contract onto the Provider wire."""

    if protocol_version not in (SUBMIT_SCHEMA_V1, SUBMIT_SCHEMA_VERSION):
        raise ValueError("unsupported submission protocol version")
    v2 = protocol_version != SUBMIT_SCHEMA_V1
    structured = tuple(contract for contract in output_contracts if contract.kind in _PAYLOADS)
    if not structured:
        model = ReplanOnlySubmitNodeResultArgumentsV2 if v2 else ReplanOnlySubmitNodeResultArguments
        schema = model.model_json_schema()
        schema["properties"]["outputs"] = {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
            "minProperties": 0,
        }
        if v2:
            schema["properties"]["deliverables"] = _deliverables_schema()
        return schema
    model = SubmitNodeResultArgumentsV2 if v2 else SubmitNodeResultArguments
    schema = model.model_json_schema()
    output_properties = {
        contract.slot: _PAYLOADS[contract.kind].model_json_schema() for contract in structured
    }
    output_schema: dict[str, Any] = {
        "type": "object",
        "properties": output_properties,
        "additionalProperties": False,
        "minProperties": 1 if structured else 0,
    }
    required = sorted(
        contract.slot for contract in structured if contract.required_for_node_completion
    )
    if required:
        output_schema["required"] = required
    schema["properties"]["outputs"] = output_schema
    if v2:
        schema["properties"]["deliverables"] = _deliverables_schema()
    return schema


_REPLAN_ONLY_DESCRIPTION = (
    "Completion protocol for this node: it declares NO structured output slots. "
    "Finish the node by sending your final report as an ordinary assistant reply; "
    "the runtime captures that reply as the node result automatically. This tool "
    "can never complete the node: an empty submission is always rejected. Call it "
    "only to request replanning, and always with a filled 'replan' object (reason, "
    "evidence refs, affected remaining work; never a graph, permissions, risk, or "
    "a start/apply decision)."
)

_REPLAN_ONLY_DESCRIPTION_V2 = (
    "Completion protocol for this node: it declares NO structured output slots. "
    "Finish the node by sending your final report as an ordinary assistant reply; "
    "the runtime captures that reply as the node result automatically. Register "
    "every file this node delivers under its declared slot in the 'deliverables' "
    "map (a workspace-relative path plus an optional label); the runtime reads and "
    "verifies those files itself, so never claim a delivered file only in prose. "
    "This tool can never complete the node by itself: an empty submission is always "
    "rejected. Call it to register deliverables or to request replanning with a "
    "filled 'replan' object (reason, evidence refs, affected remaining work; never "
    "a graph, permissions, risk, or a start/apply decision)."
)

_DELIVERY_INSTRUCTIONS_V2 = (
    " Register every delivered file under the declared output slot it belongs to in "
    "the 'deliverables' map (workspace-relative path plus an optional label); the "
    "runtime reads and verifies those files itself, so never claim a delivered file "
    "only in prose. A plain text answer needs no deliverable."
)


def make_submit_node_result_tool(
    hooks,
    output_contracts: Iterable[OutputContract],
    *,
    protocol_version: int = SUBMIT_SCHEMA_VERSION,
) -> RegisteredTool:
    if protocol_version not in (SUBMIT_SCHEMA_V1, SUBMIT_SCHEMA_VERSION):
        raise ValueError("unsupported submission protocol version")
    v2 = protocol_version != SUBMIT_SCHEMA_V1
    contracts = tuple(output_contracts)
    structured = tuple(contract for contract in contracts if contract.kind in _PAYLOADS)
    replan_only = not structured
    if replan_only:
        description = _REPLAN_ONLY_DESCRIPTION_V2 if v2 else _REPLAN_ONLY_DESCRIPTION
        arguments_model: type[BaseModel] = (
            ReplanOnlySubmitNodeResultArgumentsV2 if v2 else ReplanOnlySubmitNodeResultArguments
        )
    else:
        arguments_model = SubmitNodeResultArgumentsV2 if v2 else SubmitNodeResultArguments
        description = (
            "Submit this node's declared structured outputs. Call once with every required "
            "structured slot. The final message is transcript only and is never parsed. "
            "Declared slots: "
            + ", ".join(
                f"{contract.slot} ({contract.kind}"
                f"{' required' if contract.required_for_node_completion else ' optional'})"
                for contract in structured
            )
            + "."
        )
        if any(contract.kind == "TextResult" for contract in contracts):
            description += (
                " TextResult outputs are never submitted here: they are generated from"
                " your final reply when the node completes."
            )
        if v2:
            description += _DELIVERY_INSTRUCTIONS_V2
        description += (
            " Optional replan requests are bounded facts (reason, evidence refs, "
            "affected remaining work). Never include a graph, permissions, risk, "
            "or a start/apply decision. Finish this node normally; never wait for "
            "global replanning."
        )

    async def handler(arguments: BaseModel, context) -> dict[str, object] | ToolHandlerOutcome:
        call_id = getattr(context, "call_id", None)
        if isinstance(arguments, ReplanOnlySubmitNodeResultArguments):
            arguments = SubmitNodeResultArguments(
                schema_version=1,
                outputs={},
                replan=arguments.replan,
                summary=arguments.summary,
                evidence_refs=arguments.evidence_refs,
            )
        elif isinstance(arguments, ReplanOnlySubmitNodeResultArgumentsV2):
            arguments = SubmitNodeResultArgumentsV2(
                schema_version=2,
                outputs={},
                deliverables=arguments.deliverables,
                replan=arguments.replan,
                summary=arguments.summary,
                evidence_refs=arguments.evidence_refs,
            )
        receipt = hooks.submit_node_result(arguments, call_id=call_id)
        # The submitting ToolExecution owns the delivered bytes: its durable
        # references are what Doctor/Cleanup see, never a JSON id alone.
        refs = tuple(getattr(hooks, "submission_artifact_refs", ()) or ())
        if refs:
            return ToolHandlerOutcome(payload=receipt, artifact_refs=refs)
        return receipt

    def resolve(
        _arguments: SubmitNodeResultArguments, _context: ToolCallContext
    ) -> OperationIntent:
        return OperationIntent(kind=OperationKind.INTERNAL_READ, effect=ToolEffect.NONE)

    return make_tool(
        name=SUBMIT_NODE_RESULT_NAME,
        description=description,
        arguments_model=arguments_model,
        provider_schema=submit_node_result_provider_schema(
            contracts, protocol_version=protocol_version
        ),
        handler=handler,
        context_handler=handler,
        intent_resolver=resolve,
        execution_policy=ToolExecutionPolicy(approval=ToolApproval.NEVER),
        recovery_declaration=tool_declaration(SUBMIT_NODE_RESULT_NAME),
    )


def delivery_request_payload(deliverables: dict) -> dict[str, list[dict[str, object]]]:
    """Canonical request-only view of a registration map (no server facts)."""

    return {
        slot: [
            {"path": item.path, "label": item.label}
            for item in sorted(items, key=lambda item: item.path)
        ]
        for slot, items in sorted(deliverables.items())
    }


def submission_digest(
    outputs: dict[str, object],
    summary: str,
    evidence_refs: tuple[str, ...],
    replan=None,
    deliverables: dict[str, object] | None = None,
) -> str:
    """One request digest; v1 callers keep their exact frozen input shape."""

    body: dict[str, object] = {
        **({"replan": replan.model_dump(mode="json")} if replan else {}),
        "outputs": {name: payload.model_dump(mode="json") for name, payload in outputs.items()},
        "summary": summary,
        "evidence_refs": list(evidence_refs),
    }
    if deliverables is not None:
        body["deliverables"] = deliverables
    return sha256_digest(canonical_json_bytes(body))


def parse_submitted_payload(kind: str, raw: dict[str, Any], *, slot: str):
    model = _PAYLOADS.get(kind)
    if model is None:
        raise ToolExecutionError(
            ToolErrorCode.INVALID_ARGUMENTS,
            f"contract kind {kind} is not submitted through submit_node_result",
        )
    try:
        return model.model_validate(raw)
    except ValidationError as exc:
        details = tuple(
            {
                "path": ".".join(("outputs", slot, *(str(part) for part in error["loc"]))),
                "type": str(error["type"]),
            }
            for error in exc.errors(include_url=False, include_input=False)[:8]
        )
        raise ToolExecutionError(
            ToolErrorCode.INVALID_ARGUMENTS,
            f"schema violation: {exc.error_count()} field error(s)",
            details=details,
        ) from None
