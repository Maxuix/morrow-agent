"""Domain models used by all application layers.

This module deliberately has no dependency on CLI, rendering, YAML, SDK, or OS
integration code.  The models are the boundary at which untrusted provider and
state data becomes typed application data.
"""

from __future__ import annotations

import math
import re
from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from morrow.core.runtime_policy import RuntimePolicyOverrides
from morrow.core.state_schema import (
    GLOBAL_CONFIG_LEGACY_SCHEMA_VERSION,
    WORKSPACE_INDEX_SCHEMA_VERSION,
    WORKSPACE_PROFILE_SCHEMA_VERSION,
)

CURRENT_SCHEMA_VERSION = 1
WORKSPACE_DOCUMENT_SCHEMA_VERSION = WORKSPACE_PROFILE_SCHEMA_VERSION

TOOL_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_COST_SOURCE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")
_SECRET_TOKEN_PATTERN = re.compile(r"(?<![A-Za-z0-9])sk-[A-Za-z0-9_-]{20,}", re.IGNORECASE)


def utc_now() -> datetime:
    return datetime.now(UTC)


class MorrowModel(BaseModel):
    model_config = ConfigDict(extra="ignore", validate_assignment=True)


class ProtocolModel(BaseModel):
    """Wire protocol objects: immutable, extras rejected, ordered tuples."""

    model_config = ConfigDict(extra="forbid", frozen=True)


InputModality = Literal["text", "image", "audio", "document"]


def _require_non_empty(value: str) -> str:
    if not value.strip():
        raise ValueError("value must not be empty")
    return value


def _require_tool_name(value: str) -> str:
    if not TOOL_NAME_PATTERN.match(value):
        raise ValueError("tool name must match [A-Za-z0-9_-]{1,64}")
    return value


class SystemMessage(ProtocolModel):
    role: Literal["system"] = "system"
    content: str

    @field_validator("content")
    @classmethod
    def non_empty_content(cls, value: str) -> str:
        return _require_non_empty(value)


class UserMessage(ProtocolModel):
    role: Literal["user"] = "user"
    content: str

    @field_validator("content")
    @classmethod
    def non_empty_content(cls, value: str) -> str:
        return _require_non_empty(value)


class FunctionToolCall(ProtocolModel):
    id: str
    name: str
    arguments: str

    @field_validator("id")
    @classmethod
    def non_empty_id(cls, value: str) -> str:
        return _require_non_empty(value)

    @field_validator("name")
    @classmethod
    def valid_tool_name(cls, value: str) -> str:
        return _require_tool_name(value)


class AssistantMessage(ProtocolModel):
    role: Literal["assistant"] = "assistant"
    content: str | None = None
    tool_calls: tuple[FunctionToolCall, ...] = ()

    @field_validator("content")
    @classmethod
    def non_empty_content(cls, value: str | None) -> str | None:
        if value is not None:
            return _require_non_empty(value)
        return value

    @model_validator(mode="after")
    def content_or_calls_required(self) -> AssistantMessage:
        if self.content is None and not self.tool_calls:
            raise ValueError("assistant message requires content or at least one tool call")
        return self

    @model_validator(mode="after")
    def unique_call_ids(self) -> AssistantMessage:
        ids = [call.id for call in self.tool_calls]
        if len(ids) != len(set(ids)):
            raise ValueError("tool call ids must be unique within one assistant message")
        return self


class ToolMessage(ProtocolModel):
    role: Literal["tool"] = "tool"
    tool_call_id: str
    content: str

    @field_validator("tool_call_id")
    @classmethod
    def non_empty_call_id(cls, value: str) -> str:
        return _require_non_empty(value)

    @field_validator("content")
    @classmethod
    def non_empty_content(cls, value: str) -> str:
        return _require_non_empty(value)


Message = Annotated[
    SystemMessage | UserMessage | AssistantMessage | ToolMessage,
    Field(discriminator="role"),
]


class ToolFunction(ProtocolModel):
    name: str
    description: str
    parameters: dict[str, Any]

    @field_validator("name")
    @classmethod
    def valid_tool_name(cls, value: str) -> str:
        return _require_tool_name(value)

    @field_validator("description")
    @classmethod
    def non_empty_description(cls, value: str) -> str:
        return _require_non_empty(value)


class ToolEffect(StrEnum):
    """Local side-effect classification; never serialized to a Provider."""

    NONE = "none"
    SESSION_WRITE = "session_write"
    PERSISTENT_WRITE = "persistent_write"


class ToolApprovalRequest(ProtocolModel):
    """Sanitized local context shown to an approval adapter."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    call_id: str
    effect: ToolEffect
    preview: tuple[str, ...] = ()
    policy_verdict: Literal["require_approval"] = "require_approval"
    reason_codes: tuple[str, ...] = ()
    approval_id: str | None = None

    @field_validator("call_id")
    @classmethod
    def non_empty_call_id(cls, value: str) -> str:
        return _require_non_empty(value)


class ToolApprovalDecision(ProtocolModel):
    """Immutable approval result returned by an injected local adapter."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    approved: bool


class ToolDefinition(ProtocolModel):
    type: Literal["function"] = "function"
    function: ToolFunction


class ProviderToolSupport(ProtocolModel):
    """Exact-Model tool protocol facts used to resolve a RunPolicy."""

    tool_protocol: Literal["none", "openai_function"]
    multiple_tool_calls: bool
    safe_request_chars: int | None = Field(default=None, gt=0)


class CostMetadata(ProtocolModel):
    """Sanitized, time-stamped pricing metadata for one capability snapshot."""

    source: str = Field(min_length=1, max_length=128)
    updated_at: datetime = Field(default_factory=utc_now)
    currency: str = Field(default="USD", min_length=3, max_length=3)
    input_cost_per_million: float | None = Field(default=None, ge=0)
    output_cost_per_million: float | None = Field(default=None, ge=0)

    @field_validator("currency")
    @classmethod
    def currency_code(cls, value: str) -> str:
        if not value.isascii() or not value.isupper():
            raise ValueError("currency must be an uppercase ISO-like code")
        return value

    @field_validator("updated_at")
    @classmethod
    def timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("cost metadata updated_at must be timezone-aware")
        return value

    @field_validator("input_cost_per_million", "output_cost_per_million")
    @classmethod
    def finite_cost(cls, value: float | None) -> float | None:
        if value is not None and not math.isfinite(value):
            raise ValueError("cost metadata must be finite")
        return value


class ModelCapabilityOverrides(ProtocolModel):
    """Optional per-model restrictions persisted below one ProviderConfig."""

    streaming_text: bool | None = None
    tool_protocol: Literal["none", "openai_function"] | None = None
    multiple_tool_calls: bool | None = None
    structured_output: bool | None = None
    safe_request_chars: int | None = Field(default=None, gt=0)
    safe_context_chars: int | None = Field(default=None, gt=0)
    context_window_tokens: int | None = Field(default=None, gt=0)
    max_output_tokens: int | None = Field(default=None, gt=0)
    input_types: tuple[InputModality, ...] | None = None
    cost_metadata: CostMetadata | None = None


class RunPolicy(ProtocolModel):
    """Immutable v2 per-AgentRun policy."""

    policy_schema_version: Literal[2] = 2
    tool_timeout_seconds: float = Field(default=300.0, gt=0)
    effective_request_chars: int = Field(default=1, gt=0)
    effective_result_limit: int = Field(default=1, gt=0)
    max_validation_errors: int = Field(default=1, gt=0)
    compaction_enabled: bool = False
    context_window_tokens: int | None = Field(default=None, gt=0)
    reserve_tokens: int = Field(default=16_384, gt=0)
    keep_recent_tokens: int = Field(default=20_000, gt=0)
    retry_enabled: bool = False
    max_retries: int = Field(default=0, ge=0)
    retry_base_delay_seconds: float = Field(default=2.0, gt=0)
    max_provider_retry_delay_seconds: float = Field(default=60.0, gt=0)
    truncation_max_bytes: int = Field(default=50 * 1024, gt=0)
    truncation_max_lines: int = Field(default=2_000, gt=0)
    grep_max_line_chars: int = Field(default=500, gt=0)
    host_stop_source: Literal["none", "provided"] = "none"
    provider_tool_support: ProviderToolSupport

    @model_validator(mode="after")
    def valid_accounting(self) -> RunPolicy:
        if (
            self.context_window_tokens is not None
            and self.reserve_tokens >= self.context_window_tokens
        ):
            raise ValueError("reserve_tokens must be below the context window")
        if self.max_provider_retry_delay_seconds < self.retry_base_delay_seconds:
            raise ValueError("provider retry cap must cover the base retry delay")
        return self


class ModelRef(MorrowModel):
    provider_id: str
    model_id: str

    def __str__(self) -> str:
        return f"{self.provider_id}/{self.model_id}"


class ModelErrorCode(StrEnum):
    AUTH = "auth"
    NETWORK = "network"
    RATE_LIMIT = "rate_limit"
    TIMEOUT = "timeout"
    INVALID_RESPONSE = "invalid_response"
    CONTEXT_OVERFLOW = "context_overflow"
    INTERNAL = "internal"


class UsageAvailability(StrEnum):
    """Whether a Provider reported a trustworthy usage or cost fact."""

    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"


class ModelUsage(ProtocolModel):
    """Normalized token usage; absent values are explicit, never fabricated zeros."""

    availability: UsageAvailability = UsageAvailability.UNAVAILABLE
    input_tokens: Annotated[int, Field(strict=True, ge=0)] | None = None
    output_tokens: Annotated[int, Field(strict=True, ge=0)] | None = None
    total_tokens: Annotated[int, Field(strict=True, ge=0)] | None = None

    @model_validator(mode="after")
    def validate_availability(self) -> ModelUsage:
        values = (self.input_tokens, self.output_tokens, self.total_tokens)
        if self.availability is UsageAvailability.UNAVAILABLE:
            if any(value is not None for value in values):
                raise ValueError("unavailable usage must not contain token counts")
            return self
        if all(value is None for value in values):
            raise ValueError("available usage must contain at least one token count")
        if self.input_tokens is not None and self.output_tokens is not None:
            expected = self.input_tokens + self.output_tokens
            if self.total_tokens is not None and self.total_tokens != expected:
                raise ValueError("usage total_tokens must equal input_tokens + output_tokens")
        return self

    @classmethod
    def unavailable(cls) -> ModelUsage:
        return cls()


class ModelCost(ProtocolModel):
    """Normalized cost fact with an explicit source and availability marker."""

    availability: UsageAvailability = UsageAvailability.UNAVAILABLE
    amount_minor: Annotated[int, Field(strict=True, ge=0)] | None = None
    currency: Annotated[str, Field(strict=True, min_length=3, max_length=3)] | None = None
    source: Annotated[str, Field(strict=True, min_length=1, max_length=128)] | None = None

    @field_validator("currency")
    @classmethod
    def valid_currency(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if len(value) != 3 or not value.isascii() or not value.isupper():
            raise ValueError("cost currency must be an uppercase three-letter code")
        return value

    @field_validator("source")
    @classmethod
    def valid_source(cls, value: str | None) -> str | None:
        if value is None:
            return None
        lowered = value.casefold()
        if (
            not _COST_SOURCE_PATTERN.fullmatch(value)
            or any(
                needle in lowered
                for needle in ("api_key", "authorization", "password", "credential")
            )
            or _SECRET_TOKEN_PATTERN.search(value) is not None
        ):
            raise ValueError("cost source must be a safe provider label")
        return value

    @model_validator(mode="after")
    def validate_availability(self) -> ModelCost:
        values = (self.amount_minor, self.currency, self.source)
        if self.availability is UsageAvailability.UNAVAILABLE:
            if any(value is not None for value in values):
                raise ValueError("unavailable cost must not contain cost values")
            return self
        if any(value is None for value in values):
            raise ValueError("available cost requires amount_minor, currency and source")
        return self

    @classmethod
    def unavailable(cls) -> ModelCost:
        return cls()


# Compatibility aliases make the contract discoverable without multiplying wire types.
UsageStatus = UsageAvailability
NormalizedUsage = ModelUsage
NormalizedCost = ModelCost


class ModelProviderError(RuntimeError):
    def __init__(
        self,
        code: ModelErrorCode,
        message: str,
        *,
        retry_after_seconds: float | None = None,
        transient_internal: bool = False,
    ) -> None:
        if transient_internal and code is not ModelErrorCode.INTERNAL:
            raise ValueError("transient_internal requires the internal Provider error code")
        super().__init__(message)
        self.code = code
        self.retry_after_seconds = retry_after_seconds
        self.transient_internal = transient_internal


def provider_error_message(code: ModelErrorCode, *, phase: str | None = None) -> str:
    """Return one stable, sanitized user-facing message for a provider failure."""

    if phase == "connect":
        return "连接模型服务超时"
    if phase == "first_token":
        return "等待模型首个响应超时"
    return {
        ModelErrorCode.AUTH: "认证失败，请检查 API Key 或重新配置 Provider",
        ModelErrorCode.NETWORK: "无法连接模型服务，请检查网络或代理设置后重试",
        ModelErrorCode.RATE_LIMIT: "模型服务限流，请稍后重试",
        ModelErrorCode.TIMEOUT: "等待模型响应超时，请稍后重试",
        ModelErrorCode.INVALID_RESPONSE: "模型响应无效，请检查 Provider 地址和模型配置",
        ModelErrorCode.CONTEXT_OVERFLOW: "模型上下文长度超过限制",
        ModelErrorCode.INTERNAL: "模型服务暂时不可用，请稍后重试",
    }[code]


class ModelFinishReason(StrEnum):
    """Normalized internal model finish reasons; vendor values never leak."""

    STOP = "stop"
    TOOL_CALLS = "tool_calls"
    LENGTH = "length"
    CONTENT_FILTER = "content_filter"


class FinishReason(StrEnum):
    STOP = "stop"
    STEERED = "steered"
    CANCELLED = "cancelled"
    ERROR = "error"


class AgentStopCode(StrEnum):
    PROVIDER_AUTH = "provider_auth"
    PROVIDER_NETWORK = "provider_network"
    PROVIDER_RATE_LIMIT = "provider_rate_limit"
    PROVIDER_TIMEOUT = "provider_timeout"
    INVALID_RESPONSE = "invalid_response"
    MODEL_OUTPUT_LIMIT = "model_output_limit"
    CONTENT_FILTERED = "content_filtered"
    CONTEXT_BUDGET = "context_budget"
    MODEL_CALL_LIMIT = "model_call_limit"
    TOOL_CALL_LIMIT = "tool_call_limit"
    RUN_TIMEOUT = "run_timeout"
    LOOP_DETECTED = "loop_detected"
    MISSING_REQUIRED_CHANGE = "missing_required_change"
    VALIDATION_MISSING = "validation_missing"
    VALIDATION_FAILED = "validation_failed"
    UNEXPECTED_WORKSPACE_CHANGE = "unexpected_workspace_change"
    FORBIDDEN_WORKSPACE_CHANGE = "forbidden_workspace_change"
    UNRESOLVED_TOOL = "unresolved_tool"
    KNOWN_FAILURE = "known_failure"
    VERIFIER_FAILED = "verifier_failed"
    COMPLETION_INCONCLUSIVE = "completion_inconclusive"
    INTERNAL = "internal"


class ModelEvent(MorrowModel):
    kind: Literal["text_delta", "completed", "error"]
    text: str | None = None
    finish_reason: ModelFinishReason | None = None
    message: AssistantMessage | None = None
    error_code: ModelErrorCode | None = None
    error_message: str | None = None
    retry_after_seconds: float | None = Field(default=None, ge=0, le=60)
    made_progress: bool = False
    usage: ModelUsage = Field(default_factory=ModelUsage.unavailable)
    cost: ModelCost = Field(default_factory=ModelCost.unavailable)


class Preferences(MorrowModel):
    """Legacy fixed-field projection used only for v1/v2 decode and compatibility state."""

    language: str | None = None
    response_detail: Literal["concise", "balanced", "detailed"] | None = None
    instructions: list[str] = Field(default_factory=list)

    @field_validator("instructions")
    @classmethod
    def clean_instructions(cls, values: list[str]) -> list[str]:
        result: list[str] = []
        for value in values:
            value = " ".join(value.split())
            if value and value not in result:
                result.append(value)
        return result


class Profile(MorrowModel):
    name: str
    summary: str | None = None
    goals: list[str] = Field(default_factory=list)
    tech_stack: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    conventions: list[str] = Field(default_factory=list)


class CredentialRef(MorrowModel):
    """A versioned reference; the secret itself never enters this model."""

    ref: str
    version: int = 1


class ProviderModelConfig(MorrowModel):
    api_model_id: str
    capabilities: ModelCapabilityOverrides | None = None


class LastTestResult(MorrowModel):
    ok: bool
    tested_at: datetime = Field(default_factory=utc_now)
    error_code: ModelErrorCode | None = None
    message: str | None = None


class ProviderConfig(MorrowModel):
    adapter: str
    base_url: str
    credential_ref: CredentialRef | None = None
    models: dict[str, ProviderModelConfig] = Field(default_factory=dict)
    last_test: LastTestResult | None = None


class GlobalConfig(MorrowModel):
    schema_version: int = GLOBAL_CONFIG_LEGACY_SCHEMA_VERSION
    revision: int = 0
    updated_at: datetime = Field(default_factory=utc_now)
    preferences: Preferences = Field(default_factory=Preferences)
    providers: dict[str, ProviderConfig] = Field(default_factory=dict)
    active_model: ModelRef | None = None
    runtime_policy: RuntimePolicyOverrides | None = None

    @model_validator(mode="after")
    def active_model_is_registered(self) -> GlobalConfig:
        if self.active_model:
            provider = self.providers.get(self.active_model.provider_id)
            if not provider or self.active_model.model_id not in provider.models:
                raise ValueError("active_model must refer to a registered provider model")
        return self


class WorkspaceIndexEntry(MorrowModel):
    workspace_id: str
    path: str
    display_name: str
    git_root: str | None = None


class WorkspaceIndex(MorrowModel):
    schema_version: int = WORKSPACE_INDEX_SCHEMA_VERSION
    revision: int = 0
    updated_at: datetime = Field(default_factory=utc_now)
    workspaces: dict[str, WorkspaceIndexEntry] = Field(default_factory=dict)


class StatePresence(StrEnum):
    MISSING = "missing"
    CLEARED = "cleared"
    PRESENT = "present"


class WorkspaceDocument(MorrowModel):
    schema_version: Literal[WORKSPACE_PROFILE_SCHEMA_VERSION] = WORKSPACE_PROFILE_SCHEMA_VERSION
    revision: int = Field(default=0, ge=0)
    updated_at: datetime = Field(default_factory=utc_now)
    state: Literal["present", "cleared"] = "present"

    @field_validator("updated_at")
    @classmethod
    def updated_at_must_be_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("workspace document updated_at must be timezone-aware")
        return value


class ProjectPreferencesDocument(WorkspaceDocument):
    preferences: Preferences | None = None

    @model_validator(mode="after")
    def payload_matches_state(self) -> ProjectPreferencesDocument:
        if (self.state == "present") != (self.preferences is not None):
            raise ValueError("workspace Preferences payload must match envelope state")
        return self


class ProfileDocument(WorkspaceDocument):
    profile: Profile | None = None

    @model_validator(mode="after")
    def payload_matches_state(self) -> ProfileDocument:
        if (self.state == "present") != (self.profile is not None):
            raise ValueError("Profile payload must match envelope state")
        return self


class WorkspaceIdentity(MorrowModel):
    workspace_id: str
    path: str
    display_name: str
    git_root: str | None = None


class WorkspaceCandidate(MorrowModel):
    path: str
    display_name: str
    git_root: str | None = None
    similar_workspace_ids: list[str] = Field(default_factory=list)


class WorkspaceResolution(MorrowModel):
    status: Literal["existing", "candidate"]
    identity: WorkspaceIdentity | None = None
    candidate: WorkspaceCandidate | None = None


class ConfigPatchOperation(MorrowModel):
    op: Literal["set", "unset", "append", "remove"]
    path: str
    value: Any | None = None


class ConfigPatch(MorrowModel):
    result: Literal["config_patch"] = "config_patch"
    scope: Literal["global", "workspace", "session"]
    target: Literal["preferences", "profile"]
    operations: list[ConfigPatchOperation] = Field(min_length=1)
    reason: str | None = None


class AgentEvent(MorrowModel):
    """Public event envelope. Unknown fields are intentionally ignored."""

    schema_version: int = CURRENT_SCHEMA_VERSION
    type: str
    event_id: str
    session_id: str
    turn_id: str
    sequence: int = Field(ge=1)
    timestamp: datetime = Field(default_factory=utc_now)
    payload: dict[str, Any] = Field(default_factory=dict)


class StateLoadStatus(StrEnum):
    OK = "ok"
    CORRUPT = "corrupt"
    UNSUPPORTED_SCHEMA = "unsupported_schema"


class StateWriteStatus(StrEnum):
    OK = "ok"
    REVISION_CONFLICT = "revision_conflict"
    FAILED = "failed"


class StateLoadResult(MorrowModel):
    status: StateLoadStatus
    presence: StatePresence | None = None
    value: Any | None = None
    revision: int | None = None
    error: str | None = None


class StateWriteResult(MorrowModel):
    status: StateWriteStatus
    value: Any | None = None
    revision: int | None = None
    error: str | None = None


def sanitize_text(value: str, max_length: int = 600) -> str:
    value = re.sub(
        r"(?i)(api[_ -]?key|authorization|token|password)\s*[:=]\s*\S+", r"\1=[已隐藏]", value
    )
    value = re.sub(r"sk-[A-Za-z0-9_-]+", "[已隐藏]", value)
    value = " ".join(value.split())
    return value[:max_length]
