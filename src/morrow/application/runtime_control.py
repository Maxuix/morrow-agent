"""Application service for bounded durable steering and follow-up."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from morrow.core.runtime_control import (
    RUNTIME_CONTROL_TEXT_MAX_CHARS,
    RuntimeControlEntry,
    RuntimeControlError,
    RuntimeControlErrorCode,
    RuntimeControlKind,
)


class RuntimeControlService:
    def __init__(
        self,
        journal,
        *,
        workspace_id: str,
        id_source,
        clock: Callable[[], datetime],
    ) -> None:
        self.journal = journal
        self.workspace_id = workspace_id
        self.id_source = id_source
        self.clock = clock

    def enqueue(self, session_id: str, kind: RuntimeControlKind, text: str) -> RuntimeControlEntry:
        if not text.strip() or len(text) > RUNTIME_CONTROL_TEXT_MAX_CHARS:
            raise RuntimeControlError(
                RuntimeControlErrorCode.INVALID,
                "runtime control text must contain 1 to 4096 characters",
            )
        return self.journal.enqueue_runtime_control(
            self.workspace_id,
            session_id=session_id,
            kind=kind,
            client_message_id=self.id_source.new_id("cmsg"),
            text=text,
            created_at=self.clock(),
        )

    def enqueue_steering(self, session_id: str, text: str) -> RuntimeControlEntry:
        return self.enqueue(session_id, RuntimeControlKind.STEER, text)

    def enqueue_follow_up(self, session_id: str, text: str) -> RuntimeControlEntry:
        return self.enqueue(session_id, RuntimeControlKind.FOLLOW_UP, text)

    def peek_steering(self, session_id: str) -> RuntimeControlEntry | None:
        return self.journal.peek_runtime_control(
            self.workspace_id, session_id, kind=RuntimeControlKind.STEER
        )

    def peek_follow_up(self, session_id: str) -> RuntimeControlEntry | None:
        return self.journal.peek_runtime_control(
            self.workspace_id, session_id, kind=RuntimeControlKind.FOLLOW_UP
        )


__all__ = ["RuntimeControlService"]
