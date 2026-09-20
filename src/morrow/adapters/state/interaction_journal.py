"""Bounded admission repository sharing the operational transaction backend."""

import json

from morrow.core.application import ApplicationError, ApplicationErrorCode
from morrow.core.interactions import InteractionStatus

_COLUMNS = (
    "position,interaction_id,workspace_id,session_id,client_message_id,request_digest,"
    "binding_digest,request_json,binding_json,status,revision,turn_id,agent_run_id,user_record_id,reason"
)
_KEYS = _COLUMNS.split(",")


def _entry(row):
    if row is None:
        return None
    result = dict(zip(_KEYS, row, strict=True))
    result["request"] = json.loads(result.pop("request_json"))
    result["binding"] = json.loads(result.pop("binding_json"))
    return result


class InteractionJournal:
    def __init__(self, backend):
        self.backend = backend

    def get(self, workspace_id, session_id, key):
        return _entry(
            self.backend.read_one(
                f"SELECT {_COLUMNS} FROM chat_interactions WHERE workspace_id=? AND session_id=? AND client_message_id=?",
                (workspace_id, session_id, key),
            )
        )

    def pending(self, workspace_id, session_id):
        return tuple(
            _entry(row)
            for row in self.backend.read_all(
                f"SELECT {_COLUMNS} FROM chat_interactions WHERE workspace_id=? AND session_id=? AND status IN ('queued','blocked') ORDER BY position LIMIT 128",
                (workspace_id, session_id),
            )
        )

    def insert(
        self,
        workspace_id,
        session_id,
        request,
        binding,
        *,
        identity,
        request_digest,
        binding_digest,
    ):
        def work():
            count = self.backend.read_one(
                "SELECT COUNT(*) FROM chat_interactions WHERE workspace_id=? AND status IN ('queued','blocked')",
                (workspace_id,),
            )[0]
            if count >= 128 or len(self.pending(workspace_id, session_id)) >= 32:
                raise ApplicationError(ApplicationErrorCode.BUSY, "Interaction queue is full")
            self.backend.executor().execute(
                "INSERT INTO chat_interactions(interaction_id,workspace_id,session_id,client_message_id,request_digest,binding_digest,request_json,binding_json,status) VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    identity,
                    workspace_id,
                    session_id,
                    request.client_message_id,
                    request_digest,
                    binding_digest,
                    request.model_dump_json(),
                    json.dumps(binding),
                    "queued",
                ),
            )
            return self.get(workspace_id, session_id, request.client_message_id)

        return self.backend.transact(work)

    def update(self, entry, *, status: InteractionStatus | str, reason=None):
        status = InteractionStatus(status)

        def work():
            self.backend.executor().execute(
                "UPDATE chat_interactions SET status=?,reason=?,revision=revision+1 WHERE interaction_id=?",
                (status.value, reason, entry["interaction_id"]),
            )

        self.backend.transact(work)

    def bind_turn(self, workspace_id, session_id, key, turn_id, agent_run_id):
        entry = self.get(workspace_id, session_id, key)
        if entry is None:
            return  # Existing CLI admissions have no separate queue row.
        if entry["status"] != "queued":
            raise ApplicationError(ApplicationErrorCode.CONFLICT, "Input is no longer queued")
        row = self.backend.read_one(
            "SELECT record_id FROM conversation_records WHERE session_id=? AND json_extract(payload_json,'$.role')='user' ORDER BY conversation_position DESC LIMIT 1",
            (session_id,),
        )
        self.backend.executor().execute(
            "UPDATE chat_interactions SET status='consumed',revision=revision+1,turn_id=?,agent_run_id=?,user_record_id=? WHERE interaction_id=?",
            (turn_id, agent_run_id, row[0], entry["interaction_id"]),
        )

    def control(self, session_id):
        row = self.backend.read_one(
            "SELECT control_json FROM sessions WHERE session_id=?", (session_id,)
        )
        if not row or not row[0]:
            return {"paused": False, "revision": 1}
        try:
            value = json.loads(row[0])
            if not isinstance(value, dict):
                raise ValueError("control payload must be an object")
            return {
                "paused": bool(value.get("paused", False)),
                "revision": int(value.get("revision", 1)),
            }
        except (TypeError, ValueError) as exc:
            raise ApplicationError(
                ApplicationErrorCode.UNAVAILABLE, "Session control state is invalid"
            ) from exc

    def pause(self, session_id, paused=True):
        self.backend.transact(lambda: self._set_control(session_id, paused))

    def pause_on_restart(self, workspace_id):
        def work():
            rows = self.backend.executor().execute(
                "SELECT DISTINCT session_id FROM chat_interactions "
                "WHERE workspace_id=? AND status IN ('queued','consumed','blocked')",
                (workspace_id,),
            )
            for (session_id,) in rows:
                self._set_control(session_id, True)

        self.backend.transact(work)

    def _set_control(self, session_id, paused: bool):
        current = self.control(session_id)
        self.backend.executor().execute(
            "UPDATE sessions SET control_json=? WHERE session_id=?",
            (
                json.dumps(
                    {"paused": bool(paused), "revision": current["revision"] + 1},
                    separators=(",", ":"),
                ),
                session_id,
            ),
        )

    def by_turn(self, workspace_id, turn_id):
        return _entry(
            self.backend.read_one(
                f"SELECT {_COLUMNS} FROM chat_interactions WHERE workspace_id=? AND turn_id=?",
                (workspace_id, turn_id),
            )
        )

    def withdraw(self, entry):
        def work():
            self.update(entry, status="withdrawn")
            # Retain original control evidence; superseded means it cannot be consumed.
            self.backend.executor().execute(
                "UPDATE runtime_control_queue SET status='superseded' WHERE session_id=? AND client_message_id=? AND status='pending'",
                (entry["session_id"], entry["client_message_id"]),
            )

        self.backend.transact(work)

    def terminal_reason(self, entry):
        if entry["user_record_id"] is None:
            return None
        row = self.backend.read_one(
            "SELECT json_extract(r.payload_json,'$.finish_reason') FROM conversation_records r "
            "JOIN conversation_records u ON u.record_id=? "
            "WHERE r.session_id=u.session_id AND r.conversation_position>u.conversation_position "
            "AND (r.kind='terminal' OR json_extract(r.payload_json,'$.role')='user') "
            "ORDER BY r.conversation_position LIMIT 1",
            (entry["user_record_id"],),
        )
        return row[0] if row else None

    def latest(self, workspace_id, session_id):
        return _entry(
            self.backend.read_one(
                f"SELECT {_COLUMNS} FROM chat_interactions WHERE workspace_id=? AND session_id=? ORDER BY position DESC LIMIT 1",
                (workspace_id, session_id),
            )
        )

    def rebind_run(self, entry, agent_run_id):
        self.backend.transact(
            lambda: self.backend.executor().execute(
                "UPDATE chat_interactions SET agent_run_id=?,revision=revision+1 WHERE interaction_id=?",
                (agent_run_id, entry["interaction_id"]),
            )
        )
