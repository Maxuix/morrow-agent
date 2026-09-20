"""Bridge workflow leaf events onto Chat reply streams.

The root session receives bounded, content-free stage frames carrying explicit
execution identity (workflow run, node, leaf session, AgentRun); isolated leaf
sessions keep their own full reply stream, so a sub-session's text is never
merged into the root session's draft or history. Every projection is
synchronous, best-effort and failure-tolerant: a missing session, a busy stream
cache or a disconnected subscriber can never abort workflow execution.

Planning projections run strictly after the caller's commit (P07.3): the
durable ``workflow_planning_events`` row written in the same transaction is the
replay source, so a rollback never projects and a crash between commit and
broadcast is repaired by replaying the events through the shared
``PlanningActivityReducer``.
"""

from collections import OrderedDict

from morrow.application.workflows.observation import stage_payload
from morrow.core.models import utc_now

ROOT_CACHE = 32

_PLANNING_TERMINAL_STATES = {
    "succeeded": "succeeded",
    "failed": "failed",
    "cancelled": "cancelled",
    "expired": "cancelled",
}


def planning_item(
    workspace_id: str,
    session_id: str | None,
    operation_id: str,
    attempt: int,
    *,
    started: str,
    state: str,
    revision: int,
    ended_at: str | None,
    title: str,
) -> dict:
    """One bounded planning activity item; identity carries the operation only."""

    return {
        "schema_version": 1,
        "activity_id": f"act_model_plop_{operation_id}_{attempt}",
        "revision": revision,
        "kind": "model",
        "state": state,
        "origin": "planning_service",
        "identity": {
            "workspace_id": workspace_id,
            "root_session_id": session_id,
            "source_session_id": session_id,
            "planning_operation_id": operation_id,
        },
        "payload": {
            "kind": "model",
            "stage": "awaiting_model",
            "attempt_ordinal": attempt,
            "reasoning_capability": None,
        },
        "started_at": started,
        "updated_at": ended_at or started,
        "ended_at": ended_at,
        "last_activity_at": None,
        "safe_title": title,
        "safe_summary": None,
        "preview_ref": None,
        "content_ref": None,
        "truncated": False,
        "availability": "none",
    }


class PlanningActivityReducer:
    """Pure reducer from durable planning events to bounded activity items.

    The live bridge and the cold-start recovery replay share this reducer so a
    restart rebuilds exactly the same items from the durable planning events —
    no second state machine (D10/D14). Attempts opened without an independent
    terminal fact close as ``unknown`` so a paused or crashed planning request
    never runs as a ghost timer (A11).

    Stage contract (additive, lane B coordination):

    - ``awaiting_model`` opens/refreshes one attempt's wait item;
    - ``model_finished`` closes the attempt with the real model-request
      outcome (``succeeded``/``failed``/``interrupted``/``cancelled``). A user
      interruption lands as ``cancelled``, never
      as a fabricated failure;
    - ``request_outcome`` with ``layer="model_request"`` is an equally valid
      independent end-evidence for the attempt; other layers (candidate
      validation, planning operation) own no wait item and are ignored — the
      operation layer closes through ``operation_terminal``;
    - ``planning_pause`` parks the attempt as non-terminal ``unknown``
      ("已暂停", ended_at stays None → no ghost timer) without burning the
      terminal state, so the resume path can reopen the same activity id;
    - ``planning_resume`` reopens a parked attempt as ``running`` (later
      ``awaiting_model`` notifications with the same id are stale-revision
      drops by design — the resume carries the fresh wait start);
    - ``operation_terminal`` closes open **and** parked attempts.
    """

    _MODEL_OUTCOME_STATES = {
        "succeeded": "succeeded",
        "failed": "failed",
        "interrupted": "cancelled",
        "cancelled": "cancelled",
    }

    def __init__(self, workspace_id: str) -> None:
        self.workspace_id = workspace_id
        self.open_attempts: dict[str, dict[int, str]] = {}
        self.paused_attempts: dict[str, dict[int, str]] = {}
        self.op_sessions: dict[str, str | None] = {}
        self._revisions: dict[str, dict[int, int]] = {}

    # --- revision bookkeeping: one monotonic counter per attempt item ---

    def _next_revision(self, operation_id: str, attempt: int) -> int:
        revisions = self._revisions.setdefault(operation_id, {})
        revisions[attempt] = revisions.get(attempt, 0) + 1
        return revisions[attempt]

    def apply(self, payload: dict) -> list[dict]:
        operation_id = payload.get("operation_id")
        stage = payload.get("stage")
        if not isinstance(operation_id, str) or not operation_id:
            return []
        attempt = payload.get("attempt")
        if not isinstance(attempt, int) or attempt < 1:
            attempt = 1
        session_id = payload.get("session_id")
        if isinstance(session_id, str) and session_id:
            self.op_sessions[operation_id] = session_id
        if stage == "awaiting_model":
            started = payload.get("started_at")
            if not isinstance(started, str) or not started:
                started = utc_now().isoformat()
            # A re-notification (e.g. after a resume) refreshes the wait start
            # and bumps the revision instead of regressing the item.
            self.paused_attempts.get(operation_id, {}).pop(attempt, None)
            self.open_attempts.setdefault(operation_id, {})[attempt] = started
            return [
                planning_item(
                    self.workspace_id,
                    session_id,
                    operation_id,
                    attempt,
                    started=started,
                    state="running",
                    revision=self._next_revision(operation_id, attempt),
                    ended_at=None,
                    title="等待模型响应 · 任务规划",
                )
            ]
        if stage == "model_finished":
            return self._close(
                operation_id,
                attempt,
                state=self._MODEL_OUTCOME_STATES.get(payload.get("status"), "failed"),
                ended_at=payload.get("ended_at"),
                title="任务规划已结束",
            )
        if stage == "request_outcome":
            # Only the model-request layer owns the wait item; validation and
            # operation layers are separate facts with no wait to close.
            if payload.get("layer") != "model_request":
                return []
            return self._close(
                operation_id,
                attempt,
                state=self._MODEL_OUTCOME_STATES.get(payload.get("outcome"), "failed"),
                ended_at=payload.get("ended_at"),
                title="任务规划已结束",
            )
        if stage == "planning_pause":
            open_attempts = self.open_attempts.get(operation_id) or {}
            started = open_attempts.pop(attempt, None)
            if started is None:
                return []
            if not open_attempts:
                self.open_attempts.pop(operation_id, None)
            self.paused_attempts.setdefault(operation_id, {})[attempt] = started
            # Non-terminal unknown: the operation may resume, and the item
            # must not run as a ghost timer while parked.
            return [
                planning_item(
                    self.workspace_id,
                    self.op_sessions.get(operation_id),
                    operation_id,
                    attempt,
                    started=started,
                    state="unknown",
                    revision=self._next_revision(operation_id, attempt),
                    ended_at=None,
                    title="任务规划已暂停",
                )
            ]
        if stage == "planning_resume":
            paused = self.paused_attempts.get(operation_id) or {}
            started = paused.pop(attempt, None)
            if not paused:
                self.paused_attempts.pop(operation_id, None)
            resume_started = payload.get("started_at")
            if not isinstance(resume_started, str) or not resume_started:
                resume_started = started or utc_now().isoformat()
            self.open_attempts.setdefault(operation_id, {})[attempt] = resume_started
            return [
                planning_item(
                    self.workspace_id,
                    self.op_sessions.get(operation_id),
                    operation_id,
                    attempt,
                    started=resume_started,
                    state="running",
                    revision=self._next_revision(operation_id, attempt),
                    ended_at=None,
                    title="等待模型响应 · 任务规划",
                )
            ]
        if stage == "operation_terminal":
            state = _PLANNING_TERMINAL_STATES.get(payload.get("status"), "failed")
            items: list[dict] = []
            for attempt_ordinal in sorted(
                {
                    **self.open_attempts.get(operation_id, {}),
                    **self.paused_attempts.get(operation_id, {}),
                }
            ):
                items.extend(
                    self._close(
                        operation_id,
                        attempt_ordinal,
                        state=state,
                        ended_at=payload.get("ended_at"),
                        title="任务规划已结束",
                    )
                )
            self.open_attempts.pop(operation_id, None)
            self.paused_attempts.pop(operation_id, None)
            return items
        return []

    def close_open_without_evidence(self) -> list[dict]:
        """Close every still-open attempt as ``unknown`` (no terminal fact).

        Parked attempts are already non-terminal ``unknown``; only genuinely
        open (running) attempts need the evidence-free close.
        """

        items: list[dict] = []
        for operation_id, attempts in list(self.open_attempts.items()):
            for attempt in sorted(attempts):
                items.extend(
                    self._close(
                        operation_id,
                        attempt,
                        state="unknown",
                        ended_at=None,
                        title="任务规划中断 · 无独立结束证据",
                    )
                )
            self.open_attempts.pop(operation_id, None)
        return items

    def _close(
        self,
        operation_id,
        attempt,
        *,
        state,
        ended_at,
        title,
    ) -> list[dict]:
        open_attempts = self.open_attempts.get(operation_id) or {}
        paused = self.paused_attempts.get(operation_id) or {}
        started = open_attempts.pop(attempt, None) or paused.pop(attempt, None)
        if started is None:
            return []
        if not open_attempts:
            self.open_attempts.pop(operation_id, None)
        if not paused:
            self.paused_attempts.pop(operation_id, None)
        if state == "unknown":
            ended_at = None
        elif not isinstance(ended_at, str) or not ended_at:
            ended_at = utc_now().isoformat()
        return [
            planning_item(
                self.workspace_id,
                self.op_sessions.get(operation_id),
                operation_id,
                attempt,
                started=started,
                state=state,
                revision=self._next_revision(operation_id, attempt),
                ended_at=ended_at,
                title=title,
            )
        ]


def replay_planning_items(
    journal, workspace_id: str, session_id: str, *, limit_pages: int = 20
) -> list[dict]:
    """Rebuild planning activities from the durable planning events (P07.3/P07.4).

    Cold-start recovery for empty in-memory streams: the durable
    ``workflow_planning_events`` rows replay through the shared reducer, and
    attempts left open without an independent terminal fact close as
    ``unknown`` so no ghost timer survives a restart. Idempotent: consumers
    upsert by (activity_id, revision) with terminal-regression protection.
    """

    try:
        reducer = PlanningActivityReducer(workspace_id)
        planning = journal.workflows.planning
        items: list[dict] = []
        after = 0
        for _page in range(limit_pages):
            events = planning.events(workspace_id, session_id, after=after, limit=100)
            if not events:
                break
            for event in events:
                after = event.pop("sequence", after)
                items.extend(reducer.apply(event))
            if len(events) < 100:
                break
        items.extend(reducer.close_open_without_evidence())
        return items
    except Exception:
        # Recovery is best-effort; durable facts stay the only owners.
        return []


class WorkflowStreamBridge:
    """Implements the Scheduler's ``event_observer`` seam for one workspace."""

    def __init__(self, chat, journal, workspace_id: str) -> None:
        self.chat = chat
        self.journal = journal
        self.workspace_id = workspace_id
        self._roots: OrderedDict[str, str] = OrderedDict()
        # Shared with the cold-start recovery replay: one state machine for the
        # durable planning events (P07.3/P07.4).
        self._planning_reducer = PlanningActivityReducer(workspace_id)

    def node_event(
        self,
        event,
        *,
        workflow_run_id: str,
        node_run_id: str,
        node_id: str,
        session_id: str,
        agent_run_id: str | None,
    ) -> None:
        try:
            self._project(
                event,
                workflow_run_id=workflow_run_id,
                node_run_id=node_run_id,
                node_id=node_id,
                session_id=session_id,
                agent_run_id=agent_run_id,
            )
        except Exception:
            # Projection is best-effort: never aborts the driving leaf.
            return

    def _project(
        self,
        event,
        *,
        workflow_run_id: str,
        node_run_id: str,
        node_id: str,
        session_id: str,
        agent_run_id: str | None,
    ) -> None:
        streams = self.chat.streams
        root = self._root_session(workflow_run_id)
        # The scheduler builds its observer identity before the AgentRun row
        # exists, so ``agent_run_id`` can still be None here; the leaf's own
        # reply stream resolved the durable run id on turn.started (P5.1 will
        # move this into the scheduler identity itself).
        leaf_state = streams.states.get(session_id)
        resolved_agent_run = agent_run_id or (leaf_state.run_id if leaf_state else None)
        # Explicit execution identity for the activity projection (P2.3): the
        # leaf's own stream and, for isolated leaves, the root stream both get
        # node-identified activities. invoking_session shares one stream, so
        # the root==leaf case emits exactly once.
        identity = {
            "workflow_run_id": workflow_run_id,
            "node_run_id": node_run_id,
            "node_id": node_id,
            "agent_run_id": resolved_agent_run,
            "source_session_id": session_id,
            "root_session_id": root or session_id,
        }
        # The leaf keeps its own reply stream. For invoking_session scope the
        # leaf session IS the root session, so its reply stream is the root's;
        # sub-session text is never merged into the root session's draft.
        streams.event(session_id, event, node_identity=identity)
        if root is not None and root != session_id:
            streams.node_activity(root, event, node_identity=identity)
        stage = stage_payload(event)
        if stage is None:
            return
        if root is None:
            return
        # The root session's stage card carries the no-content progress evidence
        # ("等待模型响应", tool activity) that reply frames alone cannot show.
        streams.stage(
            root,
            {
                "workflow_run_id": workflow_run_id,
                "node_run_id": node_run_id,
                "node_id": node_id,
                "agent_run_id": agent_run_id,
                "session_id": session_id,
                **stage,
                "ts": event.timestamp.isoformat() if event.timestamp is not None else None,
            },
        )

    def leaf_reasoning(
        self,
        identity: dict,
        *,
        session_id: str,
        turn_id: str,
        attempt_ordinal: int,
        fragment: str,
    ) -> None:
        """Forward one projected reasoning fragment (P3.2/P3.5).

        The leaf stream keys the model attempt exactly like its projector; an
        isolated leaf also feeds the root stream under the node-scoped id, so
        the root trajectory shows thinking per node. invoking_session shares
        one stream and one id — no double delivery.
        """
        try:
            node_run_id = identity.get("node_run_id")
            scope = node_run_id or turn_id
            streams = self.chat.streams
            streams.activity_delta(session_id, f"act_model_{scope}_{attempt_ordinal}", fragment)
            root = self._root_session(identity["workflow_run_id"])
            if root is not None and root != session_id and node_run_id:
                streams.activity_delta(root, f"act_model_{node_run_id}_{attempt_ordinal}", fragment)
        except Exception:
            # Projection is best-effort: never aborts the driving leaf.
            return

    def leaf_tool_observation(self, identity: dict, session_id: str, fact: dict) -> None:
        """Forward one real tool transition to leaf and root streams (P4.1)."""
        try:
            streams = self.chat.streams
            streams.tool_observation(session_id, fact, node_identity=identity)
            root = self._root_session(identity["workflow_run_id"])
            if root is not None and root != session_id:
                streams.tool_observation(root, fact, node_identity=identity)
        except Exception:
            return

    def steer_expired(self, identity: dict, session_id: str, count: int) -> None:
        """Project the expired-steer receipt (P6.4) onto leaf and root streams."""
        try:
            from morrow.core.models import utc_now

            now = utc_now().isoformat()
            item = {
                "schema_version": 1,
                "activity_id": f"act_control_{identity.get('node_run_id')}_{now}",
                "revision": 1,
                "kind": "control",
                "state": "succeeded",
                "origin": "control_service",
                "identity": {
                    "workspace_id": self.workspace_id,
                    "root_session_id": identity.get("root_session_id") or session_id,
                    "source_session_id": session_id,
                    "workflow_run_id": identity.get("workflow_run_id"),
                    "node_run_id": identity.get("node_run_id"),
                    "node_id": identity.get("node_id"),
                    "agent_run_id": identity.get("agent_run_id"),
                },
                "payload": {"kind": "control", "command": "steer", "receipt": "expired"},
                "started_at": now,
                "updated_at": now,
                "ended_at": now,
                "last_activity_at": None,
                "safe_title": "纠偏已失效 · 节点已完成",
                "safe_summary": f"{count} 条未消费纠偏已标记失效" if count > 1 else None,
                "preview_ref": None,
                "content_ref": None,
                "truncated": False,
                "availability": "none",
            }
            streams = self.chat.streams
            streams.activity_upsert(session_id, item)
            root = self._root_session(identity["workflow_run_id"])
            if root is not None and root != session_id:
                streams.activity_upsert(root, item)
        except Exception:
            return

    def leaf_tool_output(self, identity: dict, session_id: str, call_id: str, text: str) -> None:
        """Forward one redacted output fragment to leaf and root streams."""
        try:
            streams = self.chat.streams
            state = streams.state(session_id)
            streams.activity_delta(
                session_id, state.projector.tool_activity_id_for_call(call_id), text
            )
            root = self._root_session(identity["workflow_run_id"])
            if root is not None and root != session_id:
                root_state = streams.state(root)
                streams.activity_delta(
                    root, root_state.projector.tool_activity_id_for_call(call_id), text
                )
        except Exception:
            return

    def planning_event(self, workspace_id: str, session_id: str, payload: dict) -> None:
        """Project one durable planning event after its transaction commits.

        ``planning_journal.event`` writes the durable event row inside the
        caller's transaction and then calls this listener synchronously. The
        projection itself is deferred via ``after_commit``: a rolled-back
        planning fact never reaches the streams (P07.3), and a crash between
        commit and projection is repaired by replaying the durable events.
        """

        try:
            self.journal.after_commit(
                lambda: self._apply_planning_payload(workspace_id, session_id, payload)
            )
        except Exception:
            # Projection is best-effort: never aborts planning.
            return

    def _apply_planning_payload(self, workspace_id: str, session_id: str, payload: dict) -> None:
        try:
            for item in self._planning_reducer.apply(payload):
                self.chat.streams.activity_upsert(session_id, item)
        except Exception:
            return

    def _root_session(self, workflow_run_id: str) -> str | None:
        cached = self._roots.get(workflow_run_id)
        if cached is not None:
            self._roots.move_to_end(workflow_run_id)
            return cached
        run = self.journal.workflows.get_run(self.workspace_id, workflow_run_id)
        root_task = (
            self.journal.get_task_run(self.workspace_id, run.root_task_run_id)
            if run is not None
            else None
        )
        root = root_task.session_id if root_task is not None else None
        if root is not None:
            self._roots[workflow_run_id] = root
            while len(self._roots) > ROOT_CACHE:
                self._roots.popitem(last=False)
        return root
