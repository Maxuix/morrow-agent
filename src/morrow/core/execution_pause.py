"""Durable execution pause facts and safe points (P02, spec 3.2).

The frozen cross-lane shapes (``PauseIntentFact``, the pause lifecycle
literals) live in ``morrow.core.contracts``. This module owns the lane-internal
persistence record: the accepted pause point row, the bounded safety point that
makes an interrupted turn resumable, and the separate cancel fact. A pause
point never stores coroutines, SDK objects, credentials or unbounded state —
only the references the next turn needs.

It also defines the runtime seam (P03) that injects an *optional* pause
authority into ``AgentLoop.run_task``: durable chat and workflow hosts supply
one, while standalone callers can omit it and keep the historical behavior.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Literal, Protocol, runtime_checkable

from pydantic import Field, model_validator

from morrow.core.contracts import (
    PauseIntentFact,
    PauseReason,
)
from morrow.core.models import AgentStopCode, ProtocolModel

__all__ = [
    "LocalTurnPauseControl",
    "PauseIntentRequest",
    "PauseSafetyPoint",
    "TurnPauseControl",
    "TurnPauseSignal",
    "WorkflowPausePoint",
]

#: Frozen owner scope of a pause intent (mirrors contracts.PauseIntentFact.owner).
PauseOwnerKind = Literal["workflow_run", "planning_operation", "chat_turn"]


class PauseIntentRequest(ProtocolModel):
    """Caller-supplied pause command before a control generation is allocated."""

    command_id: str = Field(min_length=1, max_length=128)
    owner: PauseOwnerKind
    owner_id: str = Field(min_length=1, max_length=128)
    reason: PauseReason


class PauseSafetyPoint(ProtocolModel):
    """Bounded safe-point references recorded when a segment settles.

    ``committed_position`` is the last durable conversation position the next
    turn builds on; interrupted identities are recorded for attribution so a
    late event can never drive a new segment.
    """

    stop_code: AgentStopCode | None = None
    task_run_id: str | None = Field(default=None, max_length=128)
    committed_position: int | None = Field(default=None, ge=0)
    interrupted_turn_id: str | None = Field(default=None, max_length=128)
    interrupted_agent_run_id: str | None = Field(default=None, max_length=128)
    interrupted_request_id: str | None = Field(default=None, max_length=128)
    closed_tool_call_ids: tuple[str, ...] = Field(default_factory=tuple)
    note: str | None = Field(default=None, max_length=2048)

    @model_validator(mode="after")
    def bounded_evidence(self) -> PauseSafetyPoint:
        if len(self.closed_tool_call_ids) > 64:
            raise ValueError("a safety point records at most 64 closed tool calls")
        if any(not call_id or len(call_id) > 128 for call_id in self.closed_tool_call_ids):
            raise ValueError("closed tool call ids must be bounded non-empty strings")
        return self


class WorkflowPausePoint(ProtocolModel):
    """One accepted pause control cycle and everything its resume needs.

    Lifecycle moves ``requested -> quiescing -> suspended -> resumed`` under
    OCC. Cancel is a separate fact recorded on this row; it never rewrites the
    lifecycle to ``resumed``.
    """

    pause_point_id: str = Field(min_length=1, max_length=128)
    workspace_id: str = Field(min_length=1, max_length=128)
    fact: PauseIntentFact
    node_run_id: str | None = Field(default=None, max_length=128)
    segment_id: str | None = Field(default=None, max_length=128)
    safety: PauseSafetyPoint | None = None
    continuation_segment_id: str | None = Field(default=None, max_length=128)
    continuation_command_id: str | None = Field(default=None, max_length=128)
    continuation_input: str | None = Field(default=None, max_length=4096)
    cancel_command_id: str | None = Field(default=None, max_length=128)
    cancel_reason: str | None = Field(default=None, max_length=256)
    cancelled_at: datetime | None = None
    row_version: int = Field(default=1, ge=1, strict=True)
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def pause_point_facts(self) -> WorkflowPausePoint:
        if self.continuation_segment_id is not None:
            if self.fact.lifecycle != "resumed":
                raise ValueError("a continuation segment implies a resumed pause cycle")
            if self.continuation_command_id is None:
                raise ValueError("a continuation segment records the command that triggered it")
        if (self.cancel_command_id is None) != (self.cancelled_at is None):
            raise ValueError("a cancel fact records its command and timestamp together")
        return self


class TurnPauseSignal(ProtocolModel):
    """One pause authority read delivered to a running turn."""

    control_generation: int = Field(ge=1)
    command_id: str | None = Field(default=None, max_length=128)
    reason: PauseReason = "user_interrupt"


@runtime_checkable
class TurnPauseControl(Protocol):
    """Optional pause authority consumed by ``AgentLoop.run_task``.

    ``pending_signal`` is the durable authority read at the loop's check
    points; ``wait_signal`` is the independent wake hint that ends an
    unbounded model/approval wait without cancelling the driver task.
    """

    def pending_signal(self, session_id: str) -> TurnPauseSignal | None: ...

    async def wait_signal(self, session_id: str) -> None: ...


class LocalTurnPauseControl:
    """In-process pause authority: one signal per session, Event-based wake.

    The scheduler-side wiring (P04) backs ``pending_signal`` with the durable
    pause journal; this local form holds the fact in memory for tests and for
    single-process drivers. ``interrupt`` wakes every current waiter for the
    session; the loop re-reads ``pending_signal`` as the authority.
    """

    def __init__(self) -> None:
        self._signals: dict[str, TurnPauseSignal] = {}
        self._events: dict[str, asyncio.Event] = {}

    def _event(self, session_id: str) -> asyncio.Event:
        event = self._events.get(session_id)
        if event is None:
            event = asyncio.Event()
            self._events[session_id] = event
        return event

    def interrupt(self, session_id: str, signal: TurnPauseSignal) -> None:
        """Deliver a pause signal and wake any waiting driver."""
        self._signals[session_id] = signal
        self._event(session_id).set()

    def pending_signal(self, session_id: str) -> TurnPauseSignal | None:
        return self._signals.get(session_id)

    def clear(self, session_id: str) -> None:
        """Consume the delivered signal after the turn ended interrupted."""
        self._signals.pop(session_id, None)
        event = self._events.get(session_id)
        if event is not None:
            event.clear()

    async def wait_signal(self, session_id: str) -> None:
        await self._event(session_id).wait()
