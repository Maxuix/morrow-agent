"""Public event helpers and lifecycle invariants."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from morrow.core.models import AgentEvent, FinishReason, utc_now

PUBLIC_EVENT_TYPES = frozenset(
    {"turn.started", "status.changed", "text.delta", "error", "turn.completed"}
)


def make_event(
    *,
    event_type: str,
    event_id: str,
    session_id: str,
    turn_id: str,
    sequence: int,
    payload: dict[str, Any] | None = None,
    timestamp: datetime | None = None,
) -> AgentEvent:
    if event_type not in PUBLIC_EVENT_TYPES:
        raise ValueError(f"unsupported public event type: {event_type}")
    return AgentEvent(
        type=event_type,
        event_id=event_id,
        session_id=session_id,
        turn_id=turn_id,
        sequence=sequence,
        timestamp=timestamp or utc_now(),
        payload=payload or {},
    )


def lifecycle_is_valid(events: list[AgentEvent]) -> bool:
    if not events:
        return False
    if events[0].type != "turn.started" or events[-1].type != "turn.completed":
        return False
    if any(
        left.sequence >= right.sequence for left, right in zip(events, events[1:], strict=False)
    ):
        return False
    completed = [event for event in events if event.type == "turn.completed"]
    return len(completed) == 1 and not any(event.type == "turn.started" for event in events[1:])


def completion_payload(reason: FinishReason, text: str = "") -> dict[str, Any]:
    return {"finish_reason": reason.value, "text_length": len(text)}
