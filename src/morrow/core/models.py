"""Domain models used by all application layers.

This module deliberately has no dependency on CLI, rendering, YAML, SDK, or OS
integration code.  The models are the boundary at which untrusted provider and
state data becomes typed application data.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

CURRENT_SCHEMA_VERSION = 1
WORKSPACE_DOCUMENT_SCHEMA_VERSION = 2


def utc_now() -> datetime:
    return datetime.now(UTC)


def normalize_text(value: str) -> str:
    return " ".join(value.split()).casefold()


class MorrowModel(BaseModel):
    model_config = ConfigDict(extra="ignore", validate_assignment=True)


class Message(MorrowModel):
    role: Literal["user", "assistant", "system"]
    content: str

    @field_validator("content")
    @classmethod
    def non_empty_content(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("message content must not be empty")
        return value


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


class FinishReason(StrEnum):
    STOP = "stop"
    CANCELLED = "cancelled"
    ERROR = "error"


class ModelEvent(MorrowModel):
    kind: Literal["text_delta", "completed", "error"]
    text: str | None = None
    finish_reason: FinishReason | None = None
    error_code: ModelErrorCode | None = None
    error_message: str | None = None


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


class Decision(MorrowModel):
    decision: str
    reason: str | None = None

    @field_validator("decision")
    @classmethod
    def decision_required(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("decision must not be empty")
        return value.strip()


class Handoff(MorrowModel):
    current_goal: str
    progress: list[str] = Field(default_factory=list)
    decisions: list[Decision] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    next_actions: list[str] = Field(default_factory=list)
    recovery_note: str | None = None

    @field_validator("current_goal")
    @classmethod
    def current_goal_required(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("handoff current_goal must not be empty")
        return value

    @model_validator(mode="after")
    def unique_decisions(self) -> Handoff:
        normalized = [normalize_text(item.decision) for item in self.decisions]
        if len(normalized) != len(set(normalized)):
            raise ValueError("handoff decisions must be unique after normalization")
        return self


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


class HandoffDocument(WorkspaceDocument):
    handoff: Handoff | None = None

    @model_validator(mode="after")
    def payload_matches_state(self) -> HandoffDocument:
        if (self.state == "present") != (self.handoff is not None):
            raise ValueError("Handoff payload must match envelope state")
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
    target: Literal["preferences", "profile", "handoff"]
    operations: list[ConfigPatchOperation] = Field(min_length=1)
    reason: str | None = None


class ConfigExtractionResult(MorrowModel):
    result: Literal["no_change", "clarification_required", "config_patch"]
    question: str | None = Field(default=None, max_length=300)
    patch: ConfigPatch | None = None

    @model_validator(mode="after")
    def fields_match_result(self) -> ConfigExtractionResult:
        if self.result == "config_patch":
            if self.patch is None or self.question is not None:
                raise ValueError("config_patch requires only a valid patch")
        elif self.result == "clarification_required":
            if self.patch is not None or self.question is None or not self.question.strip():
                raise ValueError("clarification_required requires only one bounded question")
        elif self.patch is not None or self.question is not None:
            raise ValueError("no_change carries neither question nor patch")
        return self


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
