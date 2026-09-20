"""Ephemeral visible reply projection. Durable events never contain deltas."""

import json
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from uuid import uuid4

from morrow.application.chat_timeline import MAX_TOOL_FACTS, reply_id
from morrow.core.application import ApplicationError, ApplicationErrorCode
from morrow.core.models import utc_now

from .activities import (
    ACTIVITY_DELIVERY_BYTES,
    ActivityStreamState,
    register_host_state,
)
from .activity_projection import SessionActivityProjector
from .events import EventHub
from .projections import session_wire
from .workflow_stream import replay_planning_items

RING_BYTES = 512 * 1024
RING_FRAMES = 512
DRAFT_BYTES = 256 * 1024
FRAME_TEXT_BYTES = 4096
STREAM_CACHE = 32
SESSION_SUBSCRIBERS = 8
HOST_SUBSCRIBERS = 64
DELIVERY_BYTES = 64 * 1024
STAGES_LIMIT = 64


@dataclass
class ReplyState:
    epoch: str = field(default_factory=lambda: uuid4().hex)
    sequence: int = 0
    frames: deque = field(default_factory=deque)
    ring_bytes: int = 0
    peak_bytes: int = 0
    draft: dict | None = None
    run_id: str | None = None
    next_position: int | None = None
    subscribers: int = 0
    hub: EventHub = field(default_factory=lambda: EventHub(max_subscribers=SESSION_SUBSCRIBERS))
    resyncs: int = 0
    # Latest content-free stage per node_run_id; snapshots replay it so a
    # reconnecting client re-syncs run progress without a history pull.
    stages: OrderedDict = field(default_factory=OrderedDict)
    # Bounded action-trajectory projection with its own epoch/sequence (P2).
    activities: ActivityStreamState = field(default_factory=ActivityStreamState)
    projector: SessionActivityProjector = field(default_factory=SessionActivityProjector)


class ReplyStreams:
    def __init__(self, manager, journal, workspace_id):
        self.manager = manager
        self.journal = journal
        self.workspace_id = workspace_id
        self.states = OrderedDict()
        self.subscribers = 0

    def state(self, sid):
        self.manager.require_session(sid)
        if sid in self.states:
            self.states.move_to_end(sid)
            return self.states[sid]
        while len(self.states) >= STREAM_CACHE:
            idle = next(
                (
                    key
                    for key, value in self.states.items()
                    if not value.subscribers and key not in self.manager.drivers
                ),
                None,
            )
            if idle is None:
                raise ApplicationError(ApplicationErrorCode.BUSY, "Stream cache is busy")
            del self.states[idle]
        state = ReplyState()
        register_host_state(state.activities)
        self.states[sid] = state
        self._recover_activities(sid, state)
        return state

    def _recover_activities(self, sid, state):
        """Cold-start recovery from durable facts (P07.3/P07.4).

        Planning activities rebuild from the durable planning events; attempts
        without an independent terminal fact close as ``unknown``. Durable
        tool skeletons join them under the stable ``act_tool_*`` identity.
        Content persisted before eviction stays reachable through its content
        ref and is marked committed. Best-effort by contract: recovery never
        aborts stream creation.
        """

        try:
            items = replay_planning_items(self.journal, self.workspace_id, sid)
            for item in items:
                state.activities.upsert(item)
            timeline = getattr(self.manager, "timeline", None)
            tool_activities = getattr(timeline, "tool_activities", None)
            if tool_activities is not None:
                recovered = tool_activities(sid, limit=MAX_TOOL_FACTS)["items"]
                known = set(state.activities.entries)
                for item in recovered:
                    if item["activity_id"] not in known:
                        state.activities.upsert(item)
            index = self._timeline_index()
            if index is None:
                return
            activity_ids = [
                entry.item["activity_id"] for entry in state.activities.entries.values()
            ]
            revisions = index.durable_content_revisions(sid, activity_ids)
            for activity_id in revisions:
                entry = state.activities.entries.get(activity_id)
                if entry is not None:
                    if not entry.item.get("content_ref"):
                        entry.item["content_ref"] = self._activity_content_ref(sid, activity_id)
                    entry.item["availability"] = "committed"
        except Exception:
            return

    def _timeline_index(self):
        timeline = getattr(self.manager, "timeline", None)
        return getattr(timeline, "index", None) if timeline is not None else None

    def _activity_content_ref(self, sid, activity_id):
        return f"/v1/workspaces/{self.workspace_id}/sessions/{sid}/activity-content/{activity_id}"

    def emit(self, sid, kind, payload, *, message_id=None):
        state = self.state(sid)
        state.sequence += 1
        frame = {
            "protocol_version": 1,
            "workspace_id": self.workspace_id,
            "session_id": sid,
            "stream_epoch": state.epoch,
            "sequence": state.sequence,
            "type": kind,
            "agent_run_id": state.run_id,
            "message_id": message_id,
            "payload": payload,
        }
        size = len(json.dumps(frame, ensure_ascii=False).encode())
        state.frames.append((frame, size))
        state.ring_bytes += size
        while len(state.frames) > RING_FRAMES or state.ring_bytes > RING_BYTES:
            _, removed = state.frames.popleft()
            state.ring_bytes -= removed
        state.peak_bytes = max(state.peak_bytes, state.ring_bytes)
        state.hub.publish(state.sequence)

    def changed(self, sid):
        self.emit(sid, "queue_changed", {})

    def stage(self, sid, payload):
        """Record one bounded, content-free execution stage and emit its frame.

        Keyed by ``node_run_id`` so stage changes update in place; the bounded
        map replays through snapshots for reconnect recovery.
        """

        key = payload.get("node_run_id")
        if not isinstance(key, str) or not key:
            return
        state = self.state(sid)
        state.stages[key] = payload
        state.stages.move_to_end(key)
        while len(state.stages) > STAGES_LIMIT:
            state.stages.popitem(last=False)
        self.emit(sid, "stage", payload)

    # --- activity stream (P2.2/P2.3): projection with its own cursor ---

    def _project_event(self, sid, state, event, *, node_identity=None):
        if event.type not in {"status.changed", "tool.status", "turn.completed"}:
            return
        items = state.projector.observe_event(
            event,
            workspace_id=self.workspace_id,
            session_id=sid,
            node_identity=node_identity,
            agent_run_id=state.run_id,
        )
        self._apply_activities(sid, state, items)

    def node_activity(self, sid, event, *, node_identity):
        """Project one leaf event onto the ROOT session's activity stream."""
        state = self.state(sid)
        self._project_event(sid, state, event, node_identity=node_identity)

    def _apply_activities(self, sid, state, items):
        if not items:
            return
        state.activities.sweep_expired()
        for item in items:
            if state.activities.upsert(item):
                self._emit_activity(
                    sid,
                    state,
                    "activity_upsert",
                    {"item": state.activities.entries[item["activity_id"]].item},
                )

    def _emit_activity(self, sid, state, kind, payload):
        state.activities.emit(kind, payload, self.workspace_id, sid)
        state.hub.publish(state.activities.sequence)

    def activity_upsert(self, sid, item):
        """Insert or update one activity item on the session's stream (P3.6)."""
        state = self.state(sid)
        self._apply_activities(sid, state, [item])

    def tool_observation(self, sid, fact, *, node_identity=None):
        """Project one real tool transition into the session stream (P4.1)."""
        state = self.state(sid)
        item = state.projector.observe_tool_fact(
            workspace_id=self.workspace_id,
            session_id=sid,
            node_identity=node_identity,
            agent_run_id=state.run_id,
            call_id=fact["call_id"],
            tool_name=fact["tool_name"],
            ordinal=fact.get("ordinal"),
            total=fact.get("total"),
            phase=fact["phase"],
            timestamp=fact["timestamp"],
            disposition=fact.get("disposition"),
            arguments_json=fact.get("arguments_json"),
            facts=fact.get("facts", ()),
            artifact_refs=fact.get("artifact_refs", ()),
            error_code=fact.get("error_code"),
            validation_reason=fact.get("validation_reason"),
            validation_path=fact.get("validation_path"),
            tool_execution_id=fact.get("tool_execution_id"),
        )
        # Identity first, content follows: clients migrate the prepared item
        # and its cached content before the stable-id upsert lands. The local
        # stream entries migrate the same way so no abandoned prepared row
        # survives server-side.
        for rekey in state.projector.drain_rekeys():
            self._emit_activity(sid, state, "activity_rekey", rekey)
            self._rekey_activity_entry(state, rekey)
        if item is not None:
            self._apply_activities(sid, state, [item])

    @staticmethod
    def _rekey_activity_entry(state, rekey):
        entries = state.activities.entries
        source = entries.pop(rekey["from_activity_id"], None)
        if source is None:
            return
        if rekey["to_activity_id"] in entries:
            state.activities.preview_bytes -= source.content_bytes
            return
        source.item = {**source.item, "activity_id": rekey["to_activity_id"]}
        entries[rekey["to_activity_id"]] = source
        entries.move_to_end(rekey["to_activity_id"])

    def activity_delta(self, sid, activity_id, delta):
        """Publish bounded projected content for one activity (P3/P4 seam).

        D12 ordering: the safe fragment is durably committed before it is
        broadcast. A failed persistence never claims durability — the frame
        goes out with ``availability="unsaved"`` and the drop counter records
        the failure for diagnosis (P07.2).
        """
        state = self.state(sid)
        if state.activities.append_delta(activity_id, delta):
            entry = state.activities.entries.get(activity_id)
            persisted = self._persist_activity_delta(
                sid, state, activity_id, delta, entry.item["kind"] if entry is not None else "model"
            )
            if persisted and entry is not None:
                # The durable read path is a JSON content endpoint, so it
                # addresses the item's content_ref — never the binary
                # artifact preview_ref.
                entry.item["content_ref"] = self._activity_content_ref(sid, activity_id)
            self._emit_activity(
                sid,
                state,
                "activity_delta",
                {
                    "activity_id": activity_id,
                    "revision": entry.item["revision"] if entry is not None else 1,
                    "delta": delta,
                    "availability": "committed" if persisted else "unsaved",
                },
            )

    def _persist_activity_delta(self, sid, state, activity_id, delta, kind) -> bool:
        index = self._timeline_index()
        if index is None:
            return False
        try:
            entry = state.activities.entries.get(activity_id)
            index.persist_activity_content(
                sid,
                activity_id,
                delta,
                kind=kind,
                revision=entry.item["revision"] if entry is not None else 1,
            )
            return True
        except Exception:
            # Failure must be diagnosable, never a silent fake success.
            state.activities.count_drop("content_persist_failed")
            return False

    def activity_content_reset(self, sid, activity_id, reason):
        state = self.state(sid)
        entry = state.activities.entries.get(activity_id)
        if entry is not None:
            state.activities.preview_bytes -= entry.content_bytes
            entry.content = ""
            entry.content_bytes = 0
            entry.item["availability"] = "evicted"
        self._emit_activity(
            sid, state, "activity_content_reset", {"activity_id": activity_id, "reason": reason}
        )

    def pull_activities(self, sid, epoch, after):
        state = self.state(sid)
        stream = state.activities
        if (
            epoch != stream.epoch
            or after > stream.sequence
            or after < (stream.frames[0][0]["sequence"] - 1 if stream.frames else stream.sequence)
        ):
            state.resyncs += 1
            return [
                {
                    "type": "resync_required",
                    "stream_epoch": stream.epoch,
                    "sequence": stream.sequence,
                    "workspace_id": self.workspace_id,
                    "session_id": sid,
                    "protocol_version": 1,
                    "agent_run_id": None,
                    "message_id": None,
                    "payload": {"reason": "cursor_expired"},
                }
            ]
        frames, used = [], 0
        for frame, size in stream.frames:
            if frame["sequence"] <= after:
                continue
            if used + size > ACTIVITY_DELIVERY_BYTES:
                break
            used += size
            frames.append(frame)
        return frames

    def event(self, sid, event, *, node_identity=None):
        state = self.state(sid)
        self._project_event(sid, state, event, node_identity=node_identity)
        if event.type == "turn.started":
            entry = self.journal.interactions.by_turn(self.workspace_id, event.turn_id)
            state.run_id = (
                entry["agent_run_id"] if entry else self.manager.interactions.active_run(sid)
            )
            state.next_position = self.manager.require_session(sid).conversation_position + 1
            self.emit(sid, "run_state", {"status": "running"})
        elif event.type == "text.delta":
            text = event.payload.get("text")
            if not isinstance(text, str) or not text:
                return
            if state.draft is None:
                position = (
                    state.next_position
                    or self.manager.require_session(sid).conversation_position + 1
                )
                message_id = reply_id(sid, position)
                state.draft = {
                    "message_id": message_id,
                    "position": position,
                    "revision": 0,
                    "text": "",
                    "bytes": 0,
                }
                self.emit(sid, "reply_started", {}, message_id=message_id)
            draft = state.draft
            if draft["bytes"] + len(text.encode()) > DRAFT_BYTES:
                # AgentLoop also bounds candidates; this handles projection resets safely.
                self.emit(sid, "resync_required", {"reason": "draft_limit"})
                state.draft = None
                return
            # Chunk by characters conservatively so every UTF-8 piece is <=4 KiB.
            for index in range(0, len(text), FRAME_TEXT_BYTES // 4):
                chunk = text[index : index + FRAME_TEXT_BYTES // 4]
                draft["text"] += chunk
                draft["bytes"] += len(chunk.encode())
                draft["revision"] += 1
                self.emit(sid, "reply_delta", {"text": chunk}, message_id=draft["message_id"])
        else:
            self._committed(sid, state)
            if event.type == "status.changed" and event.payload.get("status") == "response_reset":
                old = state.draft["message_id"] if state.draft else None
                state.draft = None
                self.emit(sid, "run_state", {"status": "response_reset", "discard_message_id": old})
            elif event.type == "turn.completed":
                old = state.draft["message_id"] if state.draft else None
                state.draft = None
                self.emit(
                    sid,
                    "run_state",
                    {"status": event.payload.get("finish_reason"), "discard_message_id": old},
                )
                state.run_id = None
            elif event.type.startswith("tool."):
                self.emit(sid, "run_state", {"status": "tool_activity"})

    def _committed(self, sid, state):
        if state.draft is None:
            state.next_position = self.manager.require_session(sid).conversation_position + 1
            return
        draft = state.draft
        rows = self.journal.chat_timeline.page(
            self.workspace_id, sid, before=draft["position"] + 1, limit=1
        )
        if rows and rows[0]["position"] == draft["position"] and rows[0]["role"] == "assistant":
            self.emit(
                sid,
                "reply_committed",
                {"record_id": rows[0]["record_id"], "item_id": draft["message_id"], "revision": 1},
                message_id=draft["message_id"],
            )
            state.draft = None
            state.next_position = self.manager.require_session(sid).conversation_position + 1

    def snapshot(self, sid, *, activity_mode=False):
        state = self.state(sid)
        # The whole projection is read on Core without an await.
        session = session_wire(self.manager.require_session(sid))
        session["metadata"] = self.manager.api.get_session_metadata(sid)
        execution = getattr(self.manager, "execution", None)
        payload = {
            "session": session,
            "settings": self.manager.settings.view(sid),
            "stream_epoch": state.epoch,
            "sequence": state.sequence,
            "draft": dict(state.draft) if state.draft else None,
            "timeline": self.manager.timeline.page(sid),
            "queue": self.manager.interactions.queue(sid),
            "stages": list(state.stages.values()),
            "execution": execution.build(sid) if execution is not None else None,
            "event_cursor": self.journal.latest_application_event_cursor(self.workspace_id),
        }
        if activity_mode:
            # Reconcile against durable owner facts before publishing: an item
            # whose run/planning operation already ended must never survive a
            # snapshot as running (D11/S3.6).
            self._reconcile_activities(sid, state)
            # Self-consistent activity cursor; clients subscribe with these.
            payload["activities"] = state.activities.snapshot_items()
            payload["activity_epoch"] = state.activities.epoch
            payload["activity_sequence"] = state.activities.sequence
        return payload

    _RUN_TERMINAL_ACTIVITY = {
        "completed": "succeeded",
        "failed": "failed",
        "cancelled": "cancelled",
        "superseded": "cancelled",
    }
    _OPERATION_TERMINAL_ACTIVITY = {
        "succeeded": "succeeded",
        "failed": "failed",
        "cancelled": "cancelled",
        "expired": "cancelled",
    }

    def _reconcile_activities(self, sid, state):
        """Close in-memory activities whose durable owner is already terminal.

        Bounded, read-only and failure-tolerant: a missing owner fact simply
        leaves the item alone instead of guessing an outcome.
        """

        if not state.activities.entries:
            return
        operations = None
        for entry in list(state.activities.entries.values()):
            if entry.terminal:
                continue
            item = entry.item
            identity = item.get("identity") or {}
            run_id = identity.get("workflow_run_id")
            operation_id = identity.get("planning_operation_id")
            target = None
            ended = None
            try:
                if isinstance(run_id, str) and run_id:
                    run = self.journal.workflows.get_run(self.workspace_id, run_id)
                    if run is not None and run.status.terminal:
                        target = self._RUN_TERMINAL_ACTIVITY.get(run.status.value, "failed")
                        ended = run.completed_at.isoformat() if run.completed_at else None
                elif isinstance(operation_id, str) and operation_id:
                    planning = getattr(self.manager, "planning", None)
                    if planning is not None:
                        if operations is None:
                            operations = {
                                operation.planning_operation_id: operation
                                for operation in planning.repo.operations(self.workspace_id, sid)
                            }
                        operation = operations.get(operation_id)
                        if operation is not None and operation.status in (
                            self._OPERATION_TERMINAL_ACTIVITY
                        ):
                            target = self._OPERATION_TERMINAL_ACTIVITY[operation.status]
                            ended = operation.updated_at.isoformat()
            except Exception:
                continue
            if target is None:
                continue
            stamp = ended or utc_now().isoformat()
            state.activities.upsert(
                {
                    **item,
                    "state": target,
                    "revision": item["revision"] + 1,
                    "updated_at": stamp,
                    "ended_at": stamp,
                }
            )

    def subscribe(self, sid, loop):
        state = self.state(sid)
        if getattr(self, "host_subscriber_count", lambda: self.subscribers)() >= HOST_SUBSCRIBERS:
            raise ApplicationError(ApplicationErrorCode.BUSY, "Host subscription limit reached")
        token, queue = state.hub.subscribe(loop)
        state.subscribers += 1
        self.subscribers += 1
        return token, queue

    def unsubscribe(self, sid, token):
        state = self.states.get(sid)
        if state and token in state.hub._subscribers:
            state.hub.unsubscribe(token)
            state.subscribers -= 1
            self.subscribers -= 1

    def pull(self, sid, epoch, after):
        state = self.state(sid)
        if (
            epoch != state.epoch
            or after > state.sequence
            or after < (state.frames[0][0]["sequence"] - 1 if state.frames else state.sequence)
        ):
            state.resyncs += 1
            return [
                {
                    "type": "resync_required",
                    "stream_epoch": state.epoch,
                    "sequence": state.sequence,
                    "workspace_id": self.workspace_id,
                    "session_id": sid,
                    "protocol_version": 1,
                    "agent_run_id": None,
                    "message_id": None,
                    "payload": {"reason": "cursor_expired"},
                }
            ]
        frames, used = [], 0
        for frame, size in state.frames:
            if frame["sequence"] <= after:
                continue
            if used + size > DELIVERY_BYTES:
                break
            used += size
            frames.append(frame)
        return frames


class SessionReasoningObserver:
    """Forwards one session's runtime reasoning fragments to its activity stream.

    Identity binding happens here (composition), so the runtime only ever
    passes typed facts (turn, attempt, projected fragment).
    """

    def __init__(self, streams, workspace_id: str, session_id: str) -> None:
        self.streams = streams
        self.workspace_id = workspace_id
        self.session_id = session_id

    def reasoning_delta(self, *, turn_id: str, attempt_ordinal: int, fragment: str) -> None:
        self.streams.activity_delta(
            self.session_id, f"act_model_{turn_id}_{attempt_ordinal}", fragment
        )

    def tool_observation(self, **fact) -> None:
        self.streams.tool_observation(self.session_id, fact)

    def tool_output(self, *, call_id: str, text: str) -> None:
        state = self.streams.state(self.session_id)
        activity_id = state.projector.tool_activity_id_for_call(call_id)
        self.streams.activity_delta(self.session_id, activity_id, text)
