"""Registry/executor contracts and the two demo in-memory tools."""

from __future__ import annotations

import asyncio
import json

import pytest
from pydantic import BaseModel, ConfigDict

from morrow.core.models import FunctionToolCall
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


@pytest.mark.asyncio
async def test_lookup_record_returns_injected_value_in_compact_envelope():
    outcome = await _demo_executor().execute(
        _call("lookup_record", '{"dataset": "plans", "key": "starter"}')
    )
    assert outcome.ok is True
    assert outcome.error_code is None
    assert json.loads(outcome.envelope) == {"ok": True, "result": {"monthly_price": 29.0}}


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
