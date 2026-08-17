"""Domain models used by all application layers.

This module deliberately has no dependency on CLI, rendering, YAML, SDK, or OS
integration code.  The models are the boundary at which untrusted provider and
state data becomes typed application data.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

CURRENT_SCHEMA_VERSION = 1
WORKSPACE_DOCUMENT_SCHEMA_VERSION = 2

TOOL_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def utc_now() -> datetime:
    return datetime.now(UTC)


class MorrowModel(BaseModel):
    model_config = ConfigDict(extra="ignore", validate_assignment=True)


class ProtocolModel(BaseModel):
    """Wire protocol objects: immutable, extras rejected, ordered tuples."""

    model_config = ConfigDict(extra="forbid", frozen=True)


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
    """Minimal, sanitized local context shown to an approval adapter."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    call_id: str
    effect: ToolEffect
    preview: tuple[str, ...] = ()

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
    INTERNAL = "internal"


class ModelProviderError(RuntimeError):
    def __init__(self, code: ModelErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code


class ModelFinishReason(StrEnum):
    """Normalized internal model finish reasons; vendor values never leak."""

    STOP = "stop"
    TOOL_CALLS = "tool_calls"
    LENGTH = "length"
    CONTENT_FILTER = "content_filter"


class FinishReason(StrEnum):
    STOP = "stop"
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
    INTERNAL = "internal"


class ModelEvent(MorrowModel):
    kind: Literal["text_delta", "completed", "error"]
    text: str | None = None
    finish_reason: ModelFinishReason | None = None
    message: AssistantMessage | None = None
    error_code: ModelErrorCode | None = None
    error_message: str | None = None
    made_progress: bool = False


class Preferences(MorrowModel):
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
    schema_version: int = CURRENT_SCHEMA_VERSION
    revision: int = 0
    updated_at: datetime = Field(default_factory=utc_now)
    preferences: Preferences = Field(default_factory=Preferences)
    providers: dict[str, ProviderConfig] = Field(default_factory=dict)
    active_model: ModelRef | None = None

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
    schema_version: int = CURRENT_SCHEMA_VERSION
    revision: int = 0
    updated_at: datetime = Field(default_factory=utc_now)
    workspaces: dict[str, WorkspaceIndexEntry] = Field(default_factory=dict)


class StatePresence(StrEnum):
    MISSING = "missing"
    CLEARED = "cleared"
    PRESENT = "present"


class WorkspaceDocument(MorrowModel):
    schema_version: Literal[WORKSPACE_DOCUMENT_SCHEMA_VERSION] = WORKSPACE_DOCUMENT_SCHEMA_VERSION
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
