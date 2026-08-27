"""Task-frozen tool registry, bounded executor, and the demo in-memory tools."""

from __future__ import annotations

import asyncio
import inspect
import json
import math
import re
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field, replace
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, field_validator

from morrow.core.capabilities import (
    OperationIntent,
    OperationKind,
    PolicyDecision,
    PolicyVerdict,
    ProcessIsolation,
    ToolCallContext,
    ToolFact,
    ToolHandlerOutcome,
    ToolRunContext,
)
from morrow.core.domain import ArtifactReference, canonical_json_bytes, sha256_digest
from morrow.core.execution import (
    EffectClass,
    MissingCompletionPolicy,
    ToolExecutionDisposition,
    ToolRecoveryDeclaration,
    UnknownToolDeclarationError,
    tool_declaration,
)
from morrow.core.models import (
    FunctionToolCall,
    ToolApprovalDecision,
    ToolApprovalRequest,
    ToolDefinition,
    ToolEffect,
    ToolFunction,
)
from morrow.core.ports import ApprovalPort
from morrow.runtime.capabilities import CapabilityPolicy, CapabilityReason
from morrow.runtime.policy import RunPolicy, ToolApproval, ToolExecutionPolicy
from morrow.runtime.tool_arguments import (
    JsonSchemaArgumentsValidator,
    PydanticArgumentsValidator,
    ToolArgumentsValidationError,
    ToolArgumentsValidator,
)

ENVELOPE_MESSAGE_LIMIT = 200
APPROVAL_PREVIEW_LINE_LIMIT = 200
APPROVAL_PREVIEW_LINES_LIMIT = 8
EXPECTED_SHAPE_LIMIT = 128
_EXPECTED_SHAPE_PATTERN = re.compile(r"^[A-Za-z0-9_.:,-]{1,128}$")


def policy_denial_message(tool_name: str, reason_codes=()) -> str:
    """Return bounded, authority-neutral guidance without echoing tool arguments."""

    codes = tuple(dict.fromkeys(str(reason) for reason in reason_codes if str(reason)))
    suffix = f"（{', '.join(codes)}）" if codes else ""
    if tool_name == "run_command":
        return (
            f"当前能力策略拒绝此操作{suffix}。run_command 会自动捕获 stdout/stderr；"
            "项目检查请改用 argv，移除 shell 重定向、管道和工作区外路径。"
            "网络、依赖安装、Git 写入和破坏性操作不可绕过。"
        )
    return f"当前能力策略拒绝此操作{suffix}"


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

    def __init__(
        self,
        code: ToolErrorCode,
        message: str,
        *,
        disposition: ToolExecutionDisposition | None = None,
        facts: tuple[ToolFact, ...] = (),
        details: tuple[dict[str, str], ...] = (),
    ) -> None:
        super().__init__(message)
        self.code = code
        self.disposition = disposition
        self.facts = tuple(facts)
        self.details = tuple(details)


def _dump(payload: dict) -> str:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def tool_error_envelope(
    code: ToolErrorCode,
    message: str,
    *,
    details: list[dict[str, str]] | None = None,
    expected: str | None = None,
) -> str:
    bounded = " ".join(str(message).split())[:ENVELOPE_MESSAGE_LIMIT]
    error: dict = {"code": code.value, "message": bounded}
    if details:
        error["details"] = details
    if (
        isinstance(expected, str)
        and len(expected) <= EXPECTED_SHAPE_LIMIT
        and _EXPECTED_SHAPE_PATTERN.fullmatch(expected) is not None
    ):
        error["expected"] = expected
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
    return PydanticArgumentsValidator(model).schema


class ToolContractAuditError(ValueError):
    """A registered tool cannot be proven safe and stable for the Provider wire."""


@dataclass(frozen=True, slots=True)
class ToolContractExpectation:
    """Independent local expectations for one static Direct tool."""

    intent_kind: OperationKind
    intent_effect: ToolEffect
    requires_host: bool | None = False
    requires_sandbox: bool | None = False
    policy_effect: ToolEffect = ToolEffect.NONE
    policy_approval: ToolApproval = ToolApproval.NEVER


def _static_contract(
    kind: OperationKind,
    effect: ToolEffect = ToolEffect.NONE,
    *,
    requires_host: bool | None = False,
    requires_sandbox: bool | None = False,
    policy_effect: ToolEffect = ToolEffect.NONE,
    policy_approval: ToolApproval = ToolApproval.NEVER,
) -> ToolContractExpectation:
    return ToolContractExpectation(
        intent_kind=kind,
        intent_effect=effect,
        requires_host=requires_host,
        requires_sandbox=requires_sandbox,
        policy_effect=policy_effect,
        policy_approval=policy_approval,
    )


_STATIC_TOOL_CONTRACTS: Mapping[str, ToolContractExpectation] = MappingProxyType(
    {
        "list_directory": _static_contract(OperationKind.WORKSPACE_READ),
        "read_file": _static_contract(OperationKind.WORKSPACE_READ),
        "find_files": _static_contract(OperationKind.WORKSPACE_READ),
        "search_text": _static_contract(OperationKind.WORKSPACE_READ),
        "read_artifact": _static_contract(OperationKind.INTERNAL_READ),
        "show_changes": _static_contract(OperationKind.INTERNAL_READ),
        "git_status": _static_contract(OperationKind.GIT_READ),
        "git_diff": _static_contract(OperationKind.GIT_READ),
        "update_configuration": _static_contract(
            OperationKind.CONFIGURATION_WRITE,
            ToolEffect.PERSISTENT_WRITE,
            policy_effect=ToolEffect.PERSISTENT_WRITE,
            policy_approval=ToolApproval.REQUIRED,
        ),
        "manage_preferences": _static_contract(
            OperationKind.CONFIGURATION_WRITE,
            ToolEffect.PERSISTENT_WRITE,
            policy_effect=ToolEffect.PERSISTENT_WRITE,
            policy_approval=ToolApproval.REQUIRED,
        ),
        "apply_patch": _static_contract(OperationKind.WORKSPACE_WRITE, ToolEffect.PERSISTENT_WRITE),
        "write_file": _static_contract(OperationKind.WORKSPACE_WRITE, ToolEffect.PERSISTENT_WRITE),
        "delete_file": _static_contract(
            OperationKind.WORKSPACE_WRITE,
            ToolEffect.PERSISTENT_WRITE,
            policy_effect=ToolEffect.PERSISTENT_WRITE,
            policy_approval=ToolApproval.REQUIRED,
        ),
        "move_file": _static_contract(
            OperationKind.WORKSPACE_WRITE,
            ToolEffect.PERSISTENT_WRITE,
            policy_effect=ToolEffect.PERSISTENT_WRITE,
            policy_approval=ToolApproval.REQUIRED,
        ),
        "rename_file": _static_contract(
            OperationKind.WORKSPACE_WRITE,
            ToolEffect.PERSISTENT_WRITE,
            policy_effect=ToolEffect.PERSISTENT_WRITE,
            policy_approval=ToolApproval.REQUIRED,
        ),
        "promote_sandbox_changes": _static_contract(
            OperationKind.WORKSPACE_WRITE,
            ToolEffect.PERSISTENT_WRITE,
            policy_effect=ToolEffect.PERSISTENT_WRITE,
            policy_approval=ToolApproval.REQUIRED,
        ),
        "run_command": _static_contract(
            OperationKind.PROCESS,
            requires_host=None,
            requires_sandbox=None,
        ),
        "run_skill_script": _static_contract(
            OperationKind.PROCESS,
            ToolEffect.SESSION_WRITE,
            requires_host=False,
            requires_sandbox=True,
            policy_effect=ToolEffect.SESSION_WRITE,
            policy_approval=ToolApproval.REQUIRED,
        ),
    }
)


def _static_contract_for(
    name: str, *, process_isolation: ProcessIsolation | None = None
) -> ToolContractExpectation | None:
    expected = _STATIC_TOOL_CONTRACTS.get(name)
    if expected is None:
        return None
    if name != "run_command" or process_isolation is None:
        return expected
    return replace(
        expected,
        requires_host=process_isolation is ProcessIsolation.HOST,
        requires_sandbox=process_isolation is ProcessIsolation.NATIVE_SANDBOX,
    )


@dataclass(frozen=True, slots=True)
class ToolContractAudit:
    """Value-free evidence that one registered tool passed the contract audit."""

    tool_name: str
    schema_digest: str
    wire_digest: str
    recovery_name: str
    has_handler: bool
    has_intent_resolver: bool
    has_policy_resolver: bool


def _contract_failure(name: str, reason: str) -> ToolContractAuditError:
    return ToolContractAuditError(f"tool contract audit failed for {name}: {reason}")


def _schema_objects_are_closed(schema: dict[str, Any]) -> bool:
    definitions = schema.get("$defs", {})
    active_refs: set[str] = set()

    def visit(node: Any) -> bool:
        if isinstance(node, bool):
            return True
        if not isinstance(node, dict):
            return False
        ref = node.get("$ref")
        if ref is not None:
            name = ref.removeprefix("#/$defs/") if isinstance(ref, str) else ""
            target = definitions.get(name) if isinstance(definitions, dict) else None
            if name in active_refs or target is None:
                return False
            active_refs.add(name)
            closed = visit(target)
            active_refs.remove(name)
            if not closed:
                return False
        node_type = node.get("type")
        if node_type == "object" or (isinstance(node_type, list) and "object" in node_type):
            if node.get("additionalProperties", True) is not False:
                return False
        properties = node.get("properties")
        if isinstance(properties, dict) and not all(visit(child) for child in properties.values()):
            return False
        for keyword in ("items", "not", "additionalProperties"):
            child = node.get(keyword)
            if isinstance(child, (dict, bool)) and not visit(child):
                return False
        for keyword in ("prefixItems", "anyOf", "oneOf", "allOf"):
            children = node.get(keyword)
            if isinstance(children, list) and not all(visit(child) for child in children):
                return False
        return True

    return visit(schema) and all(visit(definition) for definition in definitions.values())


def audit_registered_tool(
    registered: RegisteredTool,
    *,
    require_runtime_contract: bool = False,
    require_closed_schema: bool = False,
    require_production_declaration: bool = False,
    expected_process_isolation: ProcessIsolation | None = None,
) -> ToolContractAudit:
    """Fail closed when a registered tool and its final Provider contract drift apart.

    The audit only retains names, booleans and digests.  It deliberately never formats the
    validator, handler, arguments, results or exception details into an error or record.
    """

    name = registered.definition.function.name
    validator = registered.arguments_validator
    if validator is None:
        raise _contract_failure(name, "arguments validator is missing")
    if not callable(registered.handler):
        raise _contract_failure(name, "handler is missing")
    if registered.context_handler is not None and not callable(registered.context_handler):
        raise _contract_failure(name, "context handler is invalid")
    if registered.intent_resolver is not None and not callable(registered.intent_resolver):
        raise _contract_failure(name, "intent resolver is invalid")
    if registered.policy_resolver is not None and not callable(registered.policy_resolver):
        raise _contract_failure(name, "policy resolver is invalid")
    if require_runtime_contract and registered.intent_resolver is None:
        raise _contract_failure(name, "intent resolver is missing")

    declaration = registered.recovery_declaration
    if declaration is None or declaration.tool_name != name:
        raise _contract_failure(name, "recovery declaration is missing or mismatched")
    check_static_declaration = require_production_declaration or (
        require_runtime_contract
        and name in _STATIC_TOOL_CONTRACTS
        and (
            name != "run_command"
            or expected_process_isolation is not None
            or declaration.process_isolation is not None
        )
    )
    if check_static_declaration:
        declaration_isolation = (
            expected_process_isolation
            if expected_process_isolation is not None
            else declaration.process_isolation
        )
        if name == "run_command" and declaration_isolation is None:
            raise _contract_failure(name, "expected process isolation is missing")
        try:
            expected = tool_declaration(
                name,
                process_isolation=declaration_isolation if name == "run_command" else None,
                production_only=require_production_declaration,
            )
        except UnknownToolDeclarationError:
            label = "production " if require_production_declaration else "static "
            raise _contract_failure(name, f"{label}recovery declaration is unavailable") from None
        if declaration != expected:
            raise _contract_failure(name, "recovery declaration drifted")

    expected_contract = _static_contract_for(
        name,
        process_isolation=(
            expected_process_isolation
            if expected_process_isolation is not None
            else declaration.process_isolation
        ),
    )
    if require_runtime_contract and expected_contract is not None:
        if registered.runtime_contract != expected_contract:
            raise _contract_failure(name, "static runtime contract drifted")
        if (
            registered.execution_policy.effect is not expected_contract.policy_effect
            or registered.execution_policy.approval is not expected_contract.policy_approval
        ):
            raise _contract_failure(name, "execution policy contract drifted")

    try:
        validator_schema = validator.schema
        normalized = JsonSchemaArgumentsValidator(validator_schema)
        normalized_schema = normalized.schema
        validator_digest = getattr(validator, "schema_digest", normalized.schema_digest)
    except Exception:
        raise _contract_failure(name, "arguments schema is unsupported") from None
    if not isinstance(normalized_schema, dict) or normalized_schema.get("type") != "object":
        raise _contract_failure(name, "arguments schema must be an object")
    if require_closed_schema and not _schema_objects_are_closed(normalized_schema):
        raise _contract_failure(name, "production arguments schema must forbid extras")
    if validator_schema != normalized_schema:
        raise _contract_failure(name, "validator schema is not normalized")
    if registered.definition.function.parameters != normalized_schema:
        raise _contract_failure(name, "definition schema drifted from validator")
    if validator_digest != normalized.schema_digest:
        raise _contract_failure(name, "validator schema digest drifted")

    try:
        from morrow.adapters.models.openai_compatible import serialize_tool

        wire = serialize_tool(registered.definition)
        if set(wire) != {"type", "function"} or wire.get("type") != "function":
            raise ValueError
        function = wire.get("function")
        if not isinstance(function, dict) or set(function) != {"name", "description", "parameters"}:
            raise ValueError
        if function.get("name") != name or function.get("parameters") != normalized_schema:
            raise ValueError
        wire_schema = JsonSchemaArgumentsValidator(function["parameters"])
        if wire_schema.schema != normalized_schema:
            raise ValueError
        wire_digest = sha256_digest(canonical_json_bytes(wire))
    except Exception:
        raise _contract_failure(name, "Provider wire serialization is unstable") from None
    return ToolContractAudit(
        tool_name=name,
        schema_digest=normalized.schema_digest,
        wire_digest=wire_digest,
        recovery_name=declaration.tool_name,
        has_handler=True,
        has_intent_resolver=registered.intent_resolver is not None,
        has_policy_resolver=registered.policy_resolver is not None,
    )


def audit_tool_contracts(
    registered_tools: Mapping[str, RegisteredTool] | tuple[RegisteredTool, ...],
    *,
    require_runtime_contract: bool = False,
    require_closed_schema: bool = False,
    require_production_declaration: bool = False,
    expected_process_isolation: ProcessIsolation | None = None,
) -> tuple[ToolContractAudit, ...]:
    """Audit a deterministic tool inventory and return only safe audit records."""

    values = (
        tuple(registered_tools.values())
        if isinstance(registered_tools, Mapping)
        else tuple(registered_tools)
    )
    audits = tuple(
        audit_registered_tool(
            registered,
            require_runtime_contract=require_runtime_contract,
            require_closed_schema=require_closed_schema,
            require_production_declaration=require_production_declaration,
            expected_process_isolation=expected_process_isolation,
        )
        for registered in sorted(values, key=lambda item: item.definition.function.name)
    )
    names = [audit.tool_name for audit in audits]
    if len(names) != len(set(names)):
        raise ToolContractAuditError("tool contract audit failed: duplicate tool name")
    return audits


ApprovalPreview = Callable[[BaseModel], tuple[str, ...] | list[str]]
IntentResolver = Callable[
    [BaseModel, ToolCallContext], OperationIntent | Awaitable[OperationIntent]
]
PolicyResolver = Callable[
    [OperationIntent, ToolCallContext, bool], PolicyDecision | Awaitable[PolicyDecision]
]
ContextHandler = Callable[[BaseModel, ToolCallContext], Awaitable[object]]
ContextApprovalPreview = Callable[[BaseModel, ToolCallContext], tuple[str, ...] | list[str]]
ContextCleanup = Callable[[ToolCallContext], None]


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


def _fallback_recovery_declaration(name: str) -> ToolRecoveryDeclaration:
    """Keep legacy test and extension tools conservative until they declare recovery."""

    return ToolRecoveryDeclaration(
        tool_name=name,
        effect_class=EffectClass.UNCONFINED_EXTERNAL_EFFECT,
        missing_handler_completed=MissingCompletionPolicy.OUTCOME_UNKNOWN,
    )


def _recovery_declaration(
    name: str, declaration: ToolRecoveryDeclaration | None = None
) -> ToolRecoveryDeclaration:
    if declaration is not None:
        return declaration
    try:
        return tool_declaration(name)
    except UnknownToolDeclarationError:
        return _fallback_recovery_declaration(name)


@dataclass(frozen=True)
class RegisteredTool:
    definition: ToolDefinition
    handler: Callable[[BaseModel], Awaitable[object]] | ContextHandler
    arguments_validator: ToolArgumentsValidator | None = None
    execution_policy: ToolExecutionPolicy = field(default_factory=ToolExecutionPolicy)
    approval_preview: ApprovalPreview | None = None
    intent_resolver: IntentResolver | None = None
    policy_resolver: PolicyResolver | None = None
    context_handler: ContextHandler | None = None
    context_approval_preview: ContextApprovalPreview | None = None
    context_cleanup: ContextCleanup | None = None
    approval_preview_budget: ApprovalPreviewBudget = field(default_factory=ApprovalPreviewBudget)
    recovery_declaration: ToolRecoveryDeclaration | None = None
    runtime_contract: ToolContractExpectation | None = None

    def __post_init__(self) -> None:
        validator = self.arguments_validator
        if validator is None:
            raise ValueError("RegisteredTool requires an arguments validator")
        declaration = _recovery_declaration(
            self.definition.function.name, self.recovery_declaration
        )
        if self.recovery_declaration is None:
            object.__setattr__(self, "recovery_declaration", declaration)
        if declaration.tool_name != self.definition.function.name:
            raise ValueError("tool recovery declaration name must match tool definition")
        if self.runtime_contract is None:
            object.__setattr__(
                self,
                "runtime_contract",
                _static_contract_for(
                    self.definition.function.name,
                    process_isolation=declaration.process_isolation,
                ),
            )


@dataclass(frozen=True)
class ToolSet:
    """Immutable per-task view; registration mutations never reach it."""

    tools: Mapping[str, RegisteredTool]
    definitions: tuple[ToolDefinition, ...]
    audit: tuple[ToolContractAudit, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.tools, MappingProxyType):
            object.__setattr__(self, "tools", MappingProxyType(dict(self.tools)))
        if set(self.tools) != {
            registered.definition.function.name for registered in self.tools.values()
        }:
            raise ToolContractAuditError("tool contract audit failed: tool mapping drifted")
        expected = tuple(self.tools[name].definition for name in sorted(self.tools))
        if self.definitions != expected:
            raise ToolContractAuditError("tool contract audit failed: tool definitions drifted")


class ToolRegistry:
    """Mutable only during setup; `snapshot()` freezes it for one task."""

    def __init__(self) -> None:
        self._tools: dict[str, RegisteredTool] = {}

    def register(self, tool: RegisteredTool) -> None:
        name = tool.definition.function.name
        if name in self._tools:
            raise ValueError(f"duplicate tool registration: {name}")
        audit_registered_tool(tool)
        self._tools[name] = tool

    def get(self, name: str) -> RegisteredTool | None:
        return self._tools.get(name)

    def definitions(self) -> tuple[ToolDefinition, ...]:
        return tuple(self._tools[name].definition for name in sorted(self._tools))

    def snapshot(
        self,
        *,
        require_runtime_contract: bool = False,
        require_closed_schema: bool = False,
        require_production_declaration: bool = False,
        expected_process_isolation: ProcessIsolation | None = None,
    ) -> ToolSet:
        tools = MappingProxyType(dict(self._tools))
        return ToolSet(
            tools=tools,
            definitions=self.definitions(),
            audit=audit_tool_contracts(
                tools,
                require_runtime_contract=require_runtime_contract,
                require_closed_schema=require_closed_schema,
                require_production_declaration=require_production_declaration,
                expected_process_isolation=expected_process_isolation,
            ),
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
    disposition: ToolExecutionDisposition | None = None
    artifact_refs: tuple[ArtifactReference, ...] = ()
    mcp_result_artifact_refs: tuple[ArtifactReference, ...] = ()
    artifact_content: bytes | None = field(default=None, repr=False, compare=False)


class ToolExecutor:
    """One bounded outcome per call; never retries; no raw exception leaks."""

    def __init__(
        self,
        tool_set: ToolSet,
        run_policy: RunPolicy,
        approval_port: ApprovalPort | None = None,
        capability_policy: CapabilityPolicy | None = None,
        expected_process_isolation: ProcessIsolation | None = None,
    ) -> None:
        resolved_isolation = expected_process_isolation
        self.audit = audit_tool_contracts(
            tool_set.tools,
            require_runtime_contract=capability_policy is not None,
            expected_process_isolation=resolved_isolation,
        )
        self.tool_set = tool_set
        self.run_policy = run_policy
        self.approval_port = approval_port
        self.capability_policy = capability_policy
        self.expected_process_isolation = resolved_isolation
        self.long_horizon = run_policy.is_long_horizon
        self.truncation_max_bytes = (
            run_policy.truncation_max_bytes if self.long_horizon else 8 * 1024
        )
        self.truncation_max_lines = run_policy.truncation_max_lines if self.long_horizon else 400
        self.grep_max_line_chars = run_policy.grep_max_line_chars if self.long_horizon else 512
        self._active_run_context: ToolRunContext | None = None
        self._active_ordinal = 1
        self._active_total = 1

    @property
    def definitions(self) -> tuple[ToolDefinition, ...]:
        return self.tool_set.definitions

    def recovery_declaration(self, tool_name: str) -> ToolRecoveryDeclaration:
        registered = self.tool_set.tools.get(tool_name)
        if registered is None or registered.recovery_declaration is None:
            return _fallback_recovery_declaration(tool_name)
        return registered.recovery_declaration

    def resolve_policy(
        self,
        registered: RegisteredTool,
        intent: OperationIntent,
        context: ToolCallContext,
        *,
        allow_unconfined_host: bool,
    ) -> PolicyDecision:
        """Resolve an ordinary or narrowly specialized policy decision."""

        if registered.policy_resolver is not None:
            decision = registered.policy_resolver(intent, context, allow_unconfined_host)
            if inspect.isawaitable(decision):
                raise ToolExecutionError(
                    ToolErrorCode.PREFLIGHT_FAILED,
                    "同步工具预检不能等待外部策略",
                )
            if not isinstance(decision, PolicyDecision):
                raise ToolExecutionError(ToolErrorCode.PREFLIGHT_FAILED, "工具策略结果无效")
            return decision
        if self.capability_policy is None:
            raise ToolExecutionError(ToolErrorCode.PREFLIGHT_FAILED, "能力策略不可用")
        return self.capability_policy.evaluate(intent, allow_unconfined_host=allow_unconfined_host)

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
        allow_unconfined_host: bool = False,
    ) -> ToolExecutionOutcome:
        limit = result_limit or self.run_policy.effective_result_limit
        registered = self.tool_set.tools.get(call.name)
        if registered is None:
            return self._error(
                call, ToolErrorCode.UNKNOWN_TOOL, f"未注册的工具: {call.name}", limit=limit
            )
        try:
            arguments = registered.arguments_validator.validate(call.arguments)
        except ToolArgumentsValidationError as exc:
            return self._error(
                call,
                ToolErrorCode.INVALID_ARGUMENTS,
                str(exc),
                limit=limit,
                details=list(exc.details[: self.run_policy.max_validation_errors]),
                expected=exc.expected,
            )
        except Exception:
            return self._error(
                call,
                ToolErrorCode.INVALID_ARGUMENTS,
                "工具参数校验失败",
                limit=limit,
            )
        call_context = ToolCallContext(
            run=self._active_run_context or ToolRunContext(run_id="legacy", session_id="legacy"),
            call_id=call.id,
            tool_name=call.name,
            ordinal=self._active_ordinal,
            total=self._active_total,
            result_limit=limit,
            long_horizon=self.long_horizon,
            truncation_max_bytes=self.truncation_max_bytes,
            truncation_max_lines=self.truncation_max_lines,
            grep_max_line_chars=self.grep_max_line_chars,
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
                self._validate_runtime_contract(registered, intent)
                policy_decision = self.resolve_policy(
                    registered,
                    intent,
                    call_context,
                    allow_unconfined_host=allow_unconfined_host,
                )
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
                    policy_denial_message(call.name, policy_decision.reason_codes),
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
            decision = await self.request_approval(request)
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
                failed = self._error(
                    call,
                    ToolErrorCode.OUTPUT_BUDGET if semantic else ToolErrorCode.OUTPUT_FAILED,
                    "工具结果预算不足",
                    limit=limit,
                )
                if semantic:
                    failed = replace(
                        failed,
                        artifact_refs=outcome.artifact_refs,
                        mcp_result_artifact_refs=outcome.mcp_result_artifact_refs,
                        artifact_content=outcome.artifact_content,
                    )
                return failed
            return ToolExecutionOutcome(
                call_id=call.id,
                name=call.name,
                ok=True,
                envelope=envelope,
                truncated=truncated,
                original_chars=original_chars,
                facts=outcome.facts,
                artifact_refs=outcome.artifact_refs,
                mcp_result_artifact_refs=outcome.mcp_result_artifact_refs,
                artifact_content=outcome.artifact_content,
            )
        except asyncio.CancelledError:
            raise
        except ToolExecutionError as exc:
            if self._active_run_context is not None and exc.facts:
                self._active_run_context.record(exc.facts)
            return self._error(
                call,
                exc.code,
                str(exc),
                limit=limit,
                disposition=exc.disposition,
                details=list(exc.details),
                facts=exc.facts,
            )
        except Exception:
            return self._error(call, ToolErrorCode.EXECUTION_FAILED, "工具执行失败", limit=limit)

    @staticmethod
    def _validate_runtime_contract(registered: RegisteredTool, intent: OperationIntent) -> None:
        expected = registered.runtime_contract
        if expected is None:
            return
        if (
            intent.kind is not expected.intent_kind
            or intent.effect is not expected.intent_effect
            or (
                expected.requires_host is not None
                and intent.requires_host is not expected.requires_host
            )
            or (
                expected.requires_sandbox is not None
                and intent.requires_sandbox is not expected.requires_sandbox
            )
        ):
            raise ToolExecutionError(
                ToolErrorCode.PREFLIGHT_FAILED,
                "工具能力预检与静态契约不一致",
            )

    async def request_approval(self, request: ToolApprovalRequest) -> ToolApprovalDecision | None:
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
        allow_unconfined_host: bool = False,
    ) -> ToolExecutionOutcome:
        previous = (self._active_run_context, self._active_ordinal, self._active_total)
        self._active_run_context = run_context
        self._active_ordinal = ordinal
        self._active_total = total
        try:
            extra = {"skip_approval": True} if skip_approval else {}
            if allow_unconfined_host:
                extra["allow_unconfined_host"] = True
            return await self.execute(call, result_limit=result_limit, **extra)
        finally:
            registered = self.tool_set.tools.get(call.name)
            if registered is not None and registered.context_cleanup is not None:
                cleanup_context = ToolCallContext(
                    run=run_context,
                    call_id=call.id,
                    tool_name=call.name,
                    ordinal=ordinal,
                    total=total,
                    result_limit=result_limit or self.run_policy.effective_result_limit,
                    long_horizon=self.long_horizon,
                    truncation_max_bytes=self.truncation_max_bytes,
                    truncation_max_lines=self.truncation_max_lines,
                    grep_max_line_chars=self.grep_max_line_chars,
                )
                try:
                    registered.context_cleanup(cleanup_context)
                except Exception:
                    # Prepared plans are an in-memory optimization. Cleanup failure must
                    # never replace the durable tool outcome or mask cancellation.
                    pass
            self._active_run_context, self._active_ordinal, self._active_total = previous

    def cleanup_call(
        self,
        call: FunctionToolCall,
        *,
        run_context: ToolRunContext,
        ordinal: int,
        total: int,
        result_limit: int,
    ) -> None:
        """Release ephemeral preparation for a call that never enters execute()."""
        registered = self.tool_set.tools.get(call.name)
        if registered is None or registered.context_cleanup is None:
            return
        context = ToolCallContext(
            run=run_context,
            call_id=call.id,
            tool_name=call.name,
            ordinal=ordinal,
            total=total,
            result_limit=result_limit,
            long_horizon=self.long_horizon,
            truncation_max_bytes=self.truncation_max_bytes,
            truncation_max_lines=self.truncation_max_lines,
            grep_max_line_chars=self.grep_max_line_chars,
        )
        try:
            registered.context_cleanup(context)
        except Exception:
            pass

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
        expected: str | None = None,
        disposition: ToolExecutionDisposition | None = None,
        facts: tuple[ToolFact, ...] = (),
    ) -> ToolExecutionOutcome:
        envelope = tool_error_envelope(code, message, details=details, expected=expected)
        if len(envelope) > limit and details:
            envelope = tool_error_envelope(code, message, expected=expected)
        if len(envelope) > limit and expected:
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
            facts=facts,
            disposition=disposition,
        )


def make_tool(
    *,
    name: str,
    description: str,
    arguments_model: type[BaseModel] | None = None,
    arguments_validator: ToolArgumentsValidator | None = None,
    provider_schema: Mapping[str, Any] | bool | None = None,
    arguments_schema: Mapping[str, Any] | bool | None = None,
    expected_shape: str | None = None,
    handler: Callable[[BaseModel], Awaitable[object]] | ContextHandler,
    execution_policy: ToolExecutionPolicy | None = None,
    approval_preview: ApprovalPreview | None = None,
    intent_resolver: IntentResolver | None = None,
    policy_resolver: PolicyResolver | None = None,
    context_handler: ContextHandler | None = None,
    context_approval_preview: ContextApprovalPreview | None = None,
    context_cleanup: ContextCleanup | None = None,
    approval_preview_budget: ApprovalPreviewBudget | None = None,
    recovery_declaration: ToolRecoveryDeclaration | None = None,
) -> RegisteredTool:
    if provider_schema is not None and arguments_schema is not None:
        raise ValueError("make_tool accepts only one explicit Provider schema")
    explicit_schema = provider_schema if provider_schema is not None else arguments_schema
    validator = arguments_validator
    if validator is None and arguments_model is not None:
        validator = PydanticArgumentsValidator(
            arguments_model,
            provider_schema=explicit_schema,
            expected_shape=expected_shape,
        )
    elif explicit_schema is not None:
        raise ValueError("an explicit Provider schema requires an arguments model")
    if validator is None:
        raise ValueError("make_tool requires an arguments model or validator")
    if expected_shape is not None and arguments_model is None:
        raise ValueError("expected validation shape requires an arguments model")
    schema = validator.schema
    if not isinstance(schema, dict):
        raise ValueError("tool arguments schema must be an object")
    declaration = _recovery_declaration(name, recovery_declaration)
    return RegisteredTool(
        definition=ToolDefinition(
            function=ToolFunction(
                name=name,
                description=description,
                parameters=schema,
            )
        ),
        handler=handler,
        arguments_validator=validator,
        execution_policy=execution_policy or ToolExecutionPolicy(),
        approval_preview=approval_preview,
        intent_resolver=intent_resolver,
        policy_resolver=policy_resolver,
        context_handler=context_handler,
        context_approval_preview=context_approval_preview,
        context_cleanup=context_cleanup,
        approval_preview_budget=approval_preview_budget or ApprovalPreviewBudget(),
        recovery_declaration=declaration,
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
        recovery_declaration=tool_declaration("lookup_record"),
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
        recovery_declaration=tool_declaration("calculate"),
    )
