"""Internal submit_node_result mechanism tool for structured Workflow outputs.

Never granted by definitions, prompts, Skills or Artifacts. Ordinary Direct
never receives it. Schema violations are in-loop tool errors the model can
correct; a conflicting second submission is refused without overwrite.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from morrow.core.capabilities import OperationIntent, OperationKind, ToolCallContext
from morrow.core.domain import canonical_json_bytes, sha256_digest
from morrow.core.execution import tool_declaration
from morrow.core.models import ToolEffect
from morrow.core.workflows.contracts import (
    SUBMIT_NODE_RESULT_NAME,
    EvidenceBundle,
    ReviewReport,
)
from morrow.runtime.policy import ToolApproval, ToolExecutionPolicy
from morrow.runtime.tools import (
    RegisteredTool,
    ToolErrorCode,
    ToolExecutionError,
    make_tool,
)

_PAYLOADS = {"EvidenceBundle": EvidenceBundle, "ReviewReport": ReviewReport}


class SubmitNodeResultArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal[1] = 1
    outputs: dict[str, dict[str, Any]] = Field(min_length=1, max_length=64)
    summary: str = Field(default="", max_length=1024)
    evidence_refs: tuple[str, ...] = Field(default=(), max_length=32)


def make_submit_node_result_tool(hooks) -> RegisteredTool:
    async def handler(arguments: SubmitNodeResultArguments, context) -> dict[str, object]:
        del context
        return hooks.submit_node_result(arguments)

    def resolve(
        _arguments: SubmitNodeResultArguments, _context: ToolCallContext
    ) -> OperationIntent:
        return OperationIntent(kind=OperationKind.INTERNAL_READ, effect=ToolEffect.NONE)

    return make_tool(
        name=SUBMIT_NODE_RESULT_NAME,
        description=(
            "Submit this node's declared structured outputs. Call once with every required "
            "structured slot. The final message is transcript only and is never parsed."
        ),
        arguments_model=SubmitNodeResultArguments,
        handler=handler,
        context_handler=handler,
        intent_resolver=resolve,
        execution_policy=ToolExecutionPolicy(approval=ToolApproval.NEVER),
        recovery_declaration=tool_declaration(SUBMIT_NODE_RESULT_NAME),
    )


def submission_digest(
    outputs: dict[str, object], summary: str, evidence_refs: tuple[str, ...]
) -> str:
    return sha256_digest(
        canonical_json_bytes(
            {
                "outputs": {
                    name: payload.model_dump(mode="json") for name, payload in outputs.items()
                },
                "summary": summary,
                "evidence_refs": list(evidence_refs),
            }
        )
    )


def parse_submitted_payload(kind: str, raw: dict[str, Any]):
    model = _PAYLOADS.get(kind)
    if model is None:
        raise ToolExecutionError(
            ToolErrorCode.INVALID_ARGUMENTS,
            f"contract kind {kind} is not submitted through submit_node_result",
        )
    try:
        return model.model_validate(raw)
    except ValidationError as exc:
        raise ToolExecutionError(
            ToolErrorCode.INVALID_ARGUMENTS,
            f"schema violation: {exc.error_count()} field error(s)",
        ) from None
