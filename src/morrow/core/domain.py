"""Durable Session/Task/Turn/AgentRun contracts for Stage 4 Subplan 37.

These types are persistence-facing Core models. They do not write history and do
not depend on sqlite3. ConversationLog remains the message-grammar authority.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from morrow.core.models import (
    ModelRef,
    Preferences,
    Profile,
    ProtocolModel,
    utc_now,
)

WORKSPACE_ID_PREFIX = "ws"
SESSION_ID_PREFIX = "ses"
TASK_RUN_ID_PREFIX = "task"
TURN_ID_PREFIX = "turn"
AGENT_RUN_ID_PREFIX = "arun"
CONVERSATION_RECORD_ID_PREFIX = "rec"
COMMAND_ID_PREFIX = "cmd"

ID_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,127}$")
CLIENT_MESSAGE_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
DIGEST_PATTERN = re.compile(r"^[a-f0-9]{64}$")

CONVERSATION_RECORD_MAX_BYTES = 256 * 1024
AGENT_RUN_SNAPSHOT_MAX_BYTES = 64 * 1024
ERROR_DETAIL_MAX_BYTES = 4 * 1024


class SessionLifecycle(StrEnum):
    ACTIVE = "active"
    ARCHIVED = "archived"
    DELETED = "deleted"


class SessionHealth(StrEnum):
    OK = "ok"
    NEEDS_RECOVERY = "needs_recovery"
    QUARANTINED = "quarantined"
    READ_ONLY = "read_only"


class TaskRunStatus(StrEnum):
    """Subplan 37 persists only an open current TaskRun pointer."""

    OPEN = "open"


class TurnSubmitDisposition(StrEnum):
    ACCEPTED_OPEN = "accepted_open"
    ACCEPTED_CLOSED = "accepted_closed"
    RECOVERY = "recovery"
    CONFLICT = "conflict"


class SequenceNamespace(StrEnum):
    CONVERSATION_POSITION = "conversation_position"
    RUNTIME_EVENT_SEQUENCE = "runtime_event_sequence"
    APPLICATION_EVENT_CURSOR = "application_event_cursor"


def utf8_size(value: str | bytes) -> int:
    if isinstance(value, bytes):
        return len(value)
    return len(value.encode("utf-8"))


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode(
        "utf-8"
    )


def sha256_digest(value: str | bytes) -> str:
    payload = value if isinstance(value, bytes) else value.encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def require_payload_budget(payload: bytes, maximum: int, *, label: str) -> bytes:
    if len(payload) > maximum:
        raise ValueError(f"{label} exceeds the durable payload budget")
    return payload


def validate_prefixed_id(value: str, prefix: str) -> str:
    if not value or not value.strip():
        raise ValueError("identifier must not be empty")
    if not ID_PATTERN.match(value):
        raise ValueError("identifier must be a bounded opaque token")
    expected = f"{prefix}_"
    if not value.startswith(expected):
        raise ValueError("identifier prefix does not match its owner")
    remainder = value[len(expected) :]
    if not remainder:
        raise ValueError("identifier must include an opaque suffix")
    return value


class ConversationPosition(ProtocolModel):
    namespace: Literal[SequenceNamespace.CONVERSATION_POSITION] = (
        SequenceNamespace.CONVERSATION_POSITION
    )
    session_id: str
    value: int = Field(ge=0)

    @field_validator("session_id")
    @classmethod
    def valid_session_id(cls, value: str) -> str:
        return validate_prefixed_id(value, SESSION_ID_PREFIX)


class RuntimeEventSequence(ProtocolModel):
    namespace: Literal[SequenceNamespace.RUNTIME_EVENT_SEQUENCE] = (
        SequenceNamespace.RUNTIME_EVENT_SEQUENCE
    )
    turn_id: str
    value: int = Field(ge=1)

    @field_validator("turn_id")
    @classmethod
    def valid_turn_id(cls, value: str) -> str:
        return validate_prefixed_id(value, TURN_ID_PREFIX)


class ApplicationEventCursor(ProtocolModel):
    namespace: Literal[SequenceNamespace.APPLICATION_EVENT_CURSOR] = (
        SequenceNamespace.APPLICATION_EVENT_CURSOR
    )
    value: int = Field(ge=0)


class SourceRevisionRef(ProtocolModel):
    kind: Literal["global_config", "workspace_profile", "workspace_preferences"]
    revision: int = Field(ge=0)
    content_sha256: str

    @field_validator("content_sha256")
    @classmethod
    def valid_digest(cls, value: str) -> str:
        if not DIGEST_PATTERN.match(value):
            raise ValueError("source hash must be a SHA-256 hex digest")
        return value


class AgentRunSnapshot(ProtocolModel):
    """Immutable non-secret AgentRun evidence. Not a configuration authority."""

    profile: Profile | None = None
    preferences: Preferences = Field(default_factory=Preferences)
    model: ModelRef
    provider_id: str
    source_revisions: tuple[SourceRevisionRef, ...] = ()
    run_policy_digest: str
    tool_schema_digest: str
    permission_profile_digest: str
    runtime_instance_id: str

    @field_validator("provider_id", "runtime_instance_id")
    @classmethod
    def non_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("snapshot field must not be empty")
        return value

    @field_validator("run_policy_digest", "tool_schema_digest", "permission_profile_digest")
    @classmethod
    def valid_digest(cls, value: str) -> str:
        if not DIGEST_PATTERN.match(value):
            raise ValueError("snapshot digest must be a SHA-256 hex digest")
        return value

    @model_validator(mode="after")
    def enforce_budget_and_redaction(self) -> AgentRunSnapshot:
        dumped = self.model_dump(mode="json")
        payload = canonical_json_bytes(dumped)
        require_payload_budget(payload, AGENT_RUN_SNAPSHOT_MAX_BYTES, label="AgentRun snapshot")
        serialized = payload.decode("utf-8").casefold()
        for needle in ("api_key", "authorization", "password", "credential", "sk-"):
            if needle in serialized:
                raise ValueError("AgentRun snapshot cannot contain secret material")
        return self


class DurableSession(ProtocolModel):
    session_id: str
    workspace_id: str
    lifecycle: SessionLifecycle = SessionLifecycle.ACTIVE
    health: SessionHealth = SessionHealth.OK
    current_task_run_id: str | None = None
    conversation_position: int = Field(default=0, ge=0)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @field_validator("session_id")
    @classmethod
    def valid_session_id(cls, value: str) -> str:
        return validate_prefixed_id(value, SESSION_ID_PREFIX)

    @field_validator("workspace_id")
    @classmethod
    def valid_workspace_id(cls, value: str) -> str:
        return validate_prefixed_id(value, WORKSPACE_ID_PREFIX)

    @field_validator("current_task_run_id")
    @classmethod
    def valid_task_run_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return validate_prefixed_id(value, TASK_RUN_ID_PREFIX)

    @model_validator(mode="after")
    def quarantine_is_health_not_lifecycle(self) -> DurableSession:
        if self.lifecycle is SessionLifecycle.DELETED and self.health is SessionHealth.OK:
            return self
        return self


class DurableTaskRun(ProtocolModel):
    task_run_id: str
    session_id: str
    workspace_id: str
    status: TaskRunStatus = TaskRunStatus.OPEN
    created_at: datetime = Field(default_factory=utc_now)

    @field_validator("task_run_id")
    @classmethod
    def valid_task_run_id(cls, value: str) -> str:
        return validate_prefixed_id(value, TASK_RUN_ID_PREFIX)

    @field_validator("session_id")
    @classmethod
    def valid_session_id(cls, value: str) -> str:
        return validate_prefixed_id(value, SESSION_ID_PREFIX)

    @field_validator("workspace_id")
    @classmethod
    def valid_workspace_id(cls, value: str) -> str:
        return validate_prefixed_id(value, WORKSPACE_ID_PREFIX)


class DurableTurn(ProtocolModel):
    turn_id: str
    session_id: str
    task_run_id: str
    client_message_id: str
    created_at: datetime = Field(default_factory=utc_now)

    @field_validator("turn_id")
    @classmethod
    def valid_turn_id(cls, value: str) -> str:
        return validate_prefixed_id(value, TURN_ID_PREFIX)

    @field_validator("session_id")
    @classmethod
    def valid_session_id(cls, value: str) -> str:
        return validate_prefixed_id(value, SESSION_ID_PREFIX)

    @field_validator("task_run_id")
    @classmethod
    def valid_task_run_id(cls, value: str) -> str:
        return validate_prefixed_id(value, TASK_RUN_ID_PREFIX)

    @field_validator("client_message_id")
    @classmethod
    def valid_client_message_id(cls, value: str) -> str:
        if not CLIENT_MESSAGE_ID_PATTERN.match(value):
            raise ValueError("client_message_id must be a bounded opaque command field")
        return value


class DurableConversationRecord(ProtocolModel):
    record_id: str
    session_id: str
    conversation_position: int = Field(ge=1)
    kind: Literal["message", "terminal"]
    payload: dict[str, Any]

    @field_validator("record_id")
    @classmethod
    def valid_record_id(cls, value: str) -> str:
        return validate_prefixed_id(value, CONVERSATION_RECORD_ID_PREFIX)

    @field_validator("session_id")
    @classmethod
    def valid_session_id(cls, value: str) -> str:
        return validate_prefixed_id(value, SESSION_ID_PREFIX)

    @model_validator(mode="after")
    def enforce_payload_budget(self) -> DurableConversationRecord:
        require_payload_budget(
            canonical_json_bytes(self.payload),
            CONVERSATION_RECORD_MAX_BYTES,
            label="conversation record",
        )
        return self


class DurableAgentRun(ProtocolModel):
    agent_run_id: str
    turn_id: str
    session_id: str
    snapshot: AgentRunSnapshot
    resume_of_agent_run_id: str | None = None
    created_at: datetime = Field(default_factory=utc_now)

    @field_validator("agent_run_id")
    @classmethod
    def valid_agent_run_id(cls, value: str) -> str:
        return validate_prefixed_id(value, AGENT_RUN_ID_PREFIX)

    @field_validator("turn_id")
    @classmethod
    def valid_turn_id(cls, value: str) -> str:
        return validate_prefixed_id(value, TURN_ID_PREFIX)

    @field_validator("session_id")
    @classmethod
    def valid_session_id(cls, value: str) -> str:
        return validate_prefixed_id(value, SESSION_ID_PREFIX)

    @field_validator("resume_of_agent_run_id")
    @classmethod
    def valid_resume_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return validate_prefixed_id(value, AGENT_RUN_ID_PREFIX)


class TurnSubmitReceipt(ProtocolModel):
    session_id: str
    client_message_id: str
    request_digest: str
    disposition: TurnSubmitDisposition
    turn_id: str | None = None
    command_id: str | None = None

    @field_validator("session_id")
    @classmethod
    def valid_session_id(cls, value: str) -> str:
        return validate_prefixed_id(value, SESSION_ID_PREFIX)

    @field_validator("client_message_id")
    @classmethod
    def valid_client_message_id(cls, value: str) -> str:
        if not CLIENT_MESSAGE_ID_PATTERN.match(value):
            raise ValueError("client_message_id must be a bounded opaque command field")
        return value

    @field_validator("request_digest")
    @classmethod
    def valid_digest(cls, value: str) -> str:
        if not DIGEST_PATTERN.match(value):
            raise ValueError("request digest must be a SHA-256 hex digest")
        return value

    @field_validator("turn_id")
    @classmethod
    def valid_turn_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return validate_prefixed_id(value, TURN_ID_PREFIX)

    @field_validator("command_id")
    @classmethod
    def valid_command_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return validate_prefixed_id(value, COMMAND_ID_PREFIX)
