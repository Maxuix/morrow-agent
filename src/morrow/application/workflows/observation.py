"""Application-layer seam for observing workflow leaf progress.

The Scheduler forwards each leaf's public ``AgentEvent`` through this seam with
explicit execution identity, so projections never reverse-lookup identity from
ordinary Chat interactions. Observers are best-effort projections: they must
never own durable state and must never abort execution.
"""

from __future__ import annotations

from typing import Protocol

from morrow.core.models import AgentEvent

# status.changed values that describe a bounded, content-free execution stage.
STAGE_STATUSES = frozenset(
    {
        "awaiting_model",
        "thinking",
        "model_responding",
        "tool_preparing",
        "retrying",
        "compacting",
        "compacted",
    }
)

_STAGE_KEYS = ("retry_delay_seconds", "attempt_ordinal")


def stage_payload(event: AgentEvent) -> dict | None:
    """Map one public AgentLoop event to a bounded stage payload, or None.

    The payload carries only stage names, tool names, ordinals and error
    booleans — never reasoning, tool parameters, results or SDK objects.
    """

    if event.type == "status.changed":
        status = event.payload.get("status")
        if status not in STAGE_STATUSES:
            return None
        payload: dict = {"stage": status}
        for key in _STAGE_KEYS:
            value = event.payload.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                payload[key] = value
        return payload
    if event.type == "tool.status":
        status = event.payload.get("status")
        name = event.payload.get("name")
        ordinal = event.payload.get("ordinal")
        total = event.payload.get("total")
        return {
            "stage": "tool_running" if status == "running" else "tool_result",
            "tool": name if isinstance(name, str) else None,
            "ordinal": ordinal if isinstance(ordinal, int) else None,
            "total": total if isinstance(total, int) else None,
            "ok": status == "succeeded",
        }
    return None


class WorkflowEventObserver(Protocol):
    """Receives every leaf AgentEvent with explicit execution identity."""

    def node_event(
        self,
        event: AgentEvent,
        *,
        workflow_run_id: str,
        node_run_id: str,
        node_id: str,
        session_id: str,
        agent_run_id: str | None,
    ) -> None:
        """Observe one public event from one node drive; must not raise."""
