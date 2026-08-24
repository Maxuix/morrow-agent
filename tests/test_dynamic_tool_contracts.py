"""Stage 6 Subplan 71: dynamic argument and recovery contracts."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from morrow.application.prepared import prepare_cycle_executions
from morrow.application.recovery import declaration_for_execution
from morrow.core.capabilities import ToolRunContext
from morrow.core.domain import sha256_digest
from morrow.core.execution import (
    EffectClass,
    MissingCompletionPolicy,
    ToolRecoveryDeclaration,
)
from morrow.core.models import AssistantMessage, FunctionToolCall, ToolApprovalDecision, ToolEffect
from morrow.runtime.policy import ToolApproval, ToolExecutionPolicy
from morrow.runtime.session import Session
from morrow.runtime.tool_arguments import (
    MAX_SCHEMA_DEPTH,
    SCHEMA_DIALECT,
    JsonSchemaArgumentsValidator,
    PydanticArgumentsValidator,
    ToolArgumentsValidationError,
)
from morrow.runtime.tools import ToolErrorCode, ToolExecutor, ToolRegistry, make_tool
from morrow.testing import FixedIdSource, make_run_policy


class _Approval:
    def __init__(self) -> None:
        self.requests = []

    async def request(self, request) -> ToolApprovalDecision:
        self.requests.append(request)
        return ToolApprovalDecision(approved=True)


def _dynamic_declaration() -> ToolRecoveryDeclaration:
    return ToolRecoveryDeclaration(
        tool_name="runtime_unknown",
        effect_class=EffectClass.RECONCILEABLE_STRUCTURED_STATE_WRITE,
        missing_handler_completed=MissingCompletionPolicy.REQUIRES_RECONCILIATION,
        reconciliation_strategy_id="runtime-v1",
    )


def test_json_schema_validator_is_explicit_bounded_and_local_only():
    validator = JsonSchemaArgumentsValidator(
        {
            "type": "object",
            "properties": {
                "name": {"type": "string", "minLength": 1, "maxLength": 20},
                "count": {"type": "integer", "minimum": 1, "maximum": 5},
            },
            "required": ["name", "count"],
            "additionalProperties": False,
        }
    )

    assert validator.schema["$schema"] == SCHEMA_DIALECT
    assert validator.validate('{"name":"Ada","count":2}') == {"name": "Ada", "count": 2}
    with pytest.raises(ToolArgumentsValidationError) as invalid:
        validator.validate('{"name":"Ada","count":9}')
    assert invalid.value.code == "validation_failed"

    with pytest.raises(ToolArgumentsValidationError, match="方言"):
        JsonSchemaArgumentsValidator({"$schema": "http://json-schema.org/draft-07/schema#"})
    with pytest.raises(ToolArgumentsValidationError, match="引用"):
        JsonSchemaArgumentsValidator({"$ref": "https://example.test/schema.json"})
    with pytest.raises(ToolArgumentsValidationError, match="引用"):
        JsonSchemaArgumentsValidator({"$ref": "#/$defs/missing"})
    with pytest.raises(ToolArgumentsValidationError, match="节点无效"):
        JsonSchemaArgumentsValidator({"additionalProperties": "yes"})
    deep_schema: dict[str, object] = {"type": "object"}
    for _ in range(MAX_SCHEMA_DEPTH + 1):
        deep_schema = {"allOf": [deep_schema]}
    with pytest.raises(ToolArgumentsValidationError, match="嵌套过深"):
        JsonSchemaArgumentsValidator(deep_schema)

    with pytest.raises(ToolArgumentsValidationError, match="multipleOf"):
        JsonSchemaArgumentsValidator({"multipleOf": 0})


def test_argument_json_is_budgeted_and_never_leaks_raw_values():
    validator = JsonSchemaArgumentsValidator({"type": "object"})
    secret = "do-not-echo-this"
    with pytest.raises(ToolArgumentsValidationError) as malformed:
        validator.validate('{"value": "' + secret)
    assert malformed.value.code == "invalid_json"
    assert secret not in str(malformed.value)

    oversized = json.dumps({str(index): True for index in range(257)})
    with pytest.raises(ToolArgumentsValidationError) as over_budget:
        validator.validate(oversized)
    assert over_budget.value.code == "budget"

    nested: object = True
    for _ in range(33):
        nested = [nested]
    with pytest.raises(ToolArgumentsValidationError) as too_deep:
        validator.validate(json.dumps(nested))
    assert too_deep.value.code == "budget"


def test_pydantic_validator_keeps_local_json_semantics():
    from pydantic import BaseModel, ConfigDict

    class Arguments(BaseModel):
        model_config = ConfigDict(extra="forbid", strict=True)

        count: float

    validator = PydanticArgumentsValidator(Arguments)
    assert validator.validate('{"count":2}').count == 2.0
    with pytest.raises(ToolArgumentsValidationError) as invalid:
        validator.validate('{"count":"secret"}')
    assert invalid.value.code == "validation_failed"
    assert "secret" not in str(invalid.value)


@pytest.mark.asyncio
async def test_dynamic_tool_validates_approves_executes_and_freezes_recovery():
    validator = JsonSchemaArgumentsValidator(
        {
            "$schema": SCHEMA_DIALECT,
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
            "additionalProperties": False,
        }
    )
    declaration = _dynamic_declaration()

    async def handler(arguments: dict[str, str]) -> object:
        return {"echo": arguments["value"]}

    registry = ToolRegistry()
    registry.register(
        make_tool(
            name="runtime_unknown",
            description="A tool discovered after startup",
            arguments_validator=validator,
            handler=handler,
            execution_policy=ToolExecutionPolicy(
                effect=ToolEffect.SESSION_WRITE,
                approval=ToolApproval.REQUIRED,
            ),
            recovery_declaration=declaration,
        )
    )
    approval = _Approval()
    executor = ToolExecutor(registry.snapshot(), make_run_policy(), approval_port=approval)

    success = await executor.execute(
        FunctionToolCall(id="call_dynamic", name="runtime_unknown", arguments='{"value":"ok"}')
    )
    assert success.ok is True
    assert json.loads(success.envelope)["result"] == {"echo": "ok"}
    assert len(approval.requests) == 1

    invalid = await executor.execute(
        FunctionToolCall(id="call_invalid", name="runtime_unknown", arguments='{"value":4}')
    )
    assert invalid.error_code is ToolErrorCode.INVALID_ARGUMENTS
    assert len(approval.requests) == 1

    prepared = prepare_cycle_executions(
        AssistantMessage(
            tool_calls=(
                FunctionToolCall(
                    id="call_prepare",
                    name="runtime_unknown",
                    arguments='{"value":"prepared"}',
                ),
            )
        ),
        session=Session(session_id="ses_dynamic"),
        tool_executor=executor,
        run_context=ToolRunContext(run_id="run_dynamic", session_id="ses_dynamic"),
        id_source=FixedIdSource(),
        workspace_id="ws_dynamic",
        task_run_id="task_dynamic",
        turn_id="turn_dynamic",
        agent_run_id="arun_dynamic",
    )
    assert prepared[0].intent.recovery_declaration == declaration
    assert prepared[0].intent.effect_class is declaration.effect_class

    execution = SimpleNamespace(tool_name="runtime_unknown", intent=prepared[0].intent)
    resolved = declaration_for_execution(execution)
    assert resolved == declaration
    assert resolved.reconciliation_strategy_id == "runtime-v1"


def test_dynamic_recovery_does_not_consult_current_static_registry(monkeypatch):
    declaration = _dynamic_declaration()
    from morrow.core.execution import PreparedIntent

    intent = PreparedIntent(
        tool_name="runtime_unknown",
        call_id="call_frozen",
        ordinal=1,
        arguments_digest=sha256_digest("arguments"),
        schema_digest=sha256_digest("schema"),
        permission_context_digest=sha256_digest("permissions"),
        effect_class=declaration.effect_class,
        recovery_declaration=declaration,
    )

    def should_not_be_called(*_args, **_kwargs):
        raise AssertionError("recovery must use the frozen declaration")

    monkeypatch.setattr("morrow.application.recovery.tool_declaration", should_not_be_called)
    assert declaration_for_execution(
        SimpleNamespace(tool_name="runtime_unknown", intent=intent)
    ) == (declaration)


def test_schema_depth_constant_is_bounded():
    assert MAX_SCHEMA_DEPTH <= 16
