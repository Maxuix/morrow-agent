"""Typed configuration commands and the standard configuration tool factory."""

from __future__ import annotations

import math
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
from morrow.core.models import StatePresence, ToolEffect
from morrow.runtime.policy import ToolApproval, ToolExecutionPolicy
from morrow.runtime.tools import RegisteredTool, ToolErrorCode, ToolExecutionError, make_tool

ConfigurationScope = Literal["session", "workspace", "global"]
ConfigurationTarget = Literal["preferences", "profile"]
ConfigurationOperation = Literal["set", "unset", "append", "remove", "reset"]

PREFERENCE_PATHS = frozenset({"language", "response_detail", "instructions"})
PROFILE_PATHS = frozenset({"name", "summary", "goals", "tech_stack", "constraints", "conventions"})
ALLOWED_PATHS: dict[tuple[str, str], frozenset[str]] = {
    ("session", "preferences"): PREFERENCE_PATHS,
    ("workspace", "preferences"): PREFERENCE_PATHS,
    ("global", "preferences"): PREFERENCE_PATHS,
    ("workspace", "profile"): PROFILE_PATHS,
}
LIST_PATHS = frozenset({"instructions", "goals", "tech_stack", "constraints", "conventions"})


def _is_json_value(value: object) -> bool:
    if value is None or isinstance(value, (str, bool, int)):
        return True
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, list):
        return all(_is_json_value(item) for item in value)
    if isinstance(value, dict):
        return all(isinstance(key, str) and _is_json_value(item) for key, item in value.items())
    return False


def _validate_configuration_fields(model: BaseModel, *, validate_values: bool = True) -> None:
    scope = model.scope
    target = model.target
    operation = model.operation
    path = model.path
    fields = model.model_fields_set
    allowed = ALLOWED_PATHS.get((scope, target))
    if allowed is None:
        raise ValueError("不允许修改此作用域或目标")
    if operation == "reset":
        if path is not None or model.value is not None:
            raise ValueError("reset 不接受 path 或 value")
        return
    if path is None or not path.strip():
        raise ValueError("此操作需要 path")
    if path not in allowed:
        raise ValueError(f"不允许修改字段: {path}")
    if target == "profile" and path == "name" and operation == "unset":
        raise ValueError("Profile 的 name 不能取消设置")
    if operation == "unset":
        if model.value is not None:
            raise ValueError("unset 不接受 value")
        if path in LIST_PATHS:
            raise ValueError("列表字段只能使用 append 或 remove")
        return
    if "value" not in fields:
        raise ValueError(f"{operation} 操作需要 value")
    if not _is_json_value(model.value):
        raise ValueError("value 必须是有限的 JSON 值")
    is_list = path in LIST_PATHS
    if validate_values and not is_list and path in {"language", "name", "summary"}:
        if not isinstance(model.value, str) or not model.value.strip():
            raise ValueError(f"{path} 必须是非空字符串")
        maximum = 128 if path == "language" else 2_048
        if len(model.value) > maximum:
            raise ValueError(f"{path} 超出长度限制")
    if (
        validate_values
        and is_list
        and (not isinstance(model.value, str) or not model.value.strip())
    ):
        raise ValueError(f"{path} 的值必须是非空字符串")
    if validate_values and is_list and isinstance(model.value, str) and len(model.value) > 512:
        raise ValueError(f"{path} 的值超出长度限制")
    if operation in {"append", "remove"} and not is_list:
        raise ValueError("标量字段只能使用 set 或 unset")
    if operation == "set" and is_list:
        raise ValueError("列表字段只能使用 append 或 remove")


class UpdateConfigurationArguments(BaseModel):
    """Flat, strict Provider arguments for one configuration operation."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    scope: ConfigurationScope
    target: ConfigurationTarget
    operation: ConfigurationOperation
    path: str | None = None
    value: Any = None

    @model_validator(mode="after")
    def valid_operation(self) -> UpdateConfigurationArguments:
        _validate_configuration_fields(self)
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
        # The application service owns value/type validation so legacy callers can receive the
        # stable ConfigurationValidationError instead of a construction-time Pydantic error.
        _validate_configuration_fields(self, validate_values=False)
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
    "仅当用户明确要求把配置保存、写入、记住或更新到指定作用域时调用此工具；普通对话、"
    "本次回答风格、问题、解释、示例、假设、引用和否定句不应调用。一次性回复偏好不持久化。"
    "scope=session 表示本次会话 Preferences，scope=workspace 表示当前工作空间的 Preferences 或 Profile，"
    "scope=global 表示所有工作空间共享的 Preferences。缺少作用域、目标、操作或必需 path/value 时先澄清，"
    "不要猜测。Provider、凭据、活动模型、workspace identity、权限、安全策略和其他敏感目标不在此工具范围内。"
)


def _configuration_tool_error(error: Exception) -> ToolExecutionError:
    from morrow.services.preferences import (
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
        handler=handler,
        execution_policy=ToolExecutionPolicy(
            effect=ToolEffect.PERSISTENT_WRITE,
            approval=ToolApproval.REQUIRED,
        ),
        approval_preview=preview,
        intent_resolver=intent,
    )
