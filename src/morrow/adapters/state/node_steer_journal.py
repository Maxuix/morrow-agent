"""Durable targeted node-steering queue (master plan P6.2/P6.4).

The control owner for node steer commands. Entries are idempotent by
``(session_id, client_message_id)``, consumed at-most-once with a single
transactional state flip, and expired — never forwarded — when the target
node completes.
"""

from __future__ import annotations

from datetime import datetime

from morrow.adapters.state.transaction import SqliteJournalBackend
from morrow.core.runtime_control import (
    RUNTIME_CONTROL_TEXT_MAX_CHARS,
    RuntimeControlError,
    RuntimeControlErrorCode,
)

_COLUMNS = (
    "workspace_id, session_id, workflow_run_id, node_run_id, node_id, "
    "graph_revision_id, client_message_id, text, status, created_at_unix, consumed_at_unix"
)


def _unix(value: datetime) -> int:
    return int(value.timestamp())


def _from_unix(value: object) -> datetime | None:
    if value is None:
        return None
    from datetime import UTC

    return datetime.fromtimestamp(int(value), tz=UTC)


def _entry_from_row(row) -> dict:
    keys = (
        "workspace_id",
        "session_id",
        "workflow_run_id",
        "node_run_id",
        "node_id",
        "graph_revision_id",
        "client_message_id",
        "text",
        "status",
        "created_at",
        "consumed_at",
    )
    values = list(row[:9])
    values.append(_from_unix(row[9]))
    values.append(_from_unix(row[10]))
    return dict(zip(keys, values, strict=True))


class SqliteNodeSteerJournal:
    def __init__(self, backend: SqliteJournalBackend) -> None:
        self._backend = backend

    def enqueue(
        self,
        workspace_id: str,
        *,
        session_id: str,
        workflow_run_id: str,
        node_run_id: str,
        node_id: str | None,
        graph_revision_id: str,
        client_message_id: str,
        text: str,
        created_at: datetime,
    ) -> dict:
        if not text.strip() or len(text) > RUNTIME_CONTROL_TEXT_MAX_CHARS:
            raise RuntimeControlError(
                RuntimeControlErrorCode.INVALID,
                "node steer text must contain 1 to 4096 characters",
            )

        def work() -> dict:
            existing = self.get(workspace_id, session_id, client_message_id)
            if existing is not None:
                if (
                    existing["workflow_run_id"] == workflow_run_id
                    and existing["node_run_id"] == node_run_id
                    and existing["text"] == text
                ):
                    return existing
                raise RuntimeControlError(
                    RuntimeControlErrorCode.CONFLICT,
                    "node steer command id conflicts with an existing entry",
                )
            self._backend.executor().execute(
                f"INSERT INTO workflow_node_steers({_COLUMNS}) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, NULL)",
                (
                    workspace_id,
                    session_id,
                    workflow_run_id,
                    node_run_id,
                    node_id,
                    graph_revision_id,
                    client_message_id,
                    text,
                    _unix(created_at),
                ),
            )
            loaded = self.get(workspace_id, session_id, client_message_id)
            if loaded is None:
                raise RuntimeControlError(
                    RuntimeControlErrorCode.NOT_FOUND,
                    "node steer entry could not be read back",
                )
            return loaded

        return self._backend.transact(work)

    def get(self, workspace_id: str, session_id: str, client_message_id: str) -> dict | None:
        row = self._backend.read_one(
            f"SELECT {_COLUMNS} FROM workflow_node_steers "
            "WHERE workspace_id = ? AND session_id = ? AND client_message_id = ?",
            (workspace_id, session_id, client_message_id),
        )
        return _entry_from_row(row) if row is not None else None

    def peek_pending(self, workspace_id: str, session_id: str) -> dict | None:
        row = self._backend.read_one(
            f"SELECT {_COLUMNS} FROM workflow_node_steers "
            "WHERE workspace_id = ? AND session_id = ? AND status = 'pending' "
            "ORDER BY created_at_unix ASC, client_message_id ASC LIMIT 1",
            (workspace_id, session_id),
        )
        return _entry_from_row(row) if row is not None else None

    def consume(
        self,
        workspace_id: str,
        session_id: str,
        client_message_id: str,
        *,
        consumed_at: datetime,
    ) -> dict | None:
        """At-most-once flip: only a pending row moves to consumed."""

        def work() -> dict | None:
            current = self.get(workspace_id, session_id, client_message_id)
            if current is None or current["status"] != "pending":
                return None
            self._backend.executor().execute(
                "UPDATE workflow_node_steers SET status = 'consumed', consumed_at_unix = ? "
                "WHERE workspace_id = ? AND session_id = ? AND client_message_id = ? "
                "AND status = 'pending'",
                (_unix(consumed_at), workspace_id, session_id, client_message_id),
            )
            return self.get(workspace_id, session_id, client_message_id)

        return self._backend.transact(work)

    def expire_pending(self, workspace_id: str, session_id: str) -> int:
        """Expire leftovers when the target node completes (P6.4)."""

        def work() -> int:
            cursor = self._backend.executor().execute(
                "UPDATE workflow_node_steers SET status = 'expired' "
                "WHERE workspace_id = ? AND session_id = ? AND status = 'pending'",
                (workspace_id, session_id),
            )
            return cursor.rowcount if cursor.rowcount and cursor.rowcount > 0 else 0

        return self._backend.transact(work)
