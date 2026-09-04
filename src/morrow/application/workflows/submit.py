"""Internal submit_node_result mechanism tool for structured Workflow outputs.

Never granted by definitions, prompts, Skills or Artifacts. Ordinary Direct
never receives it. Schema violations are in-loop tool errors the model can
correct; a conflicting second submission is refused without overwrite.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from morrow.core.capabilities import OperationIntent, OperationKind, ToolCallContext
from morrow.core.domain import canonical_json_bytes, sha256_digest
from morrow.core.execution import tool_declaration
from morrow.core.models import ToolEffect
from morrow.core.workflows.contracts import (
    SUBMIT_NODE_RESULT_NAME,
    EvidenceBundle,
    OutputContract,
    PlanArtifact,
    ReviewReport,
    SynthesisReport,
)
from morrow.core.workflows.replan import ReplanRequest
from morrow.runtime.policy import ToolApproval, ToolExecutionPolicy
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
            raise ValueError("outputs or a replan request is required")
        return self

    summary: str = Field(default="", max_length=1024)
    evidence_refs: tuple[str, ...] = Field(default=(), max_length=32)


class LegacySubmitNodeResultArguments(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, strict=True, title="SubmitNodeResultArguments"
    )
    schema_version: Literal[1] = 1
    outputs: dict[str, dict[str, Any]] = Field(min_length=1, max_length=64)
    summary: str = Field(default="", max_length=1024)
    evidence_refs: tuple[str, ...] = Field(default=(), max_length=32)


def submit_node_result_provider_schema(
    output_contracts: Iterable[OutputContract],
    *,
    allow_replan=True,
) -> dict[str, Any]:
    """Project one node's exact structured output contract onto the Provider wire."""

    structured = tuple(contract for contract in output_contracts if contract.kind in _PAYLOADS)
    arguments_model = SubmitNodeResultArguments if allow_replan else LegacySubmitNodeResultArguments
    schema = arguments_model.model_json_schema()
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
    return schema


def make_submit_node_result_tool(
    hooks, output_contracts: Iterable[OutputContract], *, allow_replan=True
) -> RegisteredTool:
    structured = tuple(contract for contract in output_contracts if contract.kind in _PAYLOADS)

    async def handler(arguments: SubmitNodeResultArguments, context) -> dict[str, object]:
        del context
        if isinstance(arguments, LegacySubmitNodeResultArguments):
            arguments = SubmitNodeResultArguments.model_validate(arguments.model_dump())
        return hooks.submit_node_result(arguments)

    def resolve(
        _arguments: SubmitNodeResultArguments, _context: ToolCallContext
    ) -> OperationIntent:
        return OperationIntent(kind=OperationKind.INTERNAL_READ, effect=ToolEffect.NONE)

    return make_tool(
        name=SUBMIT_NODE_RESULT_NAME,
        description=(
            "Submit this node's declared structured outputs. Call once with every required "
            "structured slot. The final message is transcript only and is never parsed. "
            "Declared slots: "
            + ", ".join(
                f"{contract.slot} ({contract.kind}"
                f"{' required' if contract.required_for_node_completion else ' optional'})"
                for contract in structured
            )
            + "."
            + (
                " Optional replan requests are evidence for a future task correction. "
                "Finish this node normally; never wait for global replanning."
                if allow_replan
                else ""
            )
        ),
        arguments_model=SubmitNodeResultArguments
        if allow_replan
        else LegacySubmitNodeResultArguments,
        provider_schema=submit_node_result_provider_schema(structured, allow_replan=allow_replan),
        handler=handler,
        context_handler=handler,
        intent_resolver=resolve,
        execution_policy=ToolExecutionPolicy(approval=ToolApproval.NEVER),
        recovery_declaration=tool_declaration(SUBMIT_NODE_RESULT_NAME),
    )


def submission_digest(
    outputs: dict[str, object], summary: str, evidence_refs: tuple[str, ...], replan=None
) -> str:
    return sha256_digest(
        canonical_json_bytes(
            {
                **({"replan": replan.model_dump(mode="json")} if replan else {}),
                "outputs": {
                    name: payload.model_dump(mode="json") for name, payload in outputs.items()
                },
                "summary": summary,
                "evidence_refs": list(evidence_refs),
            }
        )
    )


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
