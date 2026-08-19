"""Task-frozen tool registry, bounded executor, and the demo in-memory tools."""

from __future__ import annotations

import asyncio
import inspect
import json
import math
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field, replace
from enum import StrEnum
from types import MappingProxyType
from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from morrow.core.capabilities import (
    OperationIntent,
    OperationKind,
    PolicyVerdict,
    ToolCallContext,
    ToolFact,
    ToolHandlerOutcome,
    ToolRunContext,
)
from morrow.core.models import (
    FunctionToolCall,
    ToolApprovalDecision,
    ToolApprovalRequest,
    ToolDefinition,
    ToolFunction,
)
from morrow.core.ports import ApprovalPort
from morrow.runtime.capabilities import CapabilityPolicy, CapabilityReason
from morrow.runtime.policy import RunPolicy, ToolApproval, ToolExecutionPolicy

ENVELOPE_MESSAGE_LIMIT = 200
APPROVAL_PREVIEW_LINE_LIMIT = 200
APPROVAL_PREVIEW_LINES_LIMIT = 8


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
    APPROVAL_REJECTED = "approval_rejected"
    APPROVAL_UNAVAILABLE = "approval_unavailable"
    APPROVAL_PREVIEW_FAILED = "approval_preview_failed"
    PERMISSION_DENIED = "permission_denied"
    UNSUPPORTED_CAPABILITY = "unsupported_capability"
    PREFLIGHT_FAILED = "preflight_failed"
    OUTPUT_BUDGET = "output_budget"
    INVALID_PATH = "invalid_path"
    OUTSIDE_WORKSPACE = "outside_workspace"
    INVALID_TARGET = "invalid_target"
    BINARY_FILE = "binary_file"
    INVALID_UTF8 = "invalid_utf8"
    FILE_TOO_LARGE = "file_too_large"
    SEARCH_FAILED = "search_failed"
    SEARCH_BUDGET = "search_budget"
    INVALID_PATTERN = "invalid_pattern"
    INVALID_GLOB = "invalid_glob"
    INVALID_RANGE = "invalid_range"
    INVALID_DEPTH = "invalid_depth"
    INVALID_LIMIT = "invalid_limit"
    READ_FAILED = "read_failed"
    LIST_FAILED = "list_failed"
    PATH_UNAVAILABLE = "path_unavailable"
    SYMLINK_NOT_ALLOWED = "symlink_not_allowed"
    CONFLICT = "conflict"
    EDIT_NOT_FOUND = "edit_not_found"
    EDIT_NOT_UNIQUE = "edit_not_unique"
    EDIT_OVERLAP = "edit_overlap"
    MUTATION_LIMIT = "mutation_limit"
    PROTECTED_RESOURCE = "protected_resource"
    PUBLISH_FAILED = "publish_failed"
    INVALID_COMMAND = "invalid_command"
    PROCESS_FAILED = "process_failed"
    PROCESS_CLEANUP_FAILED = "process_cleanup_failed"
    SANDBOX_UNAVAILABLE = "sandbox_unavailable"
    SANDBOX_VIOLATION = "sandbox_violation"
    SANDBOX_LIMIT = "sandbox_limit"
    EXTERNAL_GIT_METADATA = "external_git_metadata"
    GIT_UNAVAILABLE = "git_unavailable"
    GIT_TIMEOUT = "git_timeout"
    GIT_FAILED = "git_failed"
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


# This lower bound applies only to AgentLoop-generated synthetic error envelopes.
_SYNTHETIC_ERROR_CODES = (
    ToolErrorCode.INVALID_ARGUMENTS,
    ToolErrorCode.UNKNOWN_TOOL,
    ToolErrorCode.NOT_FOUND,
    ToolErrorCode.DIVISION_BY_ZERO,
    ToolErrorCode.EXECUTION_FAILED,
    ToolErrorCode.TIMEOUT,
    ToolErrorCode.OUTPUT_FAILED,
    ToolErrorCode.CANCELLED,
    ToolErrorCode.BUDGET_EXHAUSTED,
    ToolErrorCode.INTERNAL,
)
MIN_ERROR_ENVELOPE_CHARS = max(
    len(tool_error_envelope(code, "")) for code in _SYNTHETIC_ERROR_CODES
)


def tool_parameters_from_model(model: type[BaseModel]) -> dict:
    """Return the complete Pydantic schema used by the standard tool wire."""
    return model.model_json_schema()


ApprovalPreview = Callable[[BaseModel], tuple[str, ...] | list[str]]
IntentResolver = Callable[
    [BaseModel, ToolCallContext], OperationIntent | Awaitable[OperationIntent]
]
ContextHandler = Callable[[BaseModel, ToolCallContext], Awaitable[object]]
ContextApprovalPreview = Callable[[BaseModel, ToolCallContext], tuple[str, ...] | list[str]]


@dataclass(frozen=True)
class ApprovalPreviewBudget:
    max_lines: int = 8
    max_line_chars: int = 200
    max_bytes: int = 1600
    preserve_whitespace: bool = False

    def __post_init__(self) -> None:
        if self.max_lines < 1 or self.max_line_chars < 1 or self.max_bytes < 1:
            raise ValueError("approval preview budget must be positive")


def _sanitize_approval_preview(
    value: object, *, budget: ApprovalPreviewBudget = ApprovalPreviewBudget()
) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str) or not isinstance(value, (list, tuple)):
        raise ValueError("approval preview must be a list or tuple of strings")
    lines: list[str] = []
    total_bytes = 0
    for raw_line in value[: budget.max_lines]:
        if not isinstance(raw_line, str):
            raise ValueError("approval preview lines must be strings")
        if budget.preserve_whitespace:
            line = "".join(char for char in raw_line if char == "\t" or ord(char) >= 32)
        else:
            line = " ".join(raw_line.split())
        if line:
            line = line[: budget.max_line_chars]
            encoded_length = len(line.encode("utf-8"))
            if total_bytes + encoded_length > budget.max_bytes:
                break
            lines.append(line)
            total_bytes += encoded_length
    return tuple(lines)


@dataclass(frozen=True)
class RegisteredTool:
    definition: ToolDefinition
    arguments_model: type[BaseModel]
    handler: Callable[[BaseModel], Awaitable[object]] | ContextHandler
    execution_policy: ToolExecutionPolicy = field(default_factory=ToolExecutionPolicy)
    approval_preview: ApprovalPreview | None = None
    intent_resolver: IntentResolver | None = None
    context_handler: ContextHandler | None = None
    context_approval_preview: ContextApprovalPreview | None = None
    approval_preview_budget: ApprovalPreviewBudget = field(default_factory=ApprovalPreviewBudget)


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
    facts: tuple[ToolFact, ...] = ()


class ToolExecutor:
    """One bounded outcome per call; never retries; no raw exception leaks."""

    def __init__(
        self,
        tool_set: ToolSet,
        run_policy: RunPolicy,
        approval_port: ApprovalPort | None = None,
        capability_policy: CapabilityPolicy | None = None,
    ) -> None:
        self.tool_set = tool_set
        self.run_policy = run_policy
        self.approval_port = approval_port
        self.capability_policy = capability_policy
        self._active_run_context: ToolRunContext | None = None
        self._active_ordinal = 1
        self._active_total = 1

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
        self,
        call: FunctionToolCall,
        *,
        result_limit: int | None = None,
        skip_approval: bool = False,
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
        call_context = ToolCallContext(
            run=self._active_run_context or ToolRunContext(run_id="legacy", session_id="legacy"),
            call_id=call.id,
            tool_name=call.name,
            ordinal=self._active_ordinal,
            total=self._active_total,
            result_limit=limit,
        )
        policy_decision = None
        if self.capability_policy is not None:
            try:
                if registered.intent_resolver is None:
                    raise ToolExecutionError(
                        ToolErrorCode.PREFLIGHT_FAILED,
                        "工具未提供本地能力预检",
                    )
                intent = registered.intent_resolver(arguments, call_context)
                if inspect.isawaitable(intent):
                    intent = await intent
                if not isinstance(intent, OperationIntent):
                    raise ToolExecutionError(
                        ToolErrorCode.PREFLIGHT_FAILED,
                        "工具能力预检结果无效",
                    )
                policy_decision = self.capability_policy.evaluate(intent)
            except asyncio.CancelledError:
                raise
            except ToolExecutionError as exc:
                return self._error(call, exc.code, str(exc), limit=limit)
            except Exception:
                return self._error(
                    call,
                    ToolErrorCode.PREFLIGHT_FAILED,
                    "工具能力预检失败",
                    limit=limit,
                )
            if policy_decision.verdict is PolicyVerdict.DENY:
                return self._error(
                    call,
                    self._policy_error_code(policy_decision.reason_codes),
                    "当前能力策略拒绝此操作",
                    limit=limit,
                )
        if (not skip_approval) and (
            (
                self.capability_policy is None
                and registered.execution_policy.approval == ToolApproval.REQUIRED
            )
            or (
                policy_decision is not None
                and policy_decision.verdict is PolicyVerdict.REQUIRE_APPROVAL
            )
        ):
            try:
                if registered.context_approval_preview is not None:
                    preview_value = registered.context_approval_preview(arguments, call_context)
                elif registered.approval_preview is not None:
                    preview_value = registered.approval_preview(arguments)
                else:
                    preview_value = ()
                local_preview = _sanitize_approval_preview(
                    preview_value, budget=registered.approval_preview_budget
                )
                policy_preview = (
                    policy_decision.preview_summary if policy_decision is not None else ()
                )
                preview = _sanitize_approval_preview(
                    (*policy_preview, *local_preview), budget=registered.approval_preview_budget
                )
                request = ToolApprovalRequest(
                    call_id=call.id,
                    effect=registered.execution_policy.effect,
                    preview=preview,
                    reason_codes=(
                        tuple(policy_decision.reason_codes)
                        if policy_decision is not None
                        else ("legacy_static_approval",)
                    ),
                )
            except asyncio.CancelledError:
                raise
            except ToolExecutionError as exc:
                return self._error(call, exc.code, str(exc), limit=limit)
            except Exception:
                return self._error(
                    call,
                    ToolErrorCode.APPROVAL_PREVIEW_FAILED,
                    "工具审批预览生成失败",
                    limit=limit,
                )
            decision = await self._request_approval(request)
            if decision is None:
                return self._error(
                    call,
                    ToolErrorCode.APPROVAL_UNAVAILABLE,
                    "工具需要审批，但当前没有可用的审批通道",
                    limit=limit,
                )
            if not decision.approved:
                return self._error(
                    call,
                    ToolErrorCode.APPROVAL_REJECTED,
                    "工具操作未获批准",
                    limit=limit,
                )
        call_context = replace(
            call_context,
            approval_verdict=(
                policy_decision.verdict
                if policy_decision is not None
                else (
                    PolicyVerdict.REQUIRE_APPROVAL
                    if registered.execution_policy.approval is ToolApproval.REQUIRED
                    else PolicyVerdict.ALLOW
                )
            ),
        )
        try:
            handler_result = (
                await registered.context_handler(arguments, call_context)
                if registered.context_handler is not None
                else await registered.handler(arguments)
            )
            outcome = (
                handler_result
                if isinstance(handler_result, ToolHandlerOutcome)
                else ToolHandlerOutcome(payload=handler_result)
            )
            if self._active_run_context is not None:
                self._active_run_context.record(outcome.facts)
            semantic = isinstance(handler_result, ToolHandlerOutcome)
            envelope, truncated, original_chars = self._success_envelope(
                outcome.payload, limit, semantic=semantic
            )
            if envelope is None:
                return self._error(
                    call,
                    ToolErrorCode.OUTPUT_BUDGET if semantic else ToolErrorCode.OUTPUT_FAILED,
                    "工具结果预算不足",
                    limit=limit,
                )
            return ToolExecutionOutcome(
                call_id=call.id,
                name=call.name,
                ok=True,
                envelope=envelope,
                truncated=truncated,
                original_chars=original_chars,
                facts=outcome.facts,
            )
        except asyncio.CancelledError:
            raise
        except ToolExecutionError as exc:
            return self._error(call, exc.code, str(exc), limit=limit)
        except Exception:
            return self._error(call, ToolErrorCode.EXECUTION_FAILED, "工具执行失败", limit=limit)

    async def _request_approval(self, request: ToolApprovalRequest) -> ToolApprovalDecision | None:
        port = self.approval_port
        if port is None:
            return None
        try:
            decision = await port.request(request)
        except asyncio.CancelledError:
            raise
        except Exception:
            return None
        if isinstance(decision, bool):
            return ToolApprovalDecision(approved=decision)
        if not isinstance(decision, ToolApprovalDecision):
            return None
        return decision

    async def execute_with_context(
        self,
        call: FunctionToolCall,
        *,
        result_limit: int | None = None,
        run_context: ToolRunContext,
        ordinal: int,
        total: int,
        skip_approval: bool = False,
    ) -> ToolExecutionOutcome:
        previous = (self._active_run_context, self._active_ordinal, self._active_total)
        self._active_run_context = run_context
        self._active_ordinal = ordinal
        self._active_total = total
        try:
            extra = {"skip_approval": True} if skip_approval else {}
            return await self.execute(call, result_limit=result_limit, **extra)
        finally:
            self._active_run_context, self._active_ordinal, self._active_total = previous

    @staticmethod
    def _policy_error_code(reason_codes) -> ToolErrorCode:
        unsupported = {
            CapabilityReason.FULL_ACCESS_UNSUPPORTED,
            CapabilityReason.SANDBOX_UNAVAILABLE,
        }
        return (
            ToolErrorCode.UNSUPPORTED_CAPABILITY
            if any(reason in unsupported for reason in reason_codes)
            else ToolErrorCode.PERMISSION_DENIED
        )

    @staticmethod
    def _success_envelope(
        result: object, limit: int, *, semantic: bool
    ) -> tuple[str | None, bool, int | None]:
        envelope = _dump({"ok": True, "result": result})
        if len(envelope) <= limit:
            return envelope, False, len(envelope)
        if not semantic:
            return ToolExecutor._legacy_success_envelope(result, limit, len(envelope))
        original_chars = len(envelope)
        base_result = {"truncated": True, "original_chars": original_chars, "content": ""}
        base = _dump({"ok": True, "result": base_result})
        if len(base) > limit:
            return None, False, original_chars
        if isinstance(result, str):
            candidates = [("content", result)]
        elif isinstance(result, Mapping):
            candidates = [
                (str(key), value)
                for key, value in result.items()
                if isinstance(value, (str, list, tuple))
            ]
        elif isinstance(result, (list, tuple)):
            candidates = [("items", result)]
        else:
            candidates = []
        for key, value in candidates:
            low, high = 0, len(value)
            bounded = base
            while low <= high:
                middle = (low + high) // 2
                if isinstance(value, str):
                    shortened = value[:middle]
                else:
                    shortened = list(value[:middle])
                candidate_result = dict(base_result)
                candidate_result["field"] = key
                candidate_result["content"] = shortened
                candidate = _dump({"ok": True, "result": candidate_result})
                if len(candidate) <= limit:
                    bounded = candidate
                    low = middle + 1
                else:
                    high = middle - 1
            if bounded != base:
                return bounded, True, original_chars
        return None, False, original_chars

    @staticmethod
    def _legacy_success_envelope(
        result: object, limit: int, original_chars: int
    ) -> tuple[str | None, bool, int | None]:
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
            return None, False, original_chars
        return bounded, True, original_chars

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
        if len(envelope) > limit:
            envelope = tool_error_envelope(ToolErrorCode.INTERNAL, "")
        if len(envelope) > limit:
            envelope = _dump({"ok": False})
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
    handler: Callable[[BaseModel], Awaitable[object]] | ContextHandler,
    execution_policy: ToolExecutionPolicy | None = None,
    approval_preview: ApprovalPreview | None = None,
    intent_resolver: IntentResolver | None = None,
    context_handler: ContextHandler | None = None,
    context_approval_preview: ContextApprovalPreview | None = None,
    approval_preview_budget: ApprovalPreviewBudget | None = None,
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
        execution_policy=execution_policy or ToolExecutionPolicy(),
        approval_preview=approval_preview,
        intent_resolver=intent_resolver,
        context_handler=context_handler,
        context_approval_preview=context_approval_preview,
        approval_preview_budget=approval_preview_budget or ApprovalPreviewBudget(),
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
        return ToolHandlerOutcome(payload=value)

    def intent(_: LookupRecordArguments, __: ToolCallContext) -> OperationIntent:
        return OperationIntent(
            kind=OperationKind.INTERNAL_READ,
            preview_summary=("读取注入的内存数据",),
        )

    return make_tool(
        name="lookup_record",
        description="查询注入的内存数据集（plans 或 regions）中的一条记录。",
        arguments_model=LookupRecordArguments,
        handler=handler,
        intent_resolver=intent,
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
        return ToolHandlerOutcome(payload={"operation": arguments.operation, "value": result})

    def intent(_: CalculateArguments, __: ToolCallContext) -> OperationIntent:
        return OperationIntent(
            kind=OperationKind.INTERNAL_READ,
            preview_summary=("执行本地有限数字计算",),
        )

    return make_tool(
        name="calculate",
        description="对 2 到 32 个有限数字做有序四则运算（add/subtract/multiply/divide）。",
        arguments_model=CalculateArguments,
        handler=handler,
        intent_resolver=intent,
    )
