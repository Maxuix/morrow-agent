"""Public event helpers and lifecycle invariants."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from morrow.core.models import AgentEvent, AgentStopCode, FinishReason, utc_now

PUBLIC_EVENT_TYPES = frozenset(
    {"turn.started", "status.changed", "text.delta", "tool.status", "error", "turn.completed"}
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
    errors = [event for event in events if event.type == "error"]
    if len(completed) != 1:
        return False
    if len(errors) > 1:
        return False
    completion = completed[0]
    if completion.payload.get("finish_reason") == FinishReason.ERROR.value:
        if len(errors) != 1 or events[-2] is not errors[0]:
            return False
    elif "stop_code" in completion.payload or errors:
        return False
    if errors and (
        set(errors[0].payload) != {"message", "stop_code"}
        or errors[0].payload.get("stop_code") != completion.payload.get("stop_code")
    ):
        return False
    allowed_tool_keys = {
        "call_id",
        "name",
        "status",
        "ordinal",
        "total",
        "error_code",
        "truncated",
    }
    for tool_event in (event for event in events if event.type == "tool.status"):
        payload = tool_event.payload
        if not {"call_id", "name", "status", "ordinal", "total"} <= set(payload):
            return False
        if not set(payload) <= allowed_tool_keys:
            return False
        if payload["status"] not in {"running", "succeeded", "failed", "cancelled", "skipped"}:
            return False
        if not (1 <= payload["ordinal"] <= payload["total"]):
            return False
    return not any(event.type == "turn.started" for event in events[1:])


def completion_payload(
    reason: FinishReason, text: str = "", *, stop_code: AgentStopCode | None = None
) -> dict[str, Any]:
    if reason == FinishReason.ERROR and stop_code is None:
        raise ValueError("error completion requires stop_code")
    if reason != FinishReason.ERROR and stop_code is not None:
        raise ValueError("only error completion carries stop_code")
    payload: dict[str, Any] = {
        "finish_reason": reason.value,
        "text": text,
        "text_length": len(text),
    }
    if stop_code is not None:
        payload["stop_code"] = stop_code.value
    return payload
