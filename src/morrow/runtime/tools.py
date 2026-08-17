"""Task-frozen tool registry, bounded executor, and the demo in-memory tools."""

from __future__ import annotations

import asyncio
import json
import math
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from morrow.core.models import FunctionToolCall, ToolDefinition, ToolFunction
from morrow.runtime.policy import RunPolicy

ENVELOPE_MESSAGE_LIMIT = 200


class ToolErrorCode(StrEnum):
    """Deterministic tool outcome codes carried inside the envelope."""

    INVALID_ARGUMENTS = "invalid_arguments"
    UNKNOWN_TOOL = "unknown_tool"
    NOT_FOUND = "not_found"
    DIVISION_BY_ZERO = "division_by_zero"
    EXECUTION_FAILED = "execution_failed"
    TIMEOUT = "timeout"
    OUTPUT_FAILED = "output_failed"
    CANCELLED = "cancelled"
    BUDGET_EXHAUSTED = "budget_exhausted"
    INTERNAL = "internal"


class ToolExecutionError(Exception):
    """Typed handler failure mapped to one deterministic code."""

    def __init__(self, code: ToolErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code


def _dump(payload: dict) -> str:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def tool_error_envelope(
    code: ToolErrorCode, message: str, *, details: list[dict[str, str]] | None = None
) -> str:
    bounded = " ".join(str(message).split())[:ENVELOPE_MESSAGE_LIMIT]
    error: dict = {"code": code.value, "message": bounded}
    if details:
        error["details"] = details
    return _dump({"ok": False, "error": error})


MIN_ERROR_ENVELOPE_CHARS = max(len(tool_error_envelope(code, "")) for code in ToolErrorCode)


def tool_parameters_from_model(model: type[BaseModel]) -> dict:
    schema = model.model_json_schema()
    return {
        "type": "object",
        "properties": schema.get("properties", {}),
        "required": schema.get("required", []),
    }


@dataclass(frozen=True)
class RegisteredTool:
    definition: ToolDefinition
    arguments_model: type[BaseModel]
    handler: Callable[[BaseModel], Awaitable[object]]


@dataclass(frozen=True)
class ToolSet:
    """Immutable per-task view; registration mutations never reach it."""

    tools: Mapping[str, RegisteredTool]
    definitions: tuple[ToolDefinition, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.tools, MappingProxyType):
            object.__setattr__(self, "tools", MappingProxyType(dict(self.tools)))


class ToolRegistry:
    """Mutable only during setup; `snapshot()` freezes it for one task."""

    def __init__(self) -> None:
        self._tools: dict[str, RegisteredTool] = {}

    def register(self, tool: RegisteredTool) -> None:
        name = tool.definition.function.name
        if name in self._tools:
            raise ValueError(f"duplicate tool registration: {name}")
        self._tools[name] = tool

    def get(self, name: str) -> RegisteredTool | None:
        return self._tools.get(name)

    def definitions(self) -> tuple[ToolDefinition, ...]:
        return tuple(self._tools[name].definition for name in sorted(self._tools))

    def snapshot(self) -> ToolSet:
        return ToolSet(
            tools=MappingProxyType(dict(self._tools)),
            definitions=self.definitions(),
        )


@dataclass(frozen=True)
class ToolExecutionOutcome:
    call_id: str
    name: str
    ok: bool
    envelope: str
    error_code: ToolErrorCode | None = None
    truncated: bool = False
    original_chars: int | None = None


class ToolExecutor:
    """One bounded outcome per call; never retries; no raw exception leaks."""

    def __init__(self, tool_set: ToolSet, run_policy: RunPolicy) -> None:
        self.tool_set = tool_set
        self.run_policy = run_policy

    @property
    def definitions(self) -> tuple[ToolDefinition, ...]:
        return self.tool_set.definitions

    def error_outcome(
        self,
        call: FunctionToolCall,
        code: ToolErrorCode,
        message: str,
        *,
        result_limit: int | None = None,
    ) -> ToolExecutionOutcome:
        return self._error(
            call,
            code,
            message,
            limit=result_limit or self.run_policy.effective_result_limit,
        )

    async def execute(
        self, call: FunctionToolCall, *, result_limit: int | None = None
    ) -> ToolExecutionOutcome:
        limit = result_limit or self.run_policy.effective_result_limit
        registered = self.tool_set.tools.get(call.name)
        if registered is None:
            return self._error(
                call, ToolErrorCode.UNKNOWN_TOOL, f"未注册的工具: {call.name}", limit=limit
            )
        try:
            arguments = registered.arguments_model.model_validate_json(call.arguments, strict=True)
        except ValidationError as exc:
            details = [
                {
                    "path": ".".join(str(item) for item in error["loc"]),
                    "type": str(error["type"]),
                }
                for error in exc.errors(include_url=False)[: self.run_policy.max_validation_errors]
            ]
            return self._error(
                call,
                ToolErrorCode.INVALID_ARGUMENTS,
                "工具参数校验失败",
                limit=limit,
                details=details,
            )
        try:
            result = await registered.handler(arguments)
            envelope = _dump({"ok": True, "result": result})
        except asyncio.CancelledError:
            raise
        except ToolExecutionError as exc:
            return self._error(call, exc.code, str(exc), limit=limit)
        except Exception:
            return self._error(call, ToolErrorCode.EXECUTION_FAILED, "工具执行失败", limit=limit)
        original_chars = len(envelope)
        if original_chars <= limit:
            return ToolExecutionOutcome(call_id=call.id, name=call.name, ok=True, envelope=envelope)
        base_result = {"truncated": True, "original_chars": original_chars, "content": ""}
        base = _dump({"ok": True, "result": base_result})
        available = max(0, limit - len(base))
        serialized_result = _dump({"value": result})
        low, high = 0, min(available, len(serialized_result))
        bounded = base
        while low <= high:
            middle = (low + high) // 2
            base_result["content"] = serialized_result[:middle]
            candidate = _dump({"ok": True, "result": base_result})
            if len(candidate) <= limit:
                bounded = candidate
                low = middle + 1
            else:
                high = middle - 1
        if len(bounded) > limit:
            return self._error(
                call,
                ToolErrorCode.OUTPUT_FAILED,
                "工具结果预算不足",
                limit=limit,
            )
        return ToolExecutionOutcome(
            call_id=call.id,
            name=call.name,
            ok=True,
            envelope=bounded,
            truncated=True,
            original_chars=original_chars,
        )

    @staticmethod
    def _error(
        call: FunctionToolCall,
        code: ToolErrorCode,
        message: str,
        *,
        limit: int,
        details: list[dict[str, str]] | None = None,
    ) -> ToolExecutionOutcome:
        envelope = tool_error_envelope(code, message, details=details)
        if len(envelope) > limit and details:
            envelope = tool_error_envelope(code, message)
        if len(envelope) > limit:
            envelope = tool_error_envelope(code, "")
        return ToolExecutionOutcome(
            call_id=call.id,
            name=call.name,
            ok=False,
            envelope=envelope,
            error_code=code,
        )


def make_tool(
    *,
    name: str,
    description: str,
    arguments_model: type[BaseModel],
    handler: Callable[[BaseModel], Awaitable[object]],
) -> RegisteredTool:
    return RegisteredTool(
        definition=ToolDefinition(
            function=ToolFunction(
                name=name,
                description=description,
                parameters=tool_parameters_from_model(arguments_model),
            )
        ),
        arguments_model=arguments_model,
        handler=handler,
    )


class LookupRecordArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dataset: Literal["plans", "regions"]
    key: str

    @field_validator("key")
    @classmethod
    def non_empty_key(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("key must not be empty")
        return value


def make_lookup_record_tool(records: Mapping[tuple[str, str], object]) -> RegisteredTool:
    """Read-only lookup over injected in-memory data; no filesystem access."""
    data = MappingProxyType(dict(records))

    async def handler(arguments: LookupRecordArguments) -> object:
        value = data.get((arguments.dataset, arguments.key))
        if value is None:
            raise ToolExecutionError(
                ToolErrorCode.NOT_FOUND,
                f"记录不存在: {arguments.dataset}/{arguments.key}",
            )
        return value

    return make_tool(
        name="lookup_record",
        description="查询注入的内存数据集（plans 或 regions）中的一条记录。",
        arguments_model=LookupRecordArguments,
        handler=handler,
    )


class CalculateArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation: Literal["add", "subtract", "multiply", "divide"]
    values: tuple[float, ...]

    @field_validator("values")
    @classmethod
    def bounded_finite_values(cls, values: tuple[float, ...]) -> tuple[float, ...]:
        if not 2 <= len(values) <= 32:
            raise ValueError("values must contain between 2 and 32 numbers")
        if not all(math.isfinite(value) for value in values):
            raise ValueError("values must be finite numbers")
        return values


def make_calculate_tool() -> RegisteredTool:
    """Deterministic left-to-right arithmetic over validated finite numbers."""

    async def handler(arguments: CalculateArguments) -> object:
        result = arguments.values[0]
        for value in arguments.values[1:]:
            if arguments.operation == "add":
                result += value
            elif arguments.operation == "subtract":
                result -= value
            elif arguments.operation == "multiply":
                result *= value
            elif value == 0:
                raise ToolExecutionError(ToolErrorCode.DIVISION_BY_ZERO, "除数为零")
            else:
                result /= value
            if not math.isfinite(result):
                raise ToolExecutionError(
                    ToolErrorCode.EXECUTION_FAILED,
                    "计算结果不是有限数字",
                )
        return {"operation": arguments.operation, "value": result}

    return make_tool(
        name="calculate",
        description="对 2 到 32 个有限数字做有序四则运算（add/subtract/multiply/divide）。",
        arguments_model=CalculateArguments,
        handler=handler,
    )
