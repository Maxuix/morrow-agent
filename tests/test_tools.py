"""Registry/executor contracts and the two demo in-memory tools."""

from __future__ import annotations

import asyncio
import json
from typing import Literal

import pytest
from pydantic import BaseModel, ConfigDict

from morrow.core.models import (
    FunctionToolCall,
    ToolApprovalDecision,
    ToolApprovalRequest,
    ToolEffect,
)
from morrow.runtime.policy import ToolApproval, ToolExecutionPolicy
from morrow.runtime.tools import (
    ToolErrorCode,
    ToolExecutionError,
    ToolExecutor,
    ToolRegistry,
    make_calculate_tool,
    make_lookup_record_tool,
    make_tool,
    tool_error_envelope,
)
from morrow.testing import make_run_policy

DEMO_RECORDS = {
    ("plans", "starter"): {"monthly_price": 29.0},
    ("regions", "de"): {"tax_rate": 0.19},
}


class _ScriptedApproval:
    def __init__(self, approved: bool) -> None:
        self.approved = approved
        self.requests: list[ToolApprovalRequest] = []

    async def request(self, request: ToolApprovalRequest) -> ToolApprovalDecision:
        self.requests.append(request)
        return ToolApprovalDecision(approved=self.approved)


class _BlockingApproval:
    def __init__(self) -> None:
        self.started = asyncio.Event()

    async def request(self, request: ToolApprovalRequest) -> ToolApprovalDecision:
        del request
        self.started.set()
        await asyncio.Event().wait()
        return ToolApprovalDecision(approved=True)


def _demo_executor() -> ToolExecutor:
    registry = ToolRegistry()
    registry.register(make_lookup_record_tool(DEMO_RECORDS))
    registry.register(make_calculate_tool())
    return ToolExecutor(registry.snapshot(), make_run_policy())


def _call(name: str, arguments: str, call_id: str = "call_1") -> FunctionToolCall:
    return FunctionToolCall(id=call_id, name=name, arguments=arguments)


def test_registry_rejects_duplicates_and_sorts_definitions():
    registry = ToolRegistry()
    registry.register(make_calculate_tool())
    registry.register(make_lookup_record_tool(DEMO_RECORDS))
    names = [definition.function.name for definition in registry.definitions()]
    assert names == ["calculate", "lookup_record"]
    with pytest.raises(ValueError, match="duplicate"):
        registry.register(make_calculate_tool())
    assert registry.get("calculate") is not None
    assert registry.get("missing") is None


def test_registry_snapshot_is_isolated_from_later_registration():
    registry = ToolRegistry()
    registry.register(make_calculate_tool())
    frozen = registry.snapshot()
    registry.register(make_lookup_record_tool(DEMO_RECORDS))
    assert [tool.function.name for tool in frozen.definitions] == ["calculate"]
    assert frozen.tools["calculate"] is registry.get("calculate")


def test_local_policy_metadata_is_immutable_and_provider_invisible():
    policy = ToolExecutionPolicy(
        effect=ToolEffect.PERSISTENT_WRITE,
        approval=ToolApproval.REQUIRED,
    )

    class Arguments(BaseModel):
        model_config = ConfigDict(extra="forbid")

        item: Literal["alpha", "beta"]

    async def handler(_: Arguments) -> object:
        return None

    tool = make_tool(
        name="policy_probe",
        description="policy probe",
        arguments_model=Arguments,
        handler=handler,
        execution_policy=policy,
        approval_preview=lambda _: ("写入 workspace\n仅显示安全摘要",),
    )

    assert tool.execution_policy == policy
    assert tool.definition.model_dump() == {
        "type": "function",
        "function": {
            "name": "policy_probe",
            "description": "policy probe",
            "parameters": tool.definition.function.parameters,
        },
    }
    assert "execution_policy" not in tool.definition.model_dump()
    assert "approval_preview" not in tool.definition.model_dump()
    assert tool.definition.function.parameters["additionalProperties"] is False
    assert tool.definition.function.parameters["properties"]["item"]["enum"] == [
        "alpha",
        "beta",
    ]

    with pytest.raises((TypeError, ValueError)):
        policy.approval = ToolApproval.NEVER


def test_tool_schema_preserves_nested_pydantic_constraints():
    class Nested(BaseModel):
        model_config = ConfigDict(extra="forbid")

        value: Literal["one", "two"]

    class Arguments(BaseModel):
        model_config = ConfigDict(extra="forbid")

        nested: Nested

    async def handler(_: Arguments) -> object:
        return None

    tool = make_tool(
        name="schema_probe",
        description="schema probe",
        arguments_model=Arguments,
        handler=handler,
    )
    schema = tool.definition.function.parameters

    assert schema["additionalProperties"] is False
    assert "$defs" in schema
    assert schema["$defs"]["Nested"]["properties"]["value"]["enum"] == ["one", "two"]
    assert schema["required"] == ["nested"]


@pytest.mark.asyncio
async def test_lookup_record_returns_injected_value_in_compact_envelope():
    outcome = await _demo_executor().execute(
        _call("lookup_record", '{"dataset": "plans", "key": "starter"}')
    )
    assert outcome.ok is True
    assert outcome.error_code is None
    assert json.loads(outcome.envelope) == {"ok": True, "result": {"monthly_price": 29.0}}


@pytest.mark.asyncio
async def test_required_approval_receives_only_sanitized_local_preview():
    calls = []

    class Arguments(BaseModel):
        model_config = ConfigDict(extra="forbid")

        value: str

    async def handler(arguments: Arguments) -> object:
        calls.append(arguments.value)
        return {"saved": True}

    approval = _ScriptedApproval(approved=True)
    registry = ToolRegistry()
    registry.register(
        make_tool(
            name="stateful_probe",
            description="stateful probe",
            arguments_model=Arguments,
            handler=handler,
            execution_policy=ToolExecutionPolicy(
                effect=ToolEffect.SESSION_WRITE,
                approval=ToolApproval.REQUIRED,
            ),
            approval_preview=lambda _: (
                "  scope: session\noperation: set  ",
                "不要显示模型参数",
            ),
        )
    )

    outcome = await ToolExecutor(
        registry.snapshot(), make_run_policy(), approval_port=approval
    ).execute(_call("stateful_probe", '{"value": "secret"}'))

    assert outcome.ok is True
    assert calls == ["secret"]
    assert len(approval.requests) == 1
    request = approval.requests[0]
    assert set(ToolApprovalRequest.model_fields) == {"call_id", "effect", "preview"}
    assert request.call_id == "call_1"
    assert request.effect == ToolEffect.SESSION_WRITE
    assert request.preview == ("scope: session operation: set", "不要显示模型参数")
    assert "secret" not in str(request.model_dump())


@pytest.mark.asyncio
async def test_rejected_approval_is_bounded_and_never_runs_handler():
    calls = 0

    class Arguments(BaseModel):
        model_config = ConfigDict(extra="forbid")

    async def handler(_: Arguments) -> object:
        nonlocal calls
        calls += 1
        return "must not run"

    approval = _ScriptedApproval(approved=False)
    registry = ToolRegistry()
    registry.register(
        make_tool(
            name="rejected_probe",
            description="rejected probe",
            arguments_model=Arguments,
            handler=handler,
            execution_policy=ToolExecutionPolicy(approval=ToolApproval.REQUIRED),
        )
    )

    outcome = await ToolExecutor(
        registry.snapshot(), make_run_policy(), approval_port=approval
    ).execute(_call("rejected_probe", "{}"))

    assert outcome.ok is False
    assert outcome.error_code == ToolErrorCode.APPROVAL_REJECTED
    assert len(outcome.envelope) <= make_run_policy().effective_result_limit
    assert calls == 0


@pytest.mark.asyncio
async def test_required_approval_fails_closed_without_a_port():
    calls = 0

    class Arguments(BaseModel):
        model_config = ConfigDict(extra="forbid")

    async def handler(_: Arguments) -> object:
        nonlocal calls
        calls += 1
        return "must not run"

    registry = ToolRegistry()
    registry.register(
        make_tool(
            name="unavailable_probe",
            description="unavailable probe",
            arguments_model=Arguments,
            handler=handler,
            execution_policy=ToolExecutionPolicy(approval=ToolApproval.REQUIRED),
        )
    )

    outcome = await ToolExecutor(registry.snapshot(), make_run_policy()).execute(
        _call("unavailable_probe", "{}")
    )

    assert outcome.ok is False
    assert outcome.error_code == ToolErrorCode.APPROVAL_UNAVAILABLE
    assert calls == 0


@pytest.mark.asyncio
async def test_approval_preview_failure_is_bounded_and_never_runs_handler():
    calls = 0

    class Arguments(BaseModel):
        model_config = ConfigDict(extra="forbid")

    async def handler(_: Arguments) -> object:
        nonlocal calls
        calls += 1
        return "must not run"

    def broken_preview(_: BaseModel) -> tuple[str, ...]:
        raise RuntimeError("preview secret")

    approval = _ScriptedApproval(approved=True)
    registry = ToolRegistry()
    registry.register(
        make_tool(
            name="preview_failure_probe",
            description="preview failure probe",
            arguments_model=Arguments,
            handler=handler,
            execution_policy=ToolExecutionPolicy(approval=ToolApproval.REQUIRED),
            approval_preview=broken_preview,
        )
    )

    outcome = await ToolExecutor(
        registry.snapshot(), make_run_policy(), approval_port=approval
    ).execute(_call("preview_failure_probe", "{}"))

    assert outcome.ok is False
    assert outcome.error_code == ToolErrorCode.APPROVAL_PREVIEW_FAILED
    assert calls == 0
    assert approval.requests == []


@pytest.mark.asyncio
async def test_approval_wait_propagates_cancellation_without_running_handler():
    calls = 0

    class Arguments(BaseModel):
        model_config = ConfigDict(extra="forbid")

    async def handler(_: Arguments) -> object:
        nonlocal calls
        calls += 1
        return "must not run"

    approval = _BlockingApproval()
    registry = ToolRegistry()
    registry.register(
        make_tool(
            name="cancelled_approval_probe",
            description="cancelled approval probe",
            arguments_model=Arguments,
            handler=handler,
            execution_policy=ToolExecutionPolicy(approval=ToolApproval.REQUIRED),
        )
    )
    executor = ToolExecutor(registry.snapshot(), make_run_policy(), approval_port=approval)
    task = asyncio.create_task(executor.execute(_call("cancelled_approval_probe", "{}")))
    await approval.started.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert calls == 0


@pytest.mark.asyncio
async def test_lookup_record_not_found_is_deterministic():
    outcome = await _demo_executor().execute(
        _call("lookup_record", '{"dataset": "plans", "key": "enterprise"}')
    )
    assert outcome.ok is False
    assert outcome.error_code == ToolErrorCode.NOT_FOUND
    assert json.loads(outcome.envelope)["error"]["code"] == "not_found"


@pytest.mark.parametrize(
    "arguments",
    [
        "not json",
        '{"dataset": "unknown", "key": "starter"}',
        '{"dataset": "plans"}',
        '{"dataset": "plans", "key": "  "}',
        '{"dataset": "plans", "key": "starter", "extra": 1}',
        '{"dataset": "plans", "key": ["starter"]}',
    ],
)
@pytest.mark.asyncio
async def test_invalid_arguments_are_rejected_before_handler(arguments):
    outcome = await _demo_executor().execute(_call("lookup_record", arguments))
    assert outcome.ok is False
    assert outcome.error_code == ToolErrorCode.INVALID_ARGUMENTS


@pytest.mark.asyncio
async def test_calculate_is_ordered_left_to_right():
    executor = _demo_executor()
    subtract = await executor.execute(
        _call("calculate", '{"operation": "subtract", "values": [10, 3, 2]}')
    )
    divide = await executor.execute(
        _call("calculate", '{"operation": "divide", "values": [100, 5, 2]}')
    )
    assert json.loads(subtract.envelope)["result"] == {"operation": "subtract", "value": 5.0}
    assert json.loads(divide.envelope)["result"] == {"operation": "divide", "value": 10.0}


@pytest.mark.asyncio
async def test_calculate_rejects_bounds_non_finite_and_strict_type_errors():
    executor = _demo_executor()
    for arguments in (
        '{"operation": "add", "values": [1]}',
        '{"operation": "add", "values": [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33]}',
        '{"operation": "add", "values": [1, Infinity]}',
        '{"operation": "add", "values": [1, NaN]}',
        '{"operation": "add", "values": [1, "2"]}',
        '{"operation": "modulo", "values": [1, 2]}',
    ):
        outcome = await executor.execute(_call("calculate", arguments))
        assert outcome.ok is False, arguments
        assert outcome.error_code == ToolErrorCode.INVALID_ARGUMENTS, arguments


@pytest.mark.asyncio
async def test_calculate_division_by_zero_is_deterministic():
    outcome = await _demo_executor().execute(
        _call("calculate", '{"operation": "divide", "values": [10, 0]}')
    )
    assert outcome.error_code == ToolErrorCode.DIVISION_BY_ZERO
    assert json.loads(outcome.envelope)["error"]["code"] == "division_by_zero"


@pytest.mark.asyncio
async def test_calculate_rejects_non_finite_results_from_finite_inputs():
    outcome = await _demo_executor().execute(
        _call("calculate", '{"operation": "multiply", "values": [1e308, 1e308]}')
    )
    assert outcome.ok is False
    assert outcome.error_code == ToolErrorCode.EXECUTION_FAILED
    assert "Infinity" not in outcome.envelope


@pytest.mark.asyncio
async def test_unknown_tool_is_bounded_without_leaking_call_details():
    outcome = await _demo_executor().execute(_call("shell", '{"cmd": "ls"}'))
    assert outcome.error_code == ToolErrorCode.UNKNOWN_TOOL
    assert "traceback" not in outcome.envelope.casefold()


@pytest.mark.asyncio
async def test_handler_failure_is_bounded_and_never_auto_retried():
    calls = {"count": 0}

    class _Args(BaseModel):
        model_config = ConfigDict(extra="forbid")

    async def failing(_: _Args) -> object:
        calls["count"] += 1
        raise RuntimeError("secret internal detail")

    registry = ToolRegistry()
    registry.register(
        make_tool(name="failing", description="boom", arguments_model=_Args, handler=failing)
    )
    outcome = await ToolExecutor(registry.snapshot(), make_run_policy()).execute(
        _call("failing", "{}")
    )
    assert outcome.ok is False
    assert outcome.error_code == ToolErrorCode.EXECUTION_FAILED
    assert "secret internal detail" not in outcome.envelope
    assert calls["count"] == 1


@pytest.mark.asyncio
async def test_cancelled_error_is_reraised_to_the_loop():
    class _Args(BaseModel):
        model_config = ConfigDict(extra="forbid")

    async def cancelled(_: _Args) -> object:
        raise asyncio.CancelledError

    registry = ToolRegistry()
    registry.register(
        make_tool(name="stuck", description="stuck", arguments_model=_Args, handler=cancelled)
    )
    with pytest.raises(asyncio.CancelledError):
        await ToolExecutor(registry.snapshot(), make_run_policy()).execute(_call("stuck", "{}"))


@pytest.mark.asyncio
async def test_typed_tool_execution_error_maps_to_its_code():
    class _Args(BaseModel):
        model_config = ConfigDict(extra="forbid")

    async def missing(_: _Args) -> object:
        raise ToolExecutionError(ToolErrorCode.NOT_FOUND, "没有找到")

    registry = ToolRegistry()
    registry.register(
        make_tool(name="missing", description="missing", arguments_model=_Args, handler=missing)
    )
    outcome = await ToolExecutor(registry.snapshot(), make_run_policy()).execute(
        _call("missing", "{}")
    )
    assert outcome.error_code == ToolErrorCode.NOT_FOUND
    assert json.loads(outcome.envelope)["error"]["message"] == "没有找到"


def test_error_envelope_is_compact_sorted_and_bounded():
    envelope = tool_error_envelope(ToolErrorCode.CANCELLED, "x" * 500)
    assert envelope == json.dumps(
        {"ok": False, "error": {"code": "cancelled", "message": "x" * 200}},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def test_tool_definitions_are_generated_from_argument_models():
    executor = _demo_executor()
    parameters = {tool.function.name: tool.function.parameters for tool in executor.definitions}
    assert set(parameters) == {"calculate", "lookup_record"}
    assert parameters["lookup_record"]["properties"]["dataset"]["enum"] == ["plans", "regions"]
    assert parameters["calculate"]["required"] == ["operation", "values"]


@pytest.mark.asyncio
async def test_large_success_is_truncated_to_valid_json_with_original_size():
    class _Args(BaseModel):
        model_config = ConfigDict(extra="forbid")

    async def large(_: _Args) -> object:
        return {"payload": "界" * 1000}

    registry = ToolRegistry()
    registry.register(
        make_tool(name="large", description="large", arguments_model=_Args, handler=large)
    )
    executor = ToolExecutor(registry.snapshot(), make_run_policy())
    outcome = await executor.execute(_call("large", "{}"), result_limit=180)

    parsed = json.loads(outcome.envelope)
    assert outcome.ok is True
    assert outcome.truncated is True
    assert outcome.original_chars > 180
    assert len(outcome.envelope) <= 180
    assert parsed["result"]["truncated"] is True
    assert parsed["result"]["original_chars"] == outcome.original_chars


@pytest.mark.asyncio
async def test_success_that_cannot_fit_truncation_envelope_returns_output_failure():
    class _Args(BaseModel):
        model_config = ConfigDict(extra="forbid")

    async def large(_: _Args) -> object:
        return {"payload": "x" * 1000}

    registry = ToolRegistry()
    registry.register(
        make_tool(name="large", description="large", arguments_model=_Args, handler=large)
    )
    outcome = await ToolExecutor(registry.snapshot(), make_run_policy()).execute(
        _call("large", "{}"), result_limit=60
    )

    assert outcome.ok is False
    assert outcome.error_code == ToolErrorCode.OUTPUT_FAILED
    assert len(outcome.envelope) <= 60
    assert json.loads(outcome.envelope)["error"]["code"] == "output_failed"


@pytest.mark.asyncio
async def test_validation_details_are_stable_and_capped_by_policy():
    class _Args(BaseModel):
        model_config = ConfigDict(extra="forbid")

        first: int
        second: int
        third: int

    async def unused(_: _Args) -> object:
        raise AssertionError

    registry = ToolRegistry()
    registry.register(
        make_tool(name="validate", description="validate", arguments_model=_Args, handler=unused)
    )
    policy = make_run_policy(max_validation_errors=2)
    outcome = await ToolExecutor(registry.snapshot(), policy).execute(_call("validate", "{}"))

    details = json.loads(outcome.envelope)["error"]["details"]
    assert details == [
        {"path": "first", "type": "missing"},
        {"path": "second", "type": "missing"},
    ]
