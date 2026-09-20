"""Frozen cross-lane protocol contracts for the four-lane parallel repair.

P01 freeze, contract version 1.1.0 (see tests/fixtures/parallel_contracts/README.md).
Lanes A (runtime), B (planning), C (trajectory) and D (GUI) exchange facts only
through the typed models, literal sets and ports defined here. This module is
coordinator-owned: adding or changing a field requires a contract-version bump
plus wire-fixture updates on both the Python and TypeScript sides.

Lane-internal types stay in lane-owned modules. Everything here crosses a lane
boundary: the interrupted-turn result (A produces, D renders), execution-segment
identity and the current-segment query (A owns storage), planning request
outcomes (B owns storage), the in-transaction TimelineSink port and timeline
cursor protocol (C implements for real; A/B call it through a fake), bounded
safe-content references, and the GUI control/pause request shapes.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Protocol, runtime_checkable

from pydantic import Field, model_validator

from morrow.core.models import INTERRUPT_STOP_CODES, AgentStopCode, ProtocolModel

#: Contract version; must match .agent/parallel/contracts/wire-fixtures.json.
CONTRACT_VERSION = "1.1.0"

SegmentStatus = Literal["active", "interrupted", "completed"]

#: Spec 3.2: user interrupts and internal plan-handoff boundaries are distinct facts.
PauseReason = Literal["user_interrupt", "node_boundary", "provider_failure", "process_interrupt"]

PauseLifecycle = Literal["requested", "quiescing", "suspended", "resumed"]

TimelineEntryKind = Literal[
    "user_message",
    "assistant_message",
    "turn_status",
    "interruption",
    "tool_activity",
    "planning_input",
    "plan_version",
    "control_input",
    "node_progress",
    "result",
    "artifact_ref",
]

#: Content availability: "committed" is replayable after restart; "pending"/
#: "unsaved" must never be presented as durable (spec 4.2, 5.2).
ContentAvailability = Literal["committed", "pending", "unsaved"]

MODEL_REQUEST_OUTCOMES: tuple[str, ...] = (
    "succeeded",
    "failed",
    "interrupted",
    "cancelled",
)
CANDIDATE_VALIDATION_OUTCOMES: tuple[str, ...] = ("valid", "invalid", "not_produced")
PLANNING_OPERATION_OUTCOMES: tuple[str, ...] = (
    "running",
    "paused",
    "succeeded",
    "failed",
    "cancelled",
    "expired",
)
_PLANNING_OUTCOMES_BY_LAYER: dict[str, tuple[str, ...]] = {
    "model_request": MODEL_REQUEST_OUTCOMES,
    "candidate_validation": CANDIDATE_VALIDATION_OUTCOMES,
    "planning_operation": PLANNING_OPERATION_OUTCOMES,
}

PlanningOutcomeLayer = Literal["model_request", "candidate_validation", "planning_operation"]


class TurnInterruptOutcome(ProtocolModel):
    """Typed terminal result of interrupting the current turn (spec 2.2/3.3).

    The literal values are frozen here; the implementing lane adds the same
    names to ``FinishReason``/``AgentStopCode`` instead of reusing
    ``cancelled`` for a user pause.
    """

    turn_id: str = Field(min_length=1, max_length=128)
    agent_run_id: str | None = Field(default=None, max_length=128)
    finish_reason: Literal["interrupted"] = "interrupted"
    stop_code: AgentStopCode = AgentStopCode.USER_PAUSE

    @model_validator(mode="after")
    def valid_interrupt_code(self) -> TurnInterruptOutcome:
        if self.stop_code not in INTERRUPT_STOP_CODES:
            raise ValueError("unsupported interruption stop code")
        return self

    committed_position: int | None = Field(default=None, ge=0)
    partial_text: str | None = Field(default=None, max_length=8192)
    workflow_run_id: str | None = Field(default=None, max_length=128)
    node_run_id: str | None = Field(default=None, max_length=128)
    segment_id: str | None = Field(default=None, max_length=128)


class PauseIntentFact(ProtocolModel):
    """Durably accepted pause intent; cancel is a separate fact, never this."""

    control_generation: int = Field(ge=1)
    command_id: str = Field(min_length=1, max_length=128)
    owner: Literal["workflow_run", "planning_operation", "chat_turn"]
    owner_id: str = Field(min_length=1, max_length=128)
    reason: PauseReason
    lifecycle: PauseLifecycle = "requested"
    requested_at: datetime
    suspended_at: datetime | None = None
    resumed_at: datetime | None = None


class ExecutionSegmentIdentity(ProtocolModel):
    """One append-only node execution segment (spec 3.2.1).

    ``(workflow_run_id, node_run_id, ordinal)`` is unique and a node has at
    most one ``active`` segment at any time. The original ``NodeRun`` bindings
    stay readable and map to the first segment.
    """

    segment_id: str = Field(min_length=1, max_length=128)
    workflow_run_id: str = Field(min_length=1, max_length=128)
    node_run_id: str = Field(min_length=1, max_length=128)
    ordinal: int = Field(ge=1)
    leaf_session_id: str | None = Field(default=None, max_length=128)
    leaf_task_run_id: str | None = Field(default=None, max_length=128)
    agent_run_id: str | None = Field(default=None, max_length=128)
    turn_id: str | None = Field(default=None, max_length=128)
    status: SegmentStatus
    pause_reason: PauseReason | None = None
    previous_segment_id: str | None = Field(default=None, max_length=128)
    accepted_command_id: str | None = Field(default=None, max_length=128)


@runtime_checkable
class SegmentDirectoryPort(Protocol):
    """Current-segment lookup; the real implementation belongs to lane A."""

    def current_segment(self, node_run_id: str) -> ExecutionSegmentIdentity | None:
        """Return the active segment of a node, or None when it has none."""
        ...

    def segments(
        self, workflow_run_id: str, node_run_id: str
    ) -> tuple[ExecutionSegmentIdentity, ...]:
        """Return all segments of one node ordered by ordinal."""
        ...


class PlanningRequestOutcome(ProtocolModel):
    """Layered planning result so "model returned" is never "plan valid" (4.1)."""

    operation_id: str = Field(min_length=1, max_length=128)
    attempt: int = Field(ge=1)
    request_sequence: int = Field(ge=1)
    layer: PlanningOutcomeLayer
    outcome: str = Field(min_length=1, max_length=32)
    started_at: datetime
    ended_at: datetime | None = None
    reason: str | None = Field(default=None, max_length=256)
    revision: int = Field(default=1, ge=1)

    @model_validator(mode="after")
    def outcome_matches_layer(self):
        allowed = _PLANNING_OUTCOMES_BY_LAYER[self.layer]
        if self.outcome not in allowed:
            raise ValueError(f"outcome {self.outcome!r} is not valid for layer {self.layer}")
        return self


class TimelineEntryIdentity(ProtocolModel):
    """Cross-lane identity of one unified display entry (spec 5.1).

    The display index is not an agent-context log: the entry references the
    source of truth, and the body keeps living with its original owner.
    """

    workspace_id: str = Field(min_length=1, max_length=128)
    root_session_id: str = Field(min_length=1, max_length=128)
    item_id: str = Field(min_length=1, max_length=256)
    kind: TimelineEntryKind
    source_kind: str = Field(min_length=1, max_length=64)
    source_id: str = Field(min_length=1, max_length=256)
    source_session_id: str = Field(min_length=1, max_length=128)
    source_position: int | None = Field(default=None, ge=0)
    planning_binding_id: str | None = Field(default=None, max_length=128)
    planning_operation_id: str | None = Field(default=None, max_length=128)
    workflow_run_id: str | None = Field(default=None, max_length=128)
    node_run_id: str | None = Field(default=None, max_length=128)
    segment_id: str | None = Field(default=None, max_length=128)
    parent_item_id: str | None = Field(default=None, max_length=256)
    revision: int = Field(default=1, ge=1)
    occurred_at: datetime
    content_ref: str | None = Field(default=None, max_length=512)
    availability: ContentAvailability = "committed"


class TimelineSinkEntry(TimelineEntryIdentity):
    """A stored entry: ``timeline_position`` is assigned by the index owner."""

    timeline_position: int = Field(ge=0)


@runtime_checkable
class TimelineSinkPort(Protocol):
    """In-transaction display sink; the real implementation belongs to lane C."""

    def record(self, identity: TimelineEntryIdentity) -> None:
        """Record one entry inside the caller's open transaction.

        Implementations must not commit and must not make the entry visible
        before the source fact commits; a rolled-back transaction removes it.
        Assigning ``timeline_position`` is the sink's job.
        """
        ...


@runtime_checkable
class PostCommitNotifierPort(Protocol):
    """Display fan-out that runs only after the caller's transaction commits."""

    def notify_after_commit(self, entry: TimelineEntryIdentity) -> None:
        """Schedule a post-commit notification; a rollback must never notify."""
        ...


class SafeContentRef(ProtocolModel):
    """Bounded safe-content reference; clients only ever see committed offsets."""

    content_id: str = Field(min_length=1, max_length=128)
    kind: Literal["inline", "blob"]
    committed_offset: int = Field(ge=0)
    total_length: int | None = Field(default=None, ge=0)
    availability: ContentAvailability = "committed"


class TimelineCursor(ProtocolModel):
    """Versioned cursor; snapshot/subscribe share one high-water contract."""

    schema_version: int = Field(default=1, ge=1)
    workspace_id: str = Field(min_length=1, max_length=128)
    root_session_id: str = Field(min_length=1, max_length=128)
    high_water: int = Field(ge=0)
    before_position: int | None = Field(default=None, ge=0)


class TimelineSnapshotPage(ProtocolModel):
    """Self-consistent snapshot page; ``reset`` demands a full re-fetch."""

    entries: tuple[TimelineSinkEntry, ...] = Field(default_factory=tuple)
    cursor: TimelineCursor
    revision: int = Field(ge=1)
    reset: bool = False


class GuiControlRequest(ProtocolModel):
    """Body of the frozen chat control endpoint (mirrors server contract)."""

    command_id: str = Field(min_length=1, max_length=128)
    session_id: str = Field(min_length=1, max_length=128)
    text: str = Field(min_length=1, max_length=4096)
    client_message_id: str | None = Field(default=None, max_length=128)


class GuiPauseRequest(ProtocolModel):
    """Body of the frozen pause entry; cancel stays a separate action."""

    command_id: str = Field(min_length=1, max_length=128)
    session_id: str = Field(min_length=1, max_length=128)
    expected_run_row_version: int | None = Field(default=None, ge=1)
