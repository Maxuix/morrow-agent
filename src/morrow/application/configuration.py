"""Typed configuration commands and the standard configuration tool factory."""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from morrow.application.legacy_configuration import validate_legacy_configuration_fields
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
from morrow.core.preference_models import PreferenceLifecycleOperation, PreferenceOperation
from morrow.runtime.policy import ToolApproval, ToolExecutionPolicy
from morrow.runtime.tools import RegisteredTool, ToolErrorCode, ToolExecutionError, make_tool

ConfigurationScope = Literal["session", "workspace", "global"]
ConfigurationTarget = Literal["preferences", "profile"]
ConfigurationOperation = Literal["set", "unset", "append", "remove", "reset"]


def _validate_profile_fields(model: BaseModel, *, validate_values: bool = True) -> None:
    if model.scope != "workspace" or model.target != "profile":
        raise ValueError("update_configuration 只支持 workspace Profile")
    validate_legacy_configuration_fields(model, validate_values=validate_values)


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
        # The application service owns value/type validation so legacy callers can receive the
        # stable ConfigurationValidationError instead of a construction-time Pydantic error.
        validate_legacy_configuration_fields(self, validate_values=False)
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
    preference_operations: tuple[PreferenceOperation, ...] = ()
    preference_lifecycle_operations: tuple[PreferenceLifecycleOperation, ...] = ()
    preference_command_id: str | None = None
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
        recovery_declaration=tool_declaration("update_configuration"),
    )
