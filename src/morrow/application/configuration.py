"""Typed configuration commands and the standard configuration tool factory."""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from morrow.core.capabilities import (
    OperationIntent,
    OperationKind,
    ToolCallContext,
    ToolHandlerOutcome,
)
from morrow.core.domain import (
    canonical_json_bytes,
    refuse_secret_material,
    require_payload_budget,
    sha256_digest,
)
from morrow.core.execution import tool_declaration
from morrow.core.models import StatePresence, ToolEffect
from morrow.runtime.policy import ToolApproval, ToolExecutionPolicy
from morrow.runtime.tool_arguments import MAX_STRING_CHARS, SCHEMA_DIALECT
from morrow.runtime.tools import RegisteredTool, ToolErrorCode, ToolExecutionError, make_tool

ConfigurationScope = Literal["workspace"]
ConfigurationTarget = Literal["profile"]
ConfigurationOperation = Literal["set", "unset", "append", "remove", "reset"]
PROFILE_PATHS = frozenset({"name", "summary", "goals", "tech_stack", "constraints", "conventions"})
PROFILE_LIST_PATHS = frozenset({"goals", "tech_stack", "constraints", "conventions"})


def _configuration_string(*, max_length: int, minimum: int = 1) -> dict[str, object]:
    return {
        "type": "string",
        "minLength": minimum,
        "maxLength": min(max_length, MAX_STRING_CHARS),
        "pattern": r"^(?!\s*$)(?!.*\x00)(?!.*[\x00-\x1f\x7f])[\s\S]+$",
    }


def _configuration_branch(
    operation: str,
    *,
    path_values: tuple[str, ...] = (),
    value_schema: dict[str, object] | None = None,
    required: tuple[str, ...] = (),
    forbidden: tuple[str, ...] = (),
) -> dict[str, object]:
    properties: dict[str, object] = {"operation": {"const": operation}}
    if path_values:
        properties["path"] = {"type": "string", "enum": list(path_values)}
    if value_schema is not None:
        properties["value"] = value_schema
    branch: dict[str, object] = {"properties": properties}
    if required:
        branch["required"] = list(required)
    if forbidden:
        branch["not"] = {"anyOf": [{"required": [name]} for name in forbidden]}
    return branch


_PROFILE_SCALAR_PATHS = tuple(sorted(PROFILE_PATHS - PROFILE_LIST_PATHS))
_PROFILE_LIST_PATHS = tuple(sorted(PROFILE_LIST_PATHS))

CONFIGURATION_PROVIDER_SCHEMA = {
    "$schema": SCHEMA_DIALECT,
    "type": "object",
    "properties": {
        "scope": {"type": "string", "const": "workspace"},
        "target": {"type": "string", "const": "profile"},
        "operation": {
            "type": "string",
            "enum": ["set", "unset", "append", "remove", "reset"],
        },
        "path": {
            "anyOf": [
                {"type": "string", "enum": sorted(PROFILE_PATHS)},
                {"type": "null"},
            ]
        },
        "value": {},
    },
    "required": ["scope", "target", "operation"],
    "oneOf": [
        _configuration_branch(
            "set",
            path_values=_PROFILE_SCALAR_PATHS,
            value_schema=_configuration_string(max_length=2048),
            required=("path", "value"),
        ),
        _configuration_branch(
            "append",
            path_values=_PROFILE_LIST_PATHS,
            value_schema=_configuration_string(max_length=512),
            required=("path", "value"),
        ),
        _configuration_branch(
            "remove",
            path_values=_PROFILE_LIST_PATHS,
            value_schema=_configuration_string(max_length=512),
            required=("path", "value"),
        ),
        _configuration_branch(
            "unset",
            path_values=("summary",),
            required=("path",),
            forbidden=("value",),
        ),
        _configuration_branch("reset", forbidden=("path", "value")),
    ],
    "additionalProperties": False,
}


def _validate_profile_fields(model: BaseModel, *, validate_values: bool = True) -> None:
    if model.scope != "workspace" or model.target != "profile":
        raise ValueError("update_configuration 只支持 workspace Profile")
    operation = model.operation
    path = model.path
    if operation == "reset":
        if path is not None or model.value is not None:
            raise ValueError("reset 不接受 path 或 value")
        return
    if path not in PROFILE_PATHS:
        raise ValueError(f"不允许修改字段: {path}")
    if path == "name" and operation == "unset":
        raise ValueError("Profile 的 name 不能取消设置")
    is_list = path in PROFILE_LIST_PATHS
    if operation == "unset":
        if model.value is not None or is_list:
            raise ValueError("unset 只支持可选标量字段且不接受 value")
        return
    if "value" not in model.model_fields_set:
        raise ValueError(f"{operation} 操作需要 value")
    if operation in {"append", "remove"} and not is_list:
        raise ValueError("标量字段只能使用 set 或 unset")
    if operation == "set" and is_list:
        raise ValueError("列表字段只能使用 append 或 remove")
    if validate_values and (not isinstance(model.value, str) or not model.value.strip()):
        raise ValueError("Profile 配置值必须是非空字符串")
    maximum = 512 if is_list else 2_048
    if validate_values and len(model.value) > maximum:
        raise ValueError("Profile 配置值超出长度限制")


class UpdateConfigurationArguments(BaseModel):
    """Flat, strict Provider arguments for workspace Profile operations."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    scope: Literal["workspace"]
    target: Literal["profile"]
    operation: ConfigurationOperation
    path: str | None = None
    value: Any = None

    @model_validator(mode="after")
    def valid_operation(self) -> UpdateConfigurationArguments:
        _validate_profile_fields(self)
        return self

    def to_command(self) -> ConfigurationCommand:
        payload: dict[str, Any] = {
            "scope": self.scope,
            "target": self.target,
            "operation": self.operation,
        }
        if "path" in self.model_fields_set:
            payload["path"] = self.path
        if "value" in self.model_fields_set:
            payload["value"] = self.value
        return ConfigurationCommand.model_validate(payload, strict=True)


class ConfigurationCommand(BaseModel):
    """Application-owned command shared by tools and deterministic commands."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    scope: ConfigurationScope
    target: ConfigurationTarget
    operation: ConfigurationOperation
    path: str | None = None
    value: Any = None

    @model_validator(mode="after")
    def valid_operation(self) -> ConfigurationCommand:
        _validate_profile_fields(self, validate_values=False)
        return self


class ConfigurationChangeStatus(StrEnum):
    APPLIED = "applied"
    UNCHANGED = "unchanged"


class ConfigurationChangeResult(BaseModel):
    """Minimal safe result; complete state never crosses this boundary."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    status: ConfigurationChangeStatus
    scope: ConfigurationScope
    target: ConfigurationTarget
    operation: ConfigurationOperation
    path: str | None = None
    revision: int | None = None


def configuration_state_digest(presence: StatePresence, value: BaseModel | None) -> str:
    """Digest one configuration target without confusing missing and cleared state."""

    payload = {
        "presence": presence.value,
        "value": value.model_dump(mode="json") if value is not None else None,
    }
    return sha256_digest(canonical_json_bytes(payload))


class PreparedConfigurationChange(BaseModel):
    """Bounded, immutable evidence for one configuration write.

    The DTO is deliberately a change description rather than a cached target value.  Applying it
    must reload the authority and prove that the exact state observed during preparation still
    exists before publishing a new YAML revision.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    command: ConfigurationCommand
    expected_revision: int | None = Field(default=None, ge=0)
    before_presence: StatePresence
    before_digest: str
    after_digest: str
    expected_applied_revision: int | None = Field(default=None, ge=1)
    inverse_command: ConfigurationCommand | None = None
    changed: bool
    preview_lines: tuple[str, ...] = Field(max_length=16)
    preparation_version: int = Field(default=1, ge=1, le=1)

    @field_validator("before_digest", "after_digest")
    @classmethod
    def valid_digest(cls, value: str) -> str:
        if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
            raise ValueError("configuration digest must be a SHA-256 hex digest")
        return value

    @model_validator(mode="after")
    def bounded_and_consistent(self) -> PreparedConfigurationChange:
        payload = canonical_json_bytes(self.model_dump(mode="json"))
        require_payload_budget(payload, 8 * 1024, label="prepared configuration")
        refuse_secret_material(payload, label="prepared configuration")
        if self.changed and self.expected_applied_revision is None:
            if self.command.scope != "session":
                raise ValueError("changed persisted configuration requires an applied revision")
        if not self.changed and self.expected_applied_revision is not None:
            raise ValueError("unchanged configuration cannot have an applied revision")
        return self


def render_configuration_preview(command: ConfigurationCommand) -> list[str]:
    lines = [
        "配置预览：",
        f"作用域：{command.scope}",
        f"目标：{command.target}",
    ]
    line = f"- {command.operation}"
    if command.path is not None:
        line += f" {command.path}"
    if command.operation not in {"unset", "reset"}:
        line += f" = {command.value}"
    lines.append(line)
    return lines


CONFIGURATION_TOOL_DESCRIPTION = (
    "仅当用户明确要求保存或更新当前 workspace Profile 时调用；Preference 使用独立的 "
    "manage_preferences 工具。缺少操作或必需 path/value 时先澄清，不要猜测。Provider、凭据、"
    "活动模型、workspace identity、权限、安全策略和其他敏感目标不在此工具范围内。"
)


def _configuration_tool_error(error: Exception) -> ToolExecutionError:
    from morrow.services.profile_configuration import (
        ConfigurationConflictError,
        ConfigurationNotFoundError,
        ConfigurationReadOnlyError,
        ConfigurationStateError,
        ConfigurationValidationError,
    )

    if isinstance(error, ConfigurationValidationError):
        return ToolExecutionError(ToolErrorCode.INVALID_ARGUMENTS, "配置操作无效")
    if isinstance(error, ConfigurationNotFoundError):
        return ToolExecutionError(ToolErrorCode.NOT_FOUND, "配置目标不存在")
    if isinstance(error, ConfigurationConflictError):
        return ToolExecutionError(ToolErrorCode.EXECUTION_FAILED, "配置版本冲突")
    if isinstance(error, ConfigurationReadOnlyError):
        return ToolExecutionError(ToolErrorCode.EXECUTION_FAILED, "配置当前不可写")
    if isinstance(error, ConfigurationStateError):
        return ToolExecutionError(ToolErrorCode.EXECUTION_FAILED, "配置状态操作失败")
    return ToolExecutionError(ToolErrorCode.EXECUTION_FAILED, "配置操作失败")


def make_configuration_tool(config_service) -> RegisteredTool:
    """Build the thin approved adapter over the shared configuration service."""

    def preview(arguments: UpdateConfigurationArguments) -> list[str]:
        command = arguments.to_command()
        try:
            config_service.preflight(command)
        except Exception as exc:
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            raise _configuration_tool_error(exc) from None
        return render_configuration_preview(command)

    async def handler(arguments: UpdateConfigurationArguments) -> object:
        command = arguments.to_command()
        try:
            result = config_service.apply_command(command)
        except Exception as exc:
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            raise _configuration_tool_error(exc) from None
        return ToolHandlerOutcome(payload=result.model_dump(mode="json"))

    def intent(arguments: UpdateConfigurationArguments, _: ToolCallContext) -> OperationIntent:
        command = arguments.to_command()
        try:
            config_service.preflight(command)
        except Exception as exc:
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            raise _configuration_tool_error(exc) from None
        return OperationIntent(
            kind=OperationKind.CONFIGURATION_WRITE,
            effect=ToolEffect.PERSISTENT_WRITE,
            preview_summary=tuple(render_configuration_preview(command)),
        )

    return make_tool(
        name="update_configuration",
        description=CONFIGURATION_TOOL_DESCRIPTION,
        arguments_model=UpdateConfigurationArguments,
        provider_schema=CONFIGURATION_PROVIDER_SCHEMA,
        expected_shape="configuration_operation_shape",
        handler=handler,
        execution_policy=ToolExecutionPolicy(
            effect=ToolEffect.PERSISTENT_WRITE,
            approval=ToolApproval.REQUIRED,
        ),
        approval_preview=preview,
        intent_resolver=intent,
        recovery_declaration=tool_declaration("update_configuration"),
    )
