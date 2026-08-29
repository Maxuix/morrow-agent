"""Safe, bounded AgentRun observation contracts.

Observations are deliberately separate from the immutable AgentRun snapshot and
from ConversationLog.  They contain only identifiers, counters, normalized
usage/cost facts and typed state transitions; no prompt, message, tool argument,
tool result, SDK object or traceback is part of this module.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import Field, field_validator, model_validator

from morrow.core.compaction import TokenAccountingBasis
from morrow.core.domain import (
    AGENT_RUN_ID_PREFIX,
    SESSION_ID_PREFIX,
    TASK_RUN_ID_PREFIX,
    TURN_ID_PREFIX,
    WORKSPACE_ID_PREFIX,
    validate_prefixed_id,
)
from morrow.core.models import (
    AgentStopCode,
    FinishReason,
    ModelCost,
    ModelErrorCode,
    ModelFinishReason,
    ModelUsage,
    ProtocolModel,
    UsageAvailability,
    utc_now,
)
from morrow.core.prompt import PromptProfileEvidence

MODEL_REQUEST_ID_PREFIX = "mreq"


class ModelRequestState(StrEnum):
    ADMITTED = "admitted"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ModelRequestPurpose(StrEnum):
    AGENT = "agent"
    OUTCOME_INTENT = "outcome_intent"


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("observation timestamps must be timezone-aware")
    return value


class ToolTerminalCounts(ProtocolModel):
    """Counts grouped by the durable ToolExecution terminal disposition."""

    pending: int = Field(default=0, ge=0)
    denied: int = Field(default=0, ge=0)
    succeeded: int = Field(default=0, ge=0)
    failed: int = Field(default=0, ge=0)
    cancelled: int = Field(default=0, ge=0)
    interrupted: int = Field(default=0, ge=0)
    unknown: int = Field(default=0, ge=0)

    @property
    def terminal_total(self) -> int:
        return sum(
            (
                self.denied,
                self.succeeded,
                self.failed,
                self.cancelled,
                self.interrupted,
                self.unknown,
            )
        )


class ModelRequestObservation(ProtocolModel):
    """Admission and one-time settlement projection for one Provider attempt."""

    model_request_id: str
    workspace_id: str
    agent_run_id: str
    attempt_ordinal: int = Field(ge=1)
    purpose: ModelRequestPurpose = ModelRequestPurpose.AGENT
    prompt_evidence: PromptProfileEvidence | None = None
    state: ModelRequestState = ModelRequestState.ADMITTED
    admitted_at: datetime = Field(default_factory=utc_now)
    settled_at: datetime | None = None
    estimated_request_chars: int = Field(ge=0)
    request_char_budget: int = Field(gt=0)
    cleared_cycle_count: int = Field(default=0, ge=0)
    dropped_turn_count: int = Field(default=0, ge=0)
    dropped_cycle_count: int = Field(default=0, ge=0)
    dropped_record_count: int = Field(default=0, ge=0)
    tool_rounds: int = Field(default=0, ge=0)
    tool_calls: int = Field(default=0, ge=0)
    # These fields are bounded accounting facts, not prompt or tool payloads.  They are optional
    # so observations written before schema v20 remain readable without being backfilled.
    policy_schema_version: int | None = Field(default=None, ge=1, le=2)
    estimated_context_tokens: int | None = Field(default=None, ge=0)
    context_window_tokens: int | None = Field(default=None, gt=0)
    reserve_tokens: int | None = Field(default=None, gt=0)
    keep_recent_tokens: int | None = Field(default=None, gt=0)
    accounting_basis: TokenAccountingBasis | None = None
    compaction_required: bool | None = None
    finish_reason: ModelFinishReason | None = None
    error_code: ModelErrorCode | None = None
    usage: ModelUsage = Field(default_factory=ModelUsage.unavailable)
    cost: ModelCost = Field(default_factory=ModelCost.unavailable)

    @field_validator("model_request_id")
    @classmethod
    def valid_model_request_id(cls, value: str) -> str:
        return validate_prefixed_id(value, MODEL_REQUEST_ID_PREFIX)

    @field_validator("workspace_id")
    @classmethod
    def valid_workspace_id(cls, value: str) -> str:
        return validate_prefixed_id(value, WORKSPACE_ID_PREFIX)

    @field_validator("agent_run_id")
    @classmethod
    def valid_agent_run_id(cls, value: str) -> str:
        return validate_prefixed_id(value, AGENT_RUN_ID_PREFIX)

    @field_validator("admitted_at", "settled_at")
    @classmethod
    def valid_timestamp(cls, value: datetime | None) -> datetime | None:
        return _aware(value) if value is not None else None

    @model_validator(mode="after")
    def state_contract(self) -> ModelRequestObservation:
        if self.settled_at is not None and self.settled_at < self.admitted_at:
            raise ValueError("model request settlement cannot precede admission")
        if self.state is ModelRequestState.ADMITTED:
            if (
                self.settled_at is not None
                or self.finish_reason is not None
                or self.error_code
                or self.usage.availability is not UsageAvailability.UNAVAILABLE
                or self.cost.availability is not UsageAvailability.UNAVAILABLE
            ):
                raise ValueError("admitted model request must not contain settlement facts")
        elif self.settled_at is None:
            raise ValueError("settled model request requires settled_at")
        if self.state is ModelRequestState.COMPLETED:
            if self.finish_reason not in (ModelFinishReason.STOP, ModelFinishReason.TOOL_CALLS):
                raise ValueError("completed model request requires a normal finish reason")
            if self.error_code is not None:
                raise ValueError("completed model request must not contain an error code")
        elif self.state is ModelRequestState.FAILED:
            if self.error_code is None:
                raise ValueError("failed model request requires an error code")
        elif self.state is ModelRequestState.CANCELLED and self.finish_reason is not None:
            raise ValueError("cancelled model request must not contain a finish reason")
        return self


class AgentRunTerminalMetrics(ProtocolModel):
    """Exactly-once terminal aggregate for one durable AgentRun."""

    agent_run_id: str
    workspace_id: str
    session_id: str
    task_run_id: str
    turn_id: str
    finish_reason: FinishReason
    stop_code: AgentStopCode | None = None
    # Internal-only provenance for an unexpected AgentLoop failure. It is diagnostic evidence,
    # never a retry, permission, lifecycle, or recovery input.
    stop_detail: (
        Literal[
            "run_setup",
            "run_control",
            "context_build",
            "model_call",
            "conversation_commit",
            "tool_cycle",
        ]
        | None
    ) = None
    model_attempts: int = Field(default=0, ge=0)
    retry_count: int = Field(default=0, ge=0)
    tool_rounds: int = Field(default=0, ge=0)
    tool_calls: int = Field(default=0, ge=0)
    max_estimated_request_chars: int = Field(default=0, ge=0)
    request_char_budget: int = Field(default=1, gt=0)
    cleared_cycle_count: int = Field(default=0, ge=0)
    dropped_turn_count: int = Field(default=0, ge=0)
    dropped_cycle_count: int = Field(default=0, ge=0)
    dropped_record_count: int = Field(default=0, ge=0)
    usage: ModelUsage = Field(default_factory=ModelUsage.unavailable)
    cost: ModelCost = Field(default_factory=ModelCost.unavailable)
    tool_terminal_counts: ToolTerminalCounts = Field(default_factory=ToolTerminalCounts)
    # The terminal row keeps only bounded aggregates of the v2 accounting decision.  Individual
    # request rows retain the per-attempt current value and decision.
    policy_schema_version: int | None = Field(default=None, ge=1, le=2)
    max_context_tokens: int | None = Field(default=None, ge=0)
    last_context_tokens: int | None = Field(default=None, ge=0)
    context_window_tokens: int | None = Field(default=None, gt=0)
    reserve_tokens: int | None = Field(default=None, gt=0)
    keep_recent_tokens: int | None = Field(default=None, gt=0)
    accounting_basis: TokenAccountingBasis | None = None
    compaction_count: int = Field(default=0, ge=0)
    overflow_recovery_count: int = Field(default=0, ge=0)
    validation_outcome: str = Field(
        default="not_run", pattern=r"^(not_run|passed|failed|timeout|cancelled)$"
    )
    finalized_at: datetime = Field(default_factory=utc_now)

    @field_validator("agent_run_id")
    @classmethod
    def valid_agent_run_id(cls, value: str) -> str:
        return validate_prefixed_id(value, AGENT_RUN_ID_PREFIX)

    @field_validator("workspace_id")
    @classmethod
    def valid_workspace_id(cls, value: str) -> str:
        return validate_prefixed_id(value, WORKSPACE_ID_PREFIX)

    @field_validator("session_id")
    @classmethod
    def valid_session_id(cls, value: str) -> str:
        return validate_prefixed_id(value, SESSION_ID_PREFIX)

    @field_validator("task_run_id")
    @classmethod
    def valid_task_run_id(cls, value: str) -> str:
        return validate_prefixed_id(value, TASK_RUN_ID_PREFIX)

    @field_validator("turn_id")
    @classmethod
    def valid_turn_id(cls, value: str) -> str:
        return validate_prefixed_id(value, TURN_ID_PREFIX)

    @field_validator("finalized_at")
    @classmethod
    def valid_finalized_at(cls, value: datetime) -> datetime:
        return _aware(value)

    @model_validator(mode="after")
    def terminal_contract(self) -> AgentRunTerminalMetrics:
        if self.finish_reason in (
            FinishReason.STOP,
            FinishReason.STEERED,
            FinishReason.CANCELLED,
        ):
            if self.stop_code is not None:
                raise ValueError("successful or cancelled AgentRun must not contain a stop code")
        elif self.stop_code is None:
            raise ValueError("failed AgentRun requires a stop code")
        return self


class AgentRunRetryProgress(ProtocolModel):
    """Mutable, bounded retry counters used to resume one open AgentRun safely."""

    agent_run_id: str
    workspace_id: str
    consecutive_model_retries: int = Field(default=0, ge=0)
    total_retry_count: int = Field(default=0, ge=0)
    summary_retry_count: int = Field(default=0, ge=0)
    updated_at: datetime = Field(default_factory=utc_now)

    @field_validator("agent_run_id")
    @classmethod
    def valid_agent_run_id(cls, value: str) -> str:
        return validate_prefixed_id(value, AGENT_RUN_ID_PREFIX)

    @field_validator("workspace_id")
    @classmethod
    def valid_workspace_id(cls, value: str) -> str:
        return validate_prefixed_id(value, WORKSPACE_ID_PREFIX)

    @field_validator("updated_at")
    @classmethod
    def valid_updated_at(cls, value: datetime) -> datetime:
        return _aware(value)

    @model_validator(mode="after")
    def counter_contract(self) -> AgentRunRetryProgress:
        if self.consecutive_model_retries > self.total_retry_count:
            raise ValueError("consecutive model retries cannot exceed total retries")
        if self.summary_retry_count > self.total_retry_count:
            raise ValueError("summary retries cannot exceed total retries")
        return self


class AgentRunObservation(ProtocolModel):
    """Safe complete inspection of one AgentRun and its observation rows."""

    agent_run_id: str
    workspace_id: str
    session_id: str
    task_run_id: str
    turn_id: str
    resume_of_agent_run_id: str | None = None
    created_at: datetime
    terminal_metrics: AgentRunTerminalMetrics | None = None
    retry_progress: AgentRunRetryProgress | None = None
    requests: tuple[ModelRequestObservation, ...] = ()

    @field_validator("agent_run_id")
    @classmethod
    def valid_agent_run_id(cls, value: str) -> str:
        return validate_prefixed_id(value, AGENT_RUN_ID_PREFIX)

    @field_validator("workspace_id")
    @classmethod
    def valid_workspace_id(cls, value: str) -> str:
        return validate_prefixed_id(value, WORKSPACE_ID_PREFIX)

    @field_validator("session_id")
    @classmethod
    def valid_session_id(cls, value: str) -> str:
        return validate_prefixed_id(value, SESSION_ID_PREFIX)

    @field_validator("task_run_id")
    @classmethod
    def valid_task_run_id(cls, value: str) -> str:
        return validate_prefixed_id(value, TASK_RUN_ID_PREFIX)

    @field_validator("turn_id")
    @classmethod
    def valid_turn_id(cls, value: str) -> str:
        return validate_prefixed_id(value, TURN_ID_PREFIX)

    @field_validator("resume_of_agent_run_id")
    @classmethod
    def valid_resume_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return validate_prefixed_id(value, AGENT_RUN_ID_PREFIX)

    @field_validator("created_at")
    @classmethod
    def valid_created_at(cls, value: datetime) -> datetime:
        return _aware(value)

    @model_validator(mode="after")
    def ordered_requests(self) -> AgentRunObservation:
        ordinals = [request.attempt_ordinal for request in self.requests]
        if ordinals != sorted(ordinals) or len(ordinals) != len(set(ordinals)):
            raise ValueError("AgentRun model requests must be ordered and unique")
        if any(request.agent_run_id != self.agent_run_id for request in self.requests):
            raise ValueError("model request belongs to another AgentRun")
        if self.terminal_metrics is not None and (
            self.terminal_metrics.agent_run_id != self.agent_run_id
            or self.terminal_metrics.workspace_id != self.workspace_id
        ):
            raise ValueError("terminal metrics belong to another AgentRun")
        if self.retry_progress is not None and (
            self.retry_progress.agent_run_id != self.agent_run_id
            or self.retry_progress.workspace_id != self.workspace_id
        ):
            raise ValueError("retry progress belongs to another AgentRun")
        return self


__all__ = [
    "AgentRunObservation",
    "AgentRunRetryProgress",
    "AgentRunTerminalMetrics",
    "MODEL_REQUEST_ID_PREFIX",
    "ModelRequestObservation",
    "ModelRequestState",
    "ToolTerminalCounts",
]
