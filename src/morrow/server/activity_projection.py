"""Derives the first activity slice from existing public events (P2.3).

The projector is stateful per session stream: it remembers open model
requests, retries, revisions and first-seen timestamps so every observation
becomes a complete, contract-valid ``ActivityItem`` wire dict. It derives only
from durable-timestamp public events; it never fabricates content and never
touches chat history. Workflow node identity (when the caller supplies it)
flows into the item identity so root timelines can group by node.
"""

from __future__ import annotations

from pydantic import ValidationError

from morrow.application.tool_display import format_tool_display
from morrow.core.activity import (
    ACTIVITY_SCHEMA_VERSION,
    TERMINAL_ACTIVITY_STATES,
    ActivityItem,
    prepared_tool_activity_id,
    stable_tool_activity_id,
)
from morrow.core.capabilities import CommandToolFact
from morrow.core.models import utc_now

_MODEL_STAGES = {
    "awaiting_model": "awaiting_model",
    "thinking": "thinking",
    "model_responding": "responding",
    "tool_preparing": "tool_preparing",
}
_STAGE_TITLES = {
    "awaiting_model": "等待模型响应",
    "thinking": "正在思考",
    "responding": "正在生成回复",
    "tool_preparing": "正在准备工具调用",
}
_FINISH_TO_STATE = {
    "stop": "succeeded",
    "steered": "cancelled",
    "cancelled": "cancelled",
    # A typed user pause (interrupted + user_pause) is never a failure; the
    # model request ends cancelled the same way a steer does (lane A note).
    "interrupted": "cancelled",
}


def _artifact_preview_ref(identity: dict, artifact_refs: tuple) -> str | None:
    """Controlled read path for the latest produced artifact (P4.4).

    The URL must match the registered session-scoped artifact route:
    ``/v1/workspaces/{ws}/sessions/{session}/artifacts/{id}/content``.
    """
    session_id = identity.get("source_session_id")
    for reference in reversed(artifact_refs):
        artifact_id = getattr(reference, "artifact_id", None)
        if isinstance(artifact_id, str) and artifact_id and session_id:
            return (
                f"/v1/workspaces/{identity['workspace_id']}/sessions/{session_id}"
                f"/artifacts/{artifact_id}/content"
            )
    return None


_DISPOSITION_STATES = {
    "succeeded": "succeeded",
    "failed": "failed",
    "denied": "cancelled",
    "cancelled": "cancelled",
    "interrupted": "cancelled",
    "unknown": "unknown",
    "pending": "unknown",
}
_TRACK_LIMIT = 512


def _iso(value) -> str | None:
    return value.isoformat() if value is not None else None


class SessionActivityProjector:
    """One projector per session stream; emits whole-item upsert dicts."""

    def __init__(self) -> None:
        self._revisions: dict[str, int] = {}
        self._started: dict[str, str] = {}
        self._last: dict[str, dict] = {}
        self._turn_scope: dict[str, str] = {}
        self._open_model: dict[str, str] = {}
        self._open_retry: dict[str, str] = {}
        # Admission rekeys (act_call_* → act_tool_*): drained by the stream
        # owner and emitted as activity_rekey frames before the migrated upsert.
        self._pending_rekeys: list[dict] = []
        # call_id → current tool activity id, so late output deltas follow the
        # rekey instead of writing to the abandoned prepared identity.
        self._tool_activity_by_call: dict[str, str] = {}

    def _trim(self) -> None:
        for cache in (
            self._revisions,
            self._started,
            self._last,
            self._turn_scope,
            self._tool_activity_by_call,
        ):
            while len(cache) > _TRACK_LIMIT:
                cache.pop(next(iter(cache)))

    def observe_event(
        self,
        event,
        *,
        workspace_id: str,
        session_id: str,
        node_identity: dict | None = None,
        agent_run_id: str | None = None,
    ) -> list[dict]:
        """Map one public AgentEvent to zero or more validated activity items."""
        try:
            payload = event.payload or {}
            event_type = event.type
        except AttributeError:
            return []
        if event_type not in {"status.changed", "tool.status", "turn.completed"}:
            return []
        timestamp = event.timestamp or utc_now()
        identity = self._identity(
            workspace_id, session_id, node_identity, agent_run_id, getattr(event, "turn_id", None)
        )
        items: list[dict] = []
        if event_type == "status.changed":
            items.extend(self._status_changed(payload, identity, timestamp))
        elif event_type == "tool.status":
            items.append(self._tool_status(payload, identity, timestamp))
        else:
            items.extend(self._turn_completed(payload, identity, timestamp))
        validated = []
        for item in items:
            try:
                ActivityItem.model_validate(item)
            except ValidationError:
                continue
            self._last[item["activity_id"]] = item
            self._trim()
            validated.append(item)
        return validated

    def observe_tool_fact(
        self,
        *,
        workspace_id: str,
        session_id: str,
        node_identity: dict | None = None,
        agent_run_id: str | None = None,
        call_id: str,
        tool_name: str,
        ordinal: int | None,
        total: int | None,
        phase: str,
        timestamp: str,
        disposition: str | None = None,
        arguments_json: str | None = None,
        facts: tuple = (),
        artifact_refs: tuple = (),
        error_code: str | None = None,
        validation_reason: str | None = None,
        validation_path: str | None = None,
        tool_execution_id: str | None = None,
    ) -> dict | None:
        """Build one contract-valid item from a real tool transition (P4.1).

        Once the durable execution id is known the item carries the stable
        ``act_tool_*`` identity — the same id durable recovery rebuilds — and
        any existing ``act_call_*`` state migrates, queuing one rekey record.
        """
        if not isinstance(call_id, str) or not call_id:
            return None
        prepared_id = prepared_tool_activity_id(call_id)
        if isinstance(tool_execution_id, str) and tool_execution_id:
            stable_id = stable_tool_activity_id(tool_execution_id)
        else:
            stable_id = None
        activity_id = stable_id or prepared_id
        previous = self._last.get(prepared_id)
        if previous is None and stable_id is not None:
            previous = self._last.get(stable_id)
        if stable_id is not None:
            self._tool_activity_by_call[call_id] = stable_id
            if stable_id != prepared_id and self._last.get(prepared_id) is not None:
                # Migrate the prepared item's bookkeeping to the stable id so
                # revision/started continuity survives the identity change.
                self._started[stable_id] = self._started.pop(prepared_id, timestamp)
                self._revisions[stable_id] = self._revisions.pop(prepared_id, 0)
                del self._last[prepared_id]
                self._pending_rekeys.append(
                    {"from_activity_id": prepared_id, "to_activity_id": stable_id}
                )
        else:
            self._tool_activity_by_call.setdefault(call_id, activity_id)
        identity = self._identity(workspace_id, session_id, node_identity, agent_run_id, None)
        if stable_id is not None:
            # The stable identity carries the execution id, matching what
            # durable recovery rebuilds for the same execution.
            identity = {**identity, "tool_execution_id": tool_execution_id}
        if phase == "prepared":
            state, prefix = "preparing", "准备 "
        elif phase == "awaiting_approval":
            state, prefix = "waiting", "等待批准 "
        elif phase == "executing":
            state, prefix = "running", "执行 "
        elif phase == "terminal":
            mapped = _DISPOSITION_STATES.get(disposition or "", "unknown")
            state, prefix = mapped, ""
        else:
            return None
        previous_payload = previous["payload"] if previous is not None else {}
        previous_ordinal = previous_payload.get("ordinal")
        previous_total = previous_payload.get("total")
        command_fact = next((fact for fact in facts if isinstance(fact, CommandToolFact)), None)
        payload = {
            "kind": "tool",
            "tool_name": tool_name,
            "call_id": call_id,
            "tool_execution_id": (
                tool_execution_id
                if stable_id is not None
                else previous_payload.get("tool_execution_id")
            ),
            "ordinal": ordinal if isinstance(ordinal, int) and ordinal >= 1 else previous_ordinal,
            "total": total if isinstance(total, int) and total >= 1 else previous_total,
            "exit_code": (
                command_fact.exit_code
                if command_fact is not None
                else previous_payload.get("exit_code")
            ),
        }
        # Terminal failure diagnostics are value-free, bounded and display-safe;
        # they ride the payload so refresh recovery can rebuild the same view.
        for key, value in (
            ("error_code", error_code),
            ("validation_reason", validation_reason),
            ("validation_path", validation_path),
        ):
            if isinstance(value, str) and value:
                payload[key] = value
        # Typed display formatting near the tool declarations (P4.2); the raw
        # arguments never enter the item — only the projected title/summary.
        formatter_title, formatter_summary = format_tool_display(
            tool_name, arguments_json, facts=facts
        )
        title = formatter_title
        if prefix and state != "preparing":
            title = f"{prefix}{tool_name}"
        item = {
            "schema_version": ACTIVITY_SCHEMA_VERSION,
            "activity_id": activity_id,
            "revision": self._revisions.get(activity_id, 0) + 1,
            "kind": "tool",
            "state": state,
            "origin": "tool_executor",
            "identity": identity,
            "payload": payload,
            "started_at": self._started.setdefault(activity_id, timestamp),
            "updated_at": timestamp,
            "ended_at": timestamp if state in TERMINAL_ACTIVITY_STATES else None,
            "last_activity_at": None,
            "safe_title": title if len(title) <= 200 else title[:200],
            "safe_summary": formatter_summary,
            "preview_ref": _artifact_preview_ref(identity, artifact_refs),
            "content_ref": None,
            "truncated": False,
            "availability": "none",
        }
        self._revisions[activity_id] = item["revision"]
        try:
            ActivityItem.model_validate(item)
        except ValidationError:
            return None
        self._last[activity_id] = item
        self._trim()
        return item

    def drain_rekeys(self) -> list[dict]:
        """Pop queued admission rekeys (from_activity_id → to_activity_id)."""
        rekeys, self._pending_rekeys = self._pending_rekeys, []
        return rekeys

    def tool_activity_id_for_call(self, call_id: str) -> str:
        """Current activity id for one call; late deltas follow the rekey."""
        return self._tool_activity_by_call.get(call_id) or prepared_tool_activity_id(call_id)

    def _identity(
        self,
        workspace_id: str,
        session_id: str,
        node_identity: dict | None,
        agent_run_id: str | None,
        turn_id: str | None,
    ) -> dict:
        if node_identity is not None:
            return {
                "workspace_id": workspace_id,
                "root_session_id": node_identity["root_session_id"],
                "source_session_id": node_identity["source_session_id"],
                "workflow_run_id": node_identity.get("workflow_run_id"),
                "node_run_id": node_identity.get("node_run_id"),
                "node_id": node_identity.get("node_id"),
                "agent_run_id": node_identity.get("agent_run_id") or agent_run_id,
                "turn_id": turn_id,
            }
        return {
            "workspace_id": workspace_id,
            "root_session_id": session_id,
            "source_session_id": session_id,
            "agent_run_id": agent_run_id,
            "turn_id": turn_id,
        }

    def _scope(self, identity: dict) -> str:
        return identity.get("node_run_id") or identity.get("turn_id") or "scope"

    def _build(
        self,
        *,
        activity_id: str,
        kind: str,
        payload: dict,
        state: str,
        identity: dict,
        timestamp,
        title: str,
    ) -> dict:
        self._turn_scope.setdefault(
            identity.get("turn_id") or "", identity.get("node_run_id") or ""
        )
        started = self._started.setdefault(activity_id, _iso(timestamp))
        item = {
            "schema_version": ACTIVITY_SCHEMA_VERSION,
            "activity_id": activity_id,
            "revision": self._revisions.get(activity_id, 0) + 1,
            "kind": kind,
            "state": state,
            "origin": "agent_loop",
            "identity": identity,
            "payload": payload,
            "started_at": started,
            "updated_at": _iso(timestamp),
            "ended_at": _iso(timestamp) if state in TERMINAL_ACTIVITY_STATES else None,
            "last_activity_at": None,
            "safe_title": title,
            "safe_summary": None,
            "preview_ref": None,
            "content_ref": None,
            "truncated": False,
            "availability": "none",
        }
        self._revisions[activity_id] = item["revision"]
        return item

    def _close(self, activity_id: str, state: str, timestamp) -> dict | None:
        previous = self._last.get(activity_id)
        if previous is None:
            return None
        item = {
            **previous,
            "revision": self._revisions.get(activity_id, 0) + 1,
            "state": state,
            "updated_at": _iso(timestamp),
        }
        if state in TERMINAL_ACTIVITY_STATES:
            item["ended_at"] = _iso(timestamp)
        self._revisions[activity_id] = item["revision"]
        return item

    def _status_changed(self, payload: dict, identity: dict, timestamp) -> list[dict]:
        status = payload.get("status")
        scope = self._scope(identity)
        if status in _MODEL_STAGES:
            attempt = payload.get("attempt_ordinal")
            activity_id = f"act_model_{scope}_{attempt or 1}"
            items = []
            for open_id in (self._open_model.get(scope), self._open_retry.get(scope)):
                if open_id and open_id != activity_id:
                    closed = self._close(open_id, "succeeded", timestamp)
                    if closed is not None:
                        items.append(closed)
            self._open_model[scope] = activity_id
            items.append(
                self._build(
                    activity_id=activity_id,
                    kind="model",
                    payload={
                        "kind": "model",
                        "stage": _MODEL_STAGES[status],
                        "attempt_ordinal": attempt,
                        "reasoning_capability": None,
                    },
                    state="running",
                    identity=identity,
                    timestamp=timestamp,
                    title=_STAGE_TITLES[_MODEL_STAGES[status]],
                )
            )
            return items
        if status == "retrying":
            attempt = payload.get("attempt_ordinal")
            if not isinstance(attempt, int) or attempt < 2:
                return []
            delay = payload.get("retry_delay_seconds")
            activity_id = f"act_retry_{scope}_{attempt}"
            self._open_retry[scope] = activity_id
            return [
                self._build(
                    activity_id=activity_id,
                    kind="retry",
                    payload={
                        "kind": "retry",
                        "attempt_ordinal": attempt,
                        "retry_delay_seconds": delay if isinstance(delay, (int, float)) else None,
                        "reason_code": None,
                    },
                    state="running",
                    identity=identity,
                    timestamp=timestamp,
                    title=f"第 {attempt} 次尝试",
                )
            ]
        if status == "steered":
            # Real consumption boundary (P6.1): the steering text was admitted
            # into this turn's next model request by the dispatch loop.
            activity_id = f"act_control_{identity.get('turn_id') or 'turn'}"
            return [
                self._build(
                    activity_id=activity_id,
                    kind="control",
                    payload={"kind": "control", "command": "steer", "receipt": "applied"},
                    state="succeeded",
                    identity=identity,
                    timestamp=timestamp,
                    title="纠偏已生效 · 下一模型请求",
                )
            ]
        if status in ("compacting", "compacted"):
            activity_id = f"act_compaction_{scope}"
            return [
                self._build(
                    activity_id=activity_id,
                    kind="compaction",
                    payload={"kind": "compaction", "direction": status},
                    state="running" if status == "compacting" else "succeeded",
                    identity=identity,
                    timestamp=timestamp,
                    title="压缩上下文" if status == "compacting" else "上下文已压缩",
                )
            ]
        return []

    def _tool_status(self, payload: dict, identity: dict, timestamp) -> dict | None:
        call_id = payload.get("call_id")
        name = payload.get("name")
        status = payload.get("status")
        if not isinstance(call_id, str) or not call_id or not isinstance(name, str) or not name:
            return None
        if status == "running":
            # Public events only prove the call was prepared; the handler may
            # not have started. Real executing evidence arrives in P4.1.
            state, prefix = "preparing", "准备 "
        elif status in TERMINAL_ACTIVITY_STATES:
            mapped = self._tool_activity_by_call.get(call_id)
            if mapped is not None and mapped != prepared_tool_activity_id(call_id):
                # The durable fact path already owns this call's terminal state
                # under its stable act_tool_* identity; a late public event must
                # not resurrect the abandoned prepared item.
                return None
            state, prefix = status, ""
        else:
            return None
        ordinal = payload.get("ordinal")
        total = payload.get("total")
        error_code = payload.get("error_code")
        item = self._build(
            activity_id=prepared_tool_activity_id(call_id),
            kind="tool",
            payload={
                "kind": "tool",
                "tool_name": name,
                "call_id": call_id,
                "tool_execution_id": None,
                "ordinal": ordinal if isinstance(ordinal, int) else None,
                "total": total if isinstance(total, int) else None,
                "exit_code": None,
                **(
                    {"error_code": error_code} if isinstance(error_code, str) and error_code else {}
                ),
            },
            state=state,
            identity=identity,
            timestamp=timestamp,
            title=f"{prefix}{name}",
        )
        return item

    def _turn_completed(self, payload: dict, identity: dict, timestamp) -> list[dict]:
        turn_id = identity.get("turn_id")
        scope = self._turn_scope.get(turn_id) or turn_id
        finish = payload.get("finish_reason")
        state = _FINISH_TO_STATE.get(finish, "failed" if finish == "error" else "unknown")
        items = []
        for open_id in (self._open_model.get(scope), self._open_retry.get(scope)):
            if open_id:
                closed = self._close(open_id, state, timestamp)
                if closed is not None:
                    items.append(closed)
        self._open_model.pop(scope, None)
        self._open_retry.pop(scope, None)
        return items
