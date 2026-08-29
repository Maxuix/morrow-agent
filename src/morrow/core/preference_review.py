"""Bounded contracts shared by the Preference Review context and model adapter."""

from __future__ import annotations

import hashlib
from typing import Literal

from pydantic import Field, field_validator, model_validator

from morrow.core.domain import (
    CONVERSATION_RECORD_ID_PREFIX,
    CONVERSATION_RECORD_MAX_BYTES,
    TURN_ID_PREFIX,
    canonical_json_bytes,
    validate_prefixed_id,
)
from morrow.core.models import ModelErrorCode, ProtocolModel
from morrow.core.preference_documents import PreferenceReviewSnapshot
from morrow.core.preference_models import (
    PREFERENCE_EVIDENCE_ID_PREFIX,
    PREFERENCE_MAX_OPERATIONS,
    PREFERENCE_REVIEW_JOB_ID_PREFIX,
    PreferenceOperation,
    PreferenceScope,
)

# The request may contain the complete durable user message and the full frozen snapshot.  The
# adapter applies its own configured request budget; this model only prevents an unbounded context.
PREFERENCE_REVIEW_CONTEXT_MAX_BYTES = 256 * 1024
PREFERENCE_REVIEW_MAX_RECENT_MESSAGES = 8
PREFERENCE_REVIEW_RECENT_MESSAGE_MAX_CHARS = 2_048
PREFERENCE_REVIEW_REQUEST_MAX_CHARS = 256 * 1024
PREFERENCE_REVIEW_RESPONSE_MAX_BYTES = 32 * 1024


class PreferenceReviewContextError(ValueError):
    """Sanitized, deterministic failure while constructing a delayed Review context."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class PreferenceReviewerError(RuntimeError):
    """Sanitized failure raised by the no-tool Preference Reviewer adapter."""

    def __init__(self, code: ModelErrorCode, message: str, *, category: str) -> None:
        super().__init__(message)
        self.code = code
        self.category = category


class PreferenceDialogueMessage(ProtocolModel):
    """One bounded dialogue reference; only the current user message is authoritative."""

    actor: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=PREFERENCE_REVIEW_RECENT_MESSAGE_MAX_CHARS)
    turn_id: str | None = None
    record_id: str | None = None

    _valid_turn = field_validator("turn_id")(
        lambda value: None if value is None else validate_prefixed_id(value, TURN_ID_PREFIX)
    )
    _valid_record = field_validator("record_id")(
        lambda value: (
            None if value is None else validate_prefixed_id(value, CONVERSATION_RECORD_ID_PREFIX)
        )
    )

    @property
    def role(self) -> str:
        return self.actor


class PreferenceReviewContext(ProtocolModel):
    """The only model-visible input to a Preference Review attempt."""

    workspace_id: str
    job_id: str
    turn_id: str
    current_user_record_id: str
    current_user_message: str
    evidence_id: str
    recent_dialogue: tuple[PreferenceDialogueMessage, ...] = Field(
        default=(), max_length=PREFERENCE_REVIEW_MAX_RECENT_MESSAGES
    )
    active_snapshot: PreferenceReviewSnapshot
    source_global_revision: int = Field(ge=0)
    source_workspace_revision: int = Field(ge=0)
    operation_budget: int = Field(
        default=PREFERENCE_MAX_OPERATIONS, ge=0, le=PREFERENCE_MAX_OPERATIONS
    )
    rendered_char_budget: int = Field(
        default=PREFERENCE_REVIEW_CONTEXT_MAX_BYTES,
        ge=256,
        le=PREFERENCE_REVIEW_CONTEXT_MAX_BYTES,
    )

    _valid_workspace = field_validator("workspace_id")(
        lambda value: validate_prefixed_id(value, "ws")
    )
    _valid_job = field_validator("job_id")(
        lambda value: validate_prefixed_id(value, PREFERENCE_REVIEW_JOB_ID_PREFIX)
    )
    _valid_turn = field_validator("turn_id")(
        lambda value: validate_prefixed_id(value, TURN_ID_PREFIX)
    )
    _valid_record = field_validator("current_user_record_id")(
        lambda value: validate_prefixed_id(value, CONVERSATION_RECORD_ID_PREFIX)
    )
    _valid_evidence = field_validator("evidence_id")(
        lambda value: validate_prefixed_id(value, PREFERENCE_EVIDENCE_ID_PREFIX)
    )

    @field_validator("current_user_message")
    @classmethod
    def preserve_complete_message(cls, value: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("current user message must not be empty")
        if len(value.encode("utf-8")) > CONVERSATION_RECORD_MAX_BYTES:
            raise ValueError("current user message exceeds the durable message limit")
        return value

    @model_validator(mode="after")
    def bounded_and_scoped(self) -> PreferenceReviewContext:
        if self.active_snapshot.global_document_revision != self.source_global_revision:
            raise ValueError("Preference Review global snapshot revision is inconsistent")
        if self.active_snapshot.workspace_document_revision != self.source_workspace_revision:
            raise ValueError("Preference Review workspace snapshot revision is inconsistent")
        encoded = canonical_json_bytes(self.model_dump(mode="json"))
        if len(encoded) > self.rendered_char_budget:
            raise ValueError("Preference Review context exceeds its rendered budget")
        return self

    @property
    def digest(self) -> str:
        """Stable context identity without storing the raw context as a second artifact."""

        return hashlib.sha256(canonical_json_bytes(self.model_dump(mode="json"))).hexdigest()

    @property
    def current_user_content(self) -> str:
        return self.current_user_message

    @property
    def evidence_ids(self) -> tuple[str, ...]:
        return (self.evidence_id,)

    @property
    def snapshot(self) -> PreferenceReviewSnapshot:
        return self.active_snapshot


class PreferenceReviewOutput(ProtocolModel):
    """Minimal structured response accepted from the Reviewer."""

    operations: tuple[PreferenceOperation, ...] = Field(
        default=(), max_length=PREFERENCE_MAX_OPERATIONS
    )

    @model_validator(mode="after")
    def strict_v2_operations(self) -> PreferenceReviewOutput:
        for operation in self.operations:
            if operation.scope is PreferenceScope.SESSION:
                raise ValueError("session Preference scope is not learned")
            if len(operation.evidence_ids) != 1:
                raise ValueError("each learned Preference operation requires one Evidence ID")
        return self


__all__ = [
    "PREFERENCE_REVIEW_CONTEXT_MAX_BYTES",
    "PREFERENCE_REVIEW_MAX_RECENT_MESSAGES",
    "PREFERENCE_REVIEW_RECENT_MESSAGE_MAX_CHARS",
    "PREFERENCE_REVIEW_REQUEST_MAX_CHARS",
    "PREFERENCE_REVIEW_RESPONSE_MAX_BYTES",
    "PreferenceDialogueMessage",
    "PreferenceReviewContext",
    "PreferenceReviewContextError",
    "PreferenceReviewOutput",
    "PreferenceReviewerError",
]
