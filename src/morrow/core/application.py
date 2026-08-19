"""Typed client boundary for Stage 4 application commands and observations.

Application events are deliberately separate from public runtime ``AgentEvent``
objects and from ConversationLog positions.  They describe committed business
state transitions and are safe to replay as bounded, sanitized facts.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import Field, field_validator, model_validator

from morrow.core.domain import (
    COMMAND_ID_PREFIX,
    DIGEST_PATTERN,
    SESSION_ID_PREFIX,
    WORKSPACE_ID_PREFIX,
    canonical_json_bytes,
    refuse_secret_material,
    require_payload_budget,
    validate_prefixed_id,
)
from morrow.core.models import ProtocolModel, utc_now

APPLICATION_EVENT_ID_PREFIX = "evt"
APPLICATION_EVENT_SCHEMA_VERSION = 1
APPLICATION_EVENT_MAX_BYTES = 32 * 1024
_EVENT_TOKEN_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,127}$")


class ApplicationErrorCode(StrEnum):
    INVALID = "invalid"
    NOT_FOUND = "not_found"
    CONFLICT = "conflict"
    STALE = "stale"
    CROSS_WORKSPACE = "cross_workspace"
    UNAVAILABLE = "unavailable"
    BUSY = "busy"
    NEEDS_RECOVERY = "needs_recovery"
    QUARANTINED = "quarantined"
    READ_ONLY = "read_only"


class ApplicationError(RuntimeError):
    """Stable error exposed by the command/query boundary."""

    def __init__(self, code: ApplicationErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class ApplicationCommandDisposition(StrEnum):
    ACCEPTED = "accepted"
    REPLAY = "replay"
    CONFLICT = "conflict"


class ApplicationEvent(ProtocolModel):
    """One committed, sanitized business event in its own cursor namespace."""

    event_id: str
    workspace_id: str
    cursor: int = Field(default=0, ge=0)
    schema_version: int = Field(default=APPLICATION_EVENT_SCHEMA_VERSION, ge=1)
    event_type: str = Field(min_length=1, max_length=128)
    aggregate_kind: str = Field(min_length=1, max_length=64)
    aggregate_id: str = Field(min_length=1, max_length=128)
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)

    @field_validator("event_id")
    @classmethod
    def valid_event_id(cls, value: str) -> str:
        return validate_prefixed_id(value, APPLICATION_EVENT_ID_PREFIX)

    @field_validator("workspace_id")
    @classmethod
    def valid_workspace_id(cls, value: str) -> str:
        return validate_prefixed_id(value, WORKSPACE_ID_PREFIX)

    @field_validator("event_type", "aggregate_kind", "aggregate_id")
    @classmethod
    def clean_token(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned or not _EVENT_TOKEN_PATTERN.match(cleaned):
            raise ValueError("application event token is invalid")
        return cleaned

    @model_validator(mode="after")
    def enforce_budget_and_redaction(self) -> ApplicationEvent:
        payload = canonical_json_bytes(self.model_dump(mode="json"))
        require_payload_budget(payload, APPLICATION_EVENT_MAX_BYTES, label="application event")
        refuse_secret_material(payload, label="application event")
        return self


class ApplicationCommandReceipt(ProtocolModel):
    """Durable idempotency receipt for commands owned by the application API."""

    command_id: str
    workspace_id: str
    session_id: str | None = None
    operation: str = Field(min_length=1, max_length=128)
    request_digest: str
    disposition: ApplicationCommandDisposition = ApplicationCommandDisposition.ACCEPTED
    result_kind: str | None = None
    result_id: str | None = None
    event_cursor: int | None = Field(default=None, ge=1)
    row_version: int | None = Field(default=None, ge=1)
    created_at: datetime = Field(default_factory=utc_now)

    @field_validator("command_id")
    @classmethod
    def valid_command_id(cls, value: str) -> str:
        return validate_prefixed_id(value, COMMAND_ID_PREFIX)

    @field_validator("workspace_id")
    @classmethod
    def valid_workspace_id(cls, value: str) -> str:
        return validate_prefixed_id(value, WORKSPACE_ID_PREFIX)

    @field_validator("session_id")
    @classmethod
    def valid_session_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return validate_prefixed_id(value, SESSION_ID_PREFIX)

    @field_validator("request_digest")
    @classmethod
    def valid_digest(cls, value: str) -> str:
        if not DIGEST_PATTERN.match(value):
            raise ValueError("request digest must be a SHA-256 hex digest")
        return value


@dataclass(frozen=True)
class QueryPage[T]:
    """Bounded cursor page shared by all application queries."""

    items: tuple[T, ...]
    next_cursor: str | None = None


@dataclass(frozen=True)
class ApplicationCommandResult[T]:
    value: T
    receipt: ApplicationCommandReceipt | None = None
