"""Typed activity observation contract (activity_schema=1).

This module defines the identity, payload and observer seam for realtime
action-trajectory projections (master plan ``agent-transparency`` D02/D05).
Activities are display projections only: they never own durable execution
facts, never carry unprojected provider text and never write chat history.

Contract invariants enforced here:

- identity always carries workspace/root-session/source-session scope;
- every payload is a closed discriminated union, never an open dict;
- kind-specific required identities (tool needs a call or execution id,
  approval needs an approval id, node output needs a node run);
- terminal states require ``ended_at`` and running states forbid it;
- bounded text fields with hard character limits.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal, Protocol

from pydantic import Field, field_validator, model_validator

from morrow.core.models import ProtocolModel, utc_now

ACTIVITY_SCHEMA_VERSION = 1
ACTIVITY_ID_PREFIX = "act"
ACTIVITY_ID_PATTERN = rf"^{ACTIVITY_ID_PREFIX}_[A-Za-z0-9_-]+$"
ID_MAX_LENGTH = 128

ACTIVITY_TITLE_MAX_CHARS = 200
ACTIVITY_SUMMARY_MAX_CHARS = 2000
ACTIVITY_PREVIEW_REF_MAX_CHARS = 512
ACTIVITY_DELTA_MAX_BYTES = 4096

TERMINAL_ACTIVITY_STATES = frozenset({"succeeded", "failed", "cancelled", "skipped"})

ActivityKind = Literal["model", "tool", "approval", "retry", "compaction", "node_output", "control"]
ActivityState = Literal[
    "preparing", "waiting", "running", "succeeded", "failed", "cancelled", "skipped", "unknown"
]
ActivityOrigin = Literal[
    "agent_loop",
    "tool_executor",
    "scheduler",
    "workflow_bridge",
    "planning_service",
    "control_service",
]
# Vendor thinking visibility (proposal 3.2); "none" keeps the timeline usable
# without fabricating text when a provider exposes no reasoning surface.
ThinkingCapability = Literal["none", "activity_only", "visible_text", "summary"]
# Content availability of the bounded transient preview (proposal 4.6);
# "committed" marks content that survived durably and is served from its ref.
ContentAvailability = Literal["live", "evicted", "unsaved", "none", "committed"]


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("activity timestamps must be timezone-aware")
    return value


def stable_tool_activity_id(tool_execution_id: str) -> str:
    """Stable activity id for an admitted tool execution (proposal 4.2)."""

    return f"{ACTIVITY_ID_PREFIX}_tool_{tool_execution_id}"


def prepared_tool_activity_id(call_id: str) -> str:
    """Pre-admission identity for a tool preparation item (proposal 4.2)."""

    return f"{ACTIVITY_ID_PREFIX}_call_{call_id}"


class ActivityIdentity(ProtocolModel):
    """Execution scope for one activity. ``?`` fields are conditional, never unchecked."""

    workspace_id: str = Field(min_length=1, max_length=ID_MAX_LENGTH)
    root_session_id: str = Field(min_length=1, max_length=ID_MAX_LENGTH)
    source_session_id: str = Field(min_length=1, max_length=ID_MAX_LENGTH)
    interaction_id: str | None = Field(default=None, max_length=ID_MAX_LENGTH)
    planning_operation_id: str | None = Field(default=None, max_length=ID_MAX_LENGTH)
    workflow_run_id: str | None = Field(default=None, max_length=ID_MAX_LENGTH)
    node_run_id: str | None = Field(default=None, max_length=ID_MAX_LENGTH)
    node_id: str | None = Field(default=None, max_length=ID_MAX_LENGTH)
    agent_run_id: str | None = Field(default=None, max_length=ID_MAX_LENGTH)
    turn_id: str | None = Field(default=None, max_length=ID_MAX_LENGTH)
    model_request_id: str | None = Field(default=None, max_length=ID_MAX_LENGTH)
    attempt_id: str | None = Field(default=None, max_length=ID_MAX_LENGTH)
    tool_execution_id: str | None = Field(default=None, max_length=ID_MAX_LENGTH)
    call_id: str | None = Field(default=None, max_length=ID_MAX_LENGTH)


class ModelActivityPayload(ProtocolModel):
    """Model request / response progress (proposal 3.3 wait states)."""

    kind: Literal["model"] = "model"
    stage: Literal["awaiting_model", "thinking", "responding", "tool_preparing"]
    attempt_ordinal: int | None = Field(default=None, ge=1)
    reasoning_capability: ThinkingCapability | None = None


class ToolActivityPayload(ProtocolModel):
    """One tool invocation identity; call identity must exist (proposal 4.2)."""

    kind: Literal["tool"] = "tool"
    tool_name: str = Field(min_length=1, max_length=64)
    call_id: str | None = Field(default=None, max_length=ID_MAX_LENGTH)
    tool_execution_id: str | None = Field(default=None, max_length=ID_MAX_LENGTH)
    ordinal: int | None = Field(default=None, ge=1)
    total: int | None = Field(default=None, ge=1)
    exit_code: int | None = None
    # Value-free failure diagnostics (error code plus the stable reason and
    # field path of an argument-validation failure); never raw arguments.
    error_code: str | None = Field(default=None, max_length=64)
    validation_reason: str | None = Field(default=None, max_length=64)
    validation_path: str | None = Field(default=None, max_length=128)

    @model_validator(mode="after")
    def call_identity_required(self) -> ToolActivityPayload:
        if not self.call_id and not self.tool_execution_id:
            raise ValueError("tool activity requires call_id or tool_execution_id")
        if self.ordinal is not None and self.total is not None and self.ordinal > self.total:
            raise ValueError("tool ordinal must not exceed total")
        return self


class ApprovalActivityPayload(ProtocolModel):
    kind: Literal["approval"] = "approval"
    approval_id: str = Field(min_length=1, max_length=ID_MAX_LENGTH)
    decision: Literal["approved", "denied"] | None = None


class RetryActivityPayload(ProtocolModel):
    kind: Literal["retry"] = "retry"
    attempt_ordinal: int = Field(ge=2)
    retry_delay_seconds: float | None = Field(default=None, ge=0)
    reason_code: str | None = Field(default=None, max_length=64)


class CompactionActivityPayload(ProtocolModel):
    kind: Literal["compaction"] = "compaction"
    direction: Literal["compacting", "compacted"]


class NodeOutputActivityPayload(ProtocolModel):
    kind: Literal["node_output"] = "node_output"
    output_ref: str | None = Field(default=None, max_length=ACTIVITY_PREVIEW_REF_MAX_CHARS)


class ControlActivityPayload(ProtocolModel):
    """Typed control receipts (proposal 5.1); never an LLM intent classification."""

    kind: Literal["control"] = "control"
    command: Literal["steer", "cancel", "pause", "resume"]
    receipt: Literal["accepted", "pending", "applied", "expired", "rejected"] | None = None


ActivityPayload = Annotated[
    ModelActivityPayload
    | ToolActivityPayload
    | ApprovalActivityPayload
    | RetryActivityPayload
    | CompactionActivityPayload
    | NodeOutputActivityPayload
    | ControlActivityPayload,
    Field(discriminator="kind"),
]


class ActivityItem(ProtocolModel):
    """One bounded, typed activity record (proposal 4.2 ``item`` block).

    Transport fields (``protocol_version``/``stream_epoch``/``sequence``) stay
    on the delivery frame, not on this record: one item maps to many frames.
    """

    schema_version: Literal[ACTIVITY_SCHEMA_VERSION] = ACTIVITY_SCHEMA_VERSION
    activity_id: str = Field(pattern=ACTIVITY_ID_PATTERN, max_length=ID_MAX_LENGTH)
    revision: int = Field(ge=1)
    kind: ActivityKind
    state: ActivityState
    origin: ActivityOrigin
    identity: ActivityIdentity
    payload: ActivityPayload
    started_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    ended_at: datetime | None = None
    last_activity_at: datetime | None = None
    safe_title: str = Field(min_length=1, max_length=ACTIVITY_TITLE_MAX_CHARS)
    safe_summary: str | None = Field(default=None, max_length=ACTIVITY_SUMMARY_MAX_CHARS)
    preview_ref: str | None = Field(default=None, max_length=ACTIVITY_PREVIEW_REF_MAX_CHARS)
    # Controlled JSON read path for durable safe content (activity-content
    # endpoint); distinct from ``preview_ref``, which addresses a binary
    # artifact served by the artifact content endpoint.
    content_ref: str | None = Field(default=None, max_length=ACTIVITY_PREVIEW_REF_MAX_CHARS)
    truncated: bool = False
    availability: ContentAvailability = "none"

    @field_validator("started_at", "updated_at", "ended_at", "last_activity_at")
    @classmethod
    def _aware_timestamps(cls, value: datetime | None) -> datetime | None:
        return None if value is None else _aware(value)

    @field_validator("safe_title", "safe_summary")
    @classmethod
    def _non_empty_text(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("activity text must not be blank")
        return value

    @model_validator(mode="after")
    def _validate_item(self) -> ActivityItem:
        if self.payload.kind != self.kind:
            raise ValueError("payload kind must match item kind")
        if self.ended_at is not None:
            if self.state not in TERMINAL_ACTIVITY_STATES:
                raise ValueError("only terminal states may carry ended_at")
            if self.ended_at < self.started_at:
                raise ValueError("ended_at must not precede started_at")
        elif self.state in TERMINAL_ACTIVITY_STATES:
            raise ValueError("terminal state requires ended_at")
        if self.updated_at < self.started_at:
            raise ValueError("updated_at must not precede started_at")
        if self.last_activity_at is not None and self.last_activity_at < self.started_at:
            raise ValueError("last_activity_at must not precede started_at")
        identity = self.identity
        if self.kind == "node_output" and not identity.node_run_id:
            raise ValueError("node_output activity requires node_run_id identity")
        if self.kind == "model" and not (identity.agent_run_id or identity.planning_operation_id):
            raise ValueError("model activity requires agent_run_id or planning_operation_id")
        return self


class ActivityObserver(Protocol):
    """Best-effort realtime projection seam (proposal 4.1).

    Implementations must never raise, never block execution and never own
    durable state. Text passed here is already projected; observers never
    receive raw provider payloads.
    """

    def activity_upsert(self, item: ActivityItem) -> None:
        """Insert or update one activity record by (activity_id, revision)."""
        ...

    def activity_delta(self, activity_id: str, revision: int, delta: str) -> None:
        """Append bounded projected text to an activity's transient content."""
        ...

    def activity_content_reset(self, activity_id: str, reason: str) -> None:
        """Drop cached content for one activity; reason is an bounded code."""
        ...


class NullActivityObserver:
    """No-op observer used when composition supplies none; behaviour unchanged."""

    def activity_upsert(self, item: ActivityItem) -> None:
        return None

    def activity_delta(self, activity_id: str, revision: int, delta: str) -> None:
        return None

    def activity_content_reset(self, activity_id: str, reason: str) -> None:
        return None
