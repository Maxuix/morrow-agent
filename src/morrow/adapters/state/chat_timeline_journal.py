"""Keyset reads over immutable conversation positions, including fork prefixes.

Also hosts the durable display-index repository: the persistent
``chat_timeline_entries`` projection with index-owned ``timeline_position`` and
source-identity dedup, plus bounded safe-content storage. The index writes run
strictly inside the caller's transaction; bodies always stay with their source
owner, so nothing here is a second ConversationLog writer.
"""

import json

from morrow.core.application import ApplicationError, ApplicationErrorCode

INLINE_CONTENT_BYTES = 16 * 1024

_ENTRY_COLUMNS = (
    "workspace_id",
    "root_session_id",
    "timeline_position",
    "item_id",
    "kind",
    "source_kind",
    "source_id",
    "source_session_id",
    "source_position",
    "planning_binding_id",
    "planning_operation_id",
    "workflow_run_id",
    "node_run_id",
    "segment_id",
    "parent_item_id",
    "revision",
    "occurred_at_unix",
    "content_ref",
    "availability",
    "source_state",
    "broadcast_state",
    "created_at_unix",
    "updated_at_unix",
)


class ChatTimelineJournal:
    def __init__(self, backend, get_session):
        self.backend = backend
        self.get_session = get_session
        self.rows_read = 0
        self.index = ChatTimelineIndexRepository(backend, get_session)

    def page(self, workspace_id, session_id, *, before, limit, depth=0):
        if depth > 32:
            raise ApplicationError(
                ApplicationErrorCode.NEEDS_RECOVERY, "Session lineage is invalid"
            )
        session = self.get_session(workspace_id, session_id)
        if session is None:
            raise ApplicationError(ApplicationErrorCode.NOT_FOUND, "Session is missing")
        rows = self.backend.read_all(
            "SELECT record_id,session_id,conversation_position,kind,"
            "CASE WHEN payload_bytes<=? THEN payload_json ELSE NULL END,"
            "json_extract(payload_json,'$.role'),payload_bytes FROM conversation_records "
            "WHERE session_id=? AND conversation_position<? ORDER BY conversation_position DESC LIMIT ?",
            (INLINE_CONTENT_BYTES, session_id, before, limit),
        )
        self.rows_read += len(rows)
        result = [
            dict(
                zip(
                    (
                        "record_id",
                        "origin_session_id",
                        "position",
                        "kind",
                        "payload",
                        "role",
                        "bytes",
                    ),
                    row,
                    strict=True,
                )
            )
            for row in rows
        ]
        for item in result:
            item["payload"] = json.loads(item["payload"]) if item["payload"] else None
        if len(result) < limit and session.parent_session_id:
            result.extend(
                self.page(
                    workspace_id,
                    session.parent_session_id,
                    before=min(before, session.parent_cut_position + 1),
                    limit=limit - len(result),
                    depth=depth + 1,
                )
            )
        return result

    def source(self, workspace_id, record):
        sid, position = record["origin_session_id"], record["position"]
        count = self.backend.read_one(
            "SELECT COUNT(*) FROM conversation_records WHERE session_id=? AND conversation_position<=? AND CASE WHEN json_valid(payload_json) THEN json_extract(payload_json,'$.role')='user' ELSE 0 END",
            (sid, position),
        )[0]
        turn = (
            self.backend.read_one(
                "SELECT turn_id,task_run_id,client_message_id FROM turns WHERE session_id=? ORDER BY rowid LIMIT 1 OFFSET ?",
                (sid, max(0, count - 1)),
            )
            if count
            else None
        )
        source = {"origin_session_id": sid, "record_id": record["record_id"]}
        if turn:
            source.update(turn_id=turn[0], task_run_id=turn[1], client_message_id=turn[2])
            run = self.backend.read_one(
                "SELECT agent_run_id FROM agent_runs WHERE turn_id=? ORDER BY rowid DESC LIMIT 1",
                (turn[0],),
            )
            source["agent_run_id"] = run[0] if run else None
        return source

    def content(self, workspace_id, session_id, record_id):
        row = self.backend.read_one(
            "SELECT session_id,conversation_position,payload_json FROM conversation_records WHERE record_id=?",
            (record_id,),
        )
        if row is None:
            raise ApplicationError(ApplicationErrorCode.NOT_FOUND, "Record is missing")
        sid = session_id
        cutoff = None
        for _ in range(33):
            session = self.get_session(workspace_id, sid)
            if session is None:
                break
            if sid == row[0] and (cutoff is None or row[1] <= cutoff):
                payload = json.loads(row[2])
                if payload.get("role") not in {"user", "assistant"}:
                    break
                return {
                    "record_id": record_id,
                    "content": payload.get("content") or "",
                    **(
                        {"attachments": payload["attachments"]}
                        if payload.get("attachments")
                        else {}
                    ),
                }
            if session.parent_session_id is None:
                break
            cutoff = min(cutoff or session.parent_cut_position, session.parent_cut_position)
            sid = session.parent_session_id
        raise ApplicationError(ApplicationErrorCode.NOT_FOUND, "Record is outside this timeline")

    def history_bytes(self, workspace_id, sid, cutoff=None, depth=0):
        if depth > 32:
            raise ApplicationError(
                ApplicationErrorCode.NEEDS_RECOVERY, "Session lineage is invalid"
            )
        session = self.get_session(workspace_id, sid)
        value = self.backend.read_one(
            "SELECT COALESCE(SUM(payload_bytes),0) FROM conversation_records WHERE session_id=? AND conversation_position<=?",
            (sid, cutoff if cutoff is not None else session.conversation_position),
        )[0]
        if session.parent_session_id:
            value += self.history_bytes(
                workspace_id,
                session.parent_session_id,
                min(cutoff or session.parent_cut_position, session.parent_cut_position),
                depth + 1,
            )
        return value

    def tool_execution_facts(self, workspace_id, session_id, *, limit=128):
        """Read-only durable tool facts for activity recovery (P2.4).

        Returns bounded per-execution identity and lifecycle facts; never
        intent bodies or result content. The stored result envelope is read
        only to project its value-free failure diagnostics (error code and
        the stable validation reason/field path) onto the recovery view.
        """
        rows = self.backend.read_all(
            "SELECT tool_execution_id, call_id, tool_name, state, disposition, ordinal, "
            "agent_run_id, turn_id, approval_id, created_at_unix, closed_at_unix, "
            "error_code, result_envelope_json "
            "FROM tool_executions WHERE workspace_id=? AND session_id=? "
            "ORDER BY created_at_unix ASC, ordinal ASC, tool_execution_id ASC LIMIT ?",
            (workspace_id, session_id, int(limit)),
        )
        facts = []
        for row in rows:
            fact = dict(
                zip(
                    (
                        "tool_execution_id",
                        "call_id",
                        "tool_name",
                        "state",
                        "disposition",
                        "ordinal",
                        "agent_run_id",
                        "turn_id",
                        "approval_id",
                        "created_at_unix",
                        "closed_at_unix",
                        "error_code",
                        "result_envelope_json",
                    ),
                    row,
                    strict=True,
                )
            )
            fact["validation"] = _validation_diagnostics(fact.pop("result_envelope_json"))
            facts.append(fact)
        return facts


def _validation_diagnostics(result_envelope_json):
    """Project value-free failure diagnostics from one stored result envelope.

    Reads only the bounded error code, the stable validation reason and the
    first field path; tool output content is never surfaced here.
    """

    if not result_envelope_json:
        return None
    try:
        envelope = json.loads(result_envelope_json)
    except (TypeError, ValueError):
        return None
    if not isinstance(envelope, dict):
        return None
    reason = envelope.get("error_reason")
    diagnostics = envelope.get("validation_diagnostics")
    first = diagnostics[0] if isinstance(diagnostics, list) and diagnostics else None
    first_path = first.get("path") if isinstance(first, dict) else None
    first_type = first.get("type") if isinstance(first, dict) else None
    return {
        "error_code": envelope.get("error_code"),
        "validation_reason": reason if isinstance(reason, str) and reason else None,
        "validation_path": (first_path if isinstance(first_path, str) and first_path else None),
        "validation_type": first_type if isinstance(first_type, str) and first_type else None,
    }


class ChatTimelineIndexRepository:
    """Durable display index over the unified root-session trajectory.

    All writes go through the caller's open transaction via the shared backend
    executor: a rolled-back source fact can never leave an index row behind,
    and no method ever commits. ``timeline_position`` is assigned here from a
    per-(workspace, root session) counter; source identity dedup is enforced by
    a unique index, so replaying a source can never duplicate an entry.
    """

    def __init__(self, backend, get_session):
        self.backend = backend
        self.get_session = get_session

    # --- writes (active transaction required) ---

    def _executor(self):
        return self.backend.executor()

    def next_position(self, workspace_id, root_session_id) -> int:
        row = self.backend.read_one(
            "SELECT COALESCE(MAX(timeline_position)+1,0) FROM chat_timeline_entries "
            "WHERE workspace_id=? AND root_session_id=?",
            (workspace_id, root_session_id),
        )
        return int(row[0])

    def get_by_source(self, workspace_id, root_session_id, source_kind, source_id):
        row = self.backend.read_one(
            f"SELECT {','.join(_ENTRY_COLUMNS)} FROM chat_timeline_entries "
            "WHERE workspace_id=? AND root_session_id=? AND source_kind=? AND source_id=?",
            (workspace_id, root_session_id, source_kind, source_id),
        )
        return _entry_row(row)

    def insert_entry(self, entry) -> None:
        self._executor().execute(
            f"INSERT INTO chat_timeline_entries ({','.join(_ENTRY_COLUMNS)}) "
            f"VALUES ({','.join('?' * len(_ENTRY_COLUMNS))})",
            tuple(entry.get(column) for column in _ENTRY_COLUMNS),
        )

    def update_entry(
        self,
        workspace_id,
        root_session_id,
        timeline_position,
        *,
        item_id,
        revision,
        occurred_at_unix,
        updated_at_unix,
        source_state,
        content_ref=None,
        availability=None,
    ) -> None:
        self._executor().execute(
            "UPDATE chat_timeline_entries SET item_id=?, revision=?, occurred_at_unix=?, "
            "updated_at_unix=?, source_state=?, content_ref=COALESCE(?,content_ref), "
            "availability=COALESCE(?,availability) "
            "WHERE workspace_id=? AND root_session_id=? AND timeline_position=?",
            (
                item_id,
                revision,
                occurred_at_unix,
                updated_at_unix,
                source_state,
                content_ref,
                availability,
                workspace_id,
                root_session_id,
                timeline_position,
            ),
        )

    def set_source_state(
        self, workspace_id, root_session_id, timeline_position, source_state
    ) -> None:
        self._executor().execute(
            "UPDATE chat_timeline_entries SET source_state=? "
            "WHERE workspace_id=? AND root_session_id=? AND timeline_position=?",
            (source_state, workspace_id, root_session_id, timeline_position),
        )

    def fingerprints(self, workspace_id, root_session_id, source_kind, source_ids):
        """Batched change-detection map: source_id → (revision, source_state)."""

        if not source_ids:
            return {}
        marks = ",".join("?" * len(source_ids))
        rows = self.backend.read_all(
            "SELECT source_id, revision, source_state FROM chat_timeline_entries "
            "WHERE workspace_id=? AND root_session_id=? AND source_kind=? "
            f"AND source_id IN ({marks})",
            (workspace_id, root_session_id, source_kind, *source_ids),
        )
        return {row[0]: (row[1], row[2]) for row in rows}

    def source_watermark(self, workspace_id, root_session_id, source_kind, source_session_id):
        """Highest indexed source_position of one append-only source stream."""

        row = self.backend.read_one(
            "SELECT MAX(source_position) FROM chat_timeline_entries "
            "WHERE workspace_id=? AND root_session_id=? AND source_kind=? AND source_session_id=?",
            (workspace_id, root_session_id, source_kind, source_session_id),
        )
        return int(row[0]) if row and row[0] is not None else 0

    # --- reads ---

    def page(self, workspace_id, root_session_id, *, before_position, limit):
        rows = self.backend.read_all(
            f"SELECT {','.join(_ENTRY_COLUMNS)} FROM chat_timeline_entries "
            "WHERE workspace_id=? AND root_session_id=? AND timeline_position<? "
            "ORDER BY timeline_position DESC LIMIT ?",
            (workspace_id, root_session_id, before_position, limit),
        )
        return [row for row in (_entry_row(row) for row in rows) if row is not None]

    def after(self, workspace_id, root_session_id, after_position, *, limit):
        rows = self.backend.read_all(
            f"SELECT {','.join(_ENTRY_COLUMNS)} FROM chat_timeline_entries "
            "WHERE workspace_id=? AND root_session_id=? AND timeline_position>? "
            "ORDER BY timeline_position ASC LIMIT ?",
            (workspace_id, root_session_id, after_position, limit),
        )
        return [row for row in (_entry_row(row) for row in rows) if row is not None]

    # --- post-commit outbox (P07) ---

    def pending_broadcasts(self, workspace_id, root_session_id, *, limit):
        rows = self.backend.read_all(
            f"SELECT {','.join(_ENTRY_COLUMNS)} FROM chat_timeline_entries "
            "WHERE workspace_id=? AND root_session_id=? AND broadcast_state='pending' "
            "ORDER BY timeline_position ASC LIMIT ?",
            (workspace_id, root_session_id, limit),
        )
        return [row for row in (_entry_row(row) for row in rows) if row is not None]

    def mark_broadcast(self, workspace_id, root_session_id, timeline_position) -> None:
        self._executor().execute(
            "UPDATE chat_timeline_entries SET broadcast_state='done' "
            "WHERE workspace_id=? AND root_session_id=? AND timeline_position=?",
            (workspace_id, root_session_id, timeline_position),
        )

    # --- bounded safe activity content (P07) ---

    def put_content(self, record) -> None:
        self._executor().execute(
            "INSERT OR REPLACE INTO chat_activity_content ("
            "content_id,workspace_id,session_id,activity_id,revision,kind,body,"
            "committed_offset,total_length,availability,created_at_unix,updated_at_unix) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                record["content_id"],
                record["workspace_id"],
                record["session_id"],
                record["activity_id"],
                record["revision"],
                record["kind"],
                record["body"],
                record["committed_offset"],
                record.get("total_length"),
                record["availability"],
                record["created_at_unix"],
                record["updated_at_unix"],
            ),
        )

    def latest_content(self, workspace_id, session_id, activity_id):
        row = self.backend.read_one(
            "SELECT content_id,activity_id,revision,kind,body,committed_offset,total_length,"
            "availability,created_at_unix,updated_at_unix FROM chat_activity_content "
            "WHERE workspace_id=? AND session_id=? AND activity_id=? "
            "ORDER BY revision DESC LIMIT 1",
            (workspace_id, session_id, activity_id),
        )
        return _content_row(row)

    def find_content_by_activity(self, workspace_id, activity_id):
        """Locate one activity's durable content across owning sessions."""

        row = self.backend.read_one(
            "SELECT content_id,session_id,activity_id,revision,kind,body,committed_offset,"
            "total_length,availability,created_at_unix,updated_at_unix FROM chat_activity_content "
            "WHERE workspace_id=? AND activity_id=? ORDER BY revision DESC LIMIT 1",
            (workspace_id, activity_id),
        )
        if row is None:
            return None
        return dict(
            zip(
                (
                    "content_id",
                    "session_id",
                    "activity_id",
                    "revision",
                    "kind",
                    "body",
                    "committed_offset",
                    "total_length",
                    "availability",
                    "created_at_unix",
                    "updated_at_unix",
                ),
                row,
                strict=True,
            )
        )

    def content_revisions(self, workspace_id, session_id, activity_ids):
        """Latest durable revision per activity id (bounded recovery reads)."""

        if not activity_ids:
            return {}
        marks = ",".join("?" * len(activity_ids))
        rows = self.backend.read_all(
            "SELECT activity_id,MAX(revision) FROM chat_activity_content "
            f"WHERE workspace_id=? AND session_id=? AND activity_id IN ({marks}) "
            "GROUP BY activity_id",
            (workspace_id, session_id, *activity_ids),
        )
        return {row[0]: row[1] for row in rows}


def _entry_row(row):
    if row is None:
        return None
    return dict(zip(_ENTRY_COLUMNS, row, strict=True))


def _content_row(row):
    if row is None:
        return None
    keys = (
        "content_id",
        "activity_id",
        "revision",
        "kind",
        "body",
        "committed_offset",
        "total_length",
        "availability",
        "created_at_unix",
        "updated_at_unix",
    )
    return dict(zip(keys, row, strict=True))
