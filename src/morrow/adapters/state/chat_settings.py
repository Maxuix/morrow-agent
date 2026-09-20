"""Session settings share the Core transaction owner and never write chat history."""

import json

from morrow.core.application import ApplicationError, ApplicationErrorCode
from morrow.core.models import ChatSettings


class ChatSettingsJournal:
    def __init__(self, backend, get_session):
        self.backend, self.get_session = backend, get_session

    def get(self, workspace_id, session_id):
        if self.get_session(workspace_id, session_id) is None:
            raise ApplicationError(ApplicationErrorCode.NOT_FOUND, "Session is missing")
        row = self.backend.read_one(
            "SELECT settings_json FROM sessions WHERE session_id=?",
            (session_id,),
        )
        if not row or not row[0]:
            return ChatSettings(), 0
        try:
            payload = json.loads(row[0])
            if not isinstance(payload, dict):
                raise ValueError
            return ChatSettings.model_validate(payload.get("value", {})), int(
                payload.get("revision", 0)
            )
        except (TypeError, ValueError) as exc:
            raise ApplicationError(
                ApplicationErrorCode.UNAVAILABLE, "Chat settings are invalid"
            ) from exc

    def put(self, workspace_id, session_id, settings, expected_revision):
        if self.get(workspace_id, session_id)[1] != expected_revision:
            raise ApplicationError(ApplicationErrorCode.STALE, "设置已变化，请刷新后重试")
        self.backend.executor().execute(
            "UPDATE sessions SET settings_json=? WHERE session_id=?",
            (
                json.dumps(
                    {"value": settings.model_dump(mode="json"), "revision": expected_revision + 1},
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                session_id,
            ),
        )
        return self.get(workspace_id, session_id)
