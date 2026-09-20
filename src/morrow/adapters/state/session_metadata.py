"""Display metadata, separate from Session lifecycle and ConversationLog."""

import json

from morrow.core.application import ApplicationError, ApplicationErrorCode
from morrow.core.domain import redact_workflow_text

DEFAULT_SESSION_TITLE = "新对话"


def safe_title(text):
    return " ".join(redact_workflow_text(text[:4096])[0].split())[:120]


class SessionMetadataJournal:
    def __init__(self, backend, get_session):
        self.backend = backend
        self.get_session = get_session

    def get(self, workspace_id, session_id):
        if self.get_session(workspace_id, session_id) is None:
            raise ApplicationError(ApplicationErrorCode.NOT_FOUND, "Session is missing")
        row = self.backend.read_one(
            "SELECT metadata_json FROM sessions WHERE session_id=?", (session_id,)
        )
        metadata = {}
        if row and row[0]:
            try:
                parsed = json.loads(row[0])
                if not isinstance(parsed, dict):
                    raise ValueError("metadata payload must be an object")
                metadata = parsed
            except (TypeError, ValueError) as exc:
                raise ApplicationError(
                    ApplicationErrorCode.UNAVAILABLE, "Session metadata is invalid"
                ) from exc
        return {
            "title": metadata.get("title") or DEFAULT_SESSION_TITLE,
            "pinned": bool(metadata.get("pinned", False)),
            "revision": int(metadata.get("revision", 0)),
        }

    def update(self, workspace_id, session_id, *, expected_revision, title=None, pinned=None):
        current = self.get(workspace_id, session_id)
        if current["revision"] != expected_revision:
            raise ApplicationError(
                ApplicationErrorCode.STALE, "Session metadata changed; refresh and retry"
            )
        if title is not None:
            title = safe_title(title)
            if not title:
                raise ApplicationError(ApplicationErrorCode.INVALID, "A Session title is required")
        self.backend.executor().execute(
            "UPDATE sessions SET metadata_json=? WHERE session_id=?",
            (
                json.dumps(
                    {
                        "title": title if title is not None else current["title"],
                        "pinned": bool(current["pinned"] if pinned is None else pinned),
                        "revision": current["revision"] + 1,
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                session_id,
            ),
        )
        return self.get(workspace_id, session_id)

    def default_title(self, workspace_id, session_id, text):
        current = self.get(workspace_id, session_id)
        # Keep the first user Prompt as the title when no explicit title was
        # saved. The legacy short-id fallback is also treated as untitled so
        # existing Sessions become readable on their next interaction.
        legacy = "对话 " + session_id[-8:]
        if current["title"] not in {DEFAULT_SESSION_TITLE, legacy}:
            return
        self.backend.transact(
            lambda: self.backend.executor().execute(
                "UPDATE sessions SET metadata_json=? WHERE session_id=?",
                (
                    json.dumps(
                        {
                            "title": safe_title(text) or DEFAULT_SESSION_TITLE,
                            "pinned": current["pinned"],
                            "revision": current["revision"] + 1,
                        },
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                    session_id,
                ),
            )
        )

    def search(
        self, workspace_id, *, text="", archived=False, offset=0, limit=50, include_execution=False
    ):
        if len(text) > 120 or not 0 <= offset <= 1000000 or not 1 <= limit <= 100:
            raise ApplicationError(ApplicationErrorCode.INVALID, "Invalid Session search")
        # Internal execution containers (isolated workflow node conversations)
        # are filtered in SQL before pagination so cursors and page sizes stay
        # correct; the toggle keeps an explicit entry point for them.
        scope = (
            ""
            if include_execution
            else (
                "AND NOT (EXISTS (SELECT 1 FROM task_runs wt WHERE wt.workspace_id=s.workspace_id "
                "AND wt.session_id=s.session_id AND wt.purpose='workflow_node') "
                "AND NOT EXISTS (SELECT 1 FROM task_runs ut WHERE ut.workspace_id=s.workspace_id "
                "AND ut.session_id=s.session_id AND ut.purpose='user')) "
            )
        )
        rows = self.backend.read_all(
            "SELECT s.session_id FROM sessions s "
            "WHERE s.workspace_id=? AND s.lifecycle=? AND instr(lower(COALESCE(NULLIF(json_extract(s.metadata_json,'$.title'),''),?) || ' ' || s.session_id),lower(?))>0 "
            + scope
            + "ORDER BY COALESCE(json_extract(s.metadata_json,'$.pinned'),0) DESC,s.updated_at_unix DESC,s.session_id LIMIT ? OFFSET ?",
            (
                workspace_id,
                "archived" if archived else "active",
                DEFAULT_SESSION_TITLE,
                text,
                limit + 1,
                offset,
            ),
        )
        return [row[0] for row in rows[:limit]], str(offset + limit) if len(rows) > limit else None
