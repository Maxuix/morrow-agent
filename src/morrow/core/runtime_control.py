"""Bounded durable steering and follow-up queue contracts."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import Field, field_validator

from morrow.core.models import MorrowModel, utc_now

RUNTIME_CONTROL_MAX_PENDING = 32
RUNTIME_CONTROL_TEXT_MAX_CHARS = 4096


class RuntimeControlKind(StrEnum):
    STEER = "steer"
    FOLLOW_UP = "follow_up"


class RuntimeControlStatus(StrEnum):
    PENDING = "pending"
    CONSUMED = "consumed"
    SUPERSEDED = "superseded"


class RuntimeControlErrorCode(StrEnum):
    IDLE = "idle"
    INVALID = "invalid"
    QUEUE_FULL = "queue_full"
    NOT_FOUND = "not_found"
    CONFLICT = "conflict"


class RuntimeControlError(ValueError):
    def __init__(self, code: RuntimeControlErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code


class RuntimeControlEntry(MorrowModel):
    workspace_id: str = Field(min_length=1, max_length=128)
    session_id: str = Field(min_length=1, max_length=128)
    position: int = Field(ge=1)
    kind: RuntimeControlKind
    client_message_id: str = Field(min_length=1, max_length=128)
    text: str = Field(min_length=1, max_length=RUNTIME_CONTROL_TEXT_MAX_CHARS)
    status: RuntimeControlStatus = RuntimeControlStatus.PENDING
    created_at: datetime = Field(default_factory=utc_now)
    consumed_at: datetime | None = None

    @field_validator("text")
    @classmethod
    def text_must_have_visible_content(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("runtime control text must not be blank")
        return value


__all__ = [
    "RUNTIME_CONTROL_MAX_PENDING",
    "RUNTIME_CONTROL_TEXT_MAX_CHARS",
    "RuntimeControlEntry",
    "RuntimeControlError",
    "RuntimeControlErrorCode",
    "RuntimeControlKind",
    "RuntimeControlStatus",
]
