"""Safe, bounded Chat history projections. No history is written here."""

from datetime import UTC, datetime

from morrow.core.activity import TERMINAL_ACTIVITY_STATES, stable_tool_activity_id
from morrow.core.application import ApplicationError, ApplicationErrorCode

MAX_TOOL_FACTS = 128

_DISPOSITION_STATES = {
    "succeeded": "succeeded",
    "failed": "failed",
    "denied": "cancelled",
    "cancelled": "cancelled",
    "interrupted": "cancelled",
    "unknown": "unknown",
}


def reply_id(origin_session_id, position):
    return f"reply:{origin_session_id}:{position}"


class TimelineService:
    def __init__(self, manager, journal, workspace_id, *, result_projector_factory=None):
        self.manager = manager
        self.journal = journal
        self.workspace_id = workspace_id
        self.repository = journal.chat_timeline
        # Built on first index use: the chat-result projection needs the
        # Artifact store and the Workflow query projection, both of which are
        # wired after this service in server composition.
        self.result_projector_factory = result_projector_factory
        self._index = None

    @property
    def index(self):
        """The durable display index (P06): real TimelineSinkPort/Notifier owner.

        Built lazily so lightweight test doubles without a journal-backed
        chat_timeline repository keep working.
        """

        if self._index is None:
            from morrow.application.timeline_index import TimelineIndexService

            projector = (
                self.result_projector_factory()
                if self.result_projector_factory is not None
                else None
            )
            self._index = TimelineIndexService(
                self.journal, self.workspace_id, result_projector=projector
            )
        return self._index

    def page(self, session_id, *, before=None, limit=50, after_position=None):
        """Return a bounded page from the current durable timeline index."""

        return self.index.snapshot_page(
            session_id, before=before, limit=limit, after_position=after_position
        )

    def tool_activities(self, session_id, *, limit=MAX_TOOL_FACTS):
        """Recover durable tool activities for the trajectory (P2.4).

        Read-only, bounded and content-free: skeletons are rebuilt from the
        durable owner's execution facts; transient content is marked
        unavailable instead of being fabricated.
        """
        self.manager.require_session(session_id)
        if not isinstance(limit, int) or not 1 <= limit <= MAX_TOOL_FACTS:
            raise ApplicationError(ApplicationErrorCode.INVALID, "Invalid activity limit")
        facts = self.repository.tool_execution_facts(self.workspace_id, session_id, limit=limit)
        items = []
        for fact in facts:
            state = self._activity_state(fact["state"], fact["disposition"])
            started = self._iso(fact["created_at_unix"])
            ended = self._iso(fact["closed_at_unix"]) if state in TERMINAL_ACTIVITY_STATES else None
            payload = {
                "kind": "tool",
                "tool_name": fact["tool_name"],
                "call_id": fact["call_id"],
                "tool_execution_id": fact["tool_execution_id"],
                "ordinal": fact["ordinal"],
                "total": None,
                "exit_code": None,
            }
            validation = fact.get("validation") or {}
            for key in ("validation_reason", "validation_path"):
                value = validation.get(key)
                if isinstance(value, str) and value:
                    payload[key] = value
            stored_error_code = fact.get("error_code") or validation.get("error_code")
            if isinstance(stored_error_code, str) and stored_error_code:
                payload["error_code"] = stored_error_code
            items.append(
                {
                    "schema_version": 1,
                    "activity_id": stable_tool_activity_id(fact["tool_execution_id"]),
                    "revision": 1,
                    "kind": "tool",
                    "state": state,
                    "origin": "tool_executor",
                    "identity": {
                        "workspace_id": self.workspace_id,
                        "root_session_id": session_id,
                        "source_session_id": session_id,
                        "agent_run_id": fact["agent_run_id"],
                        "turn_id": fact["turn_id"],
                        "tool_execution_id": fact["tool_execution_id"],
                        "call_id": fact["call_id"],
                    },
                    "payload": payload,
                    "started_at": started,
                    "updated_at": ended or started,
                    "ended_at": ended,
                    "last_activity_at": None,
                    "safe_title": fact["tool_name"],
                    "safe_summary": None,
                    "preview_ref": None,
                    "content_ref": None,
                    "truncated": False,
                    "availability": "unsaved",
                }
            )
        return {"items": items, "truncated": len(items) >= limit}

    @staticmethod
    def _activity_state(state, disposition):
        if state == "closed":
            return _DISPOSITION_STATES.get(disposition, "unknown")
        if state == "awaiting_approval":
            return "waiting"
        if state in {"executing", "handler_completed"}:
            return "running"
        return "preparing"

    @staticmethod
    def _iso(unix):
        if not unix:
            return None
        return datetime.fromtimestamp(int(unix), tz=UTC).isoformat()
