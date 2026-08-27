"""SQLite persistence for the bounded durable runtime-control queue."""

from __future__ import annotations

from datetime import UTC, datetime

from morrow.adapters.state.transaction import SqliteJournalBackend
from morrow.core.runtime_control import (
    RUNTIME_CONTROL_MAX_PENDING,
    RuntimeControlEntry,
    RuntimeControlError,
    RuntimeControlErrorCode,
    RuntimeControlKind,
    RuntimeControlStatus,
)
from morrow.core.store import StorageError, StorageErrorCode

_COLUMNS = (
    "workspace_id, session_id, position, kind, client_message_id, text, status, "
    "created_at_unix, consumed_at_unix"
)


def _unix(value: datetime) -> int:
    return int(value.timestamp())


def _from_unix(value: object) -> datetime:
    return datetime.fromtimestamp(int(value), UTC)


class SqliteRuntimeControlJournal:
    """Queue repository sharing the journal's outer transaction backend."""

    def __init__(self, backend: SqliteJournalBackend) -> None:
        self.backend = backend

    def enqueue(
        self,
        workspace_id: str,
        *,
        session_id: str,
        kind: RuntimeControlKind,
        client_message_id: str,
        text: str,
        created_at: datetime,
    ) -> RuntimeControlEntry:
        def work() -> RuntimeControlEntry:
            existing = self.get(workspace_id, session_id, client_message_id)
            if existing is not None:
                if existing.kind is kind and existing.text == text:
                    return existing
                raise RuntimeControlError(
                    RuntimeControlErrorCode.CONFLICT,
                    "runtime control client message ID conflicts with an existing entry",
                )
            session_row = self.backend.read_one(
                "SELECT 1 FROM sessions WHERE workspace_id = ? AND session_id = ?",
                (workspace_id, session_id),
            )
            if session_row is None:
                raise StorageError(StorageErrorCode.NOT_FOUND, "runtime control Session is missing")
            pending = self.backend.read_one(
                "SELECT COUNT(*) FROM runtime_control_queue "
                "WHERE workspace_id = ? AND session_id = ? AND status = 'pending'",
                (workspace_id, session_id),
            )
            if pending is not None and int(pending[0]) >= RUNTIME_CONTROL_MAX_PENDING:
                raise RuntimeControlError(
                    RuntimeControlErrorCode.QUEUE_FULL,
                    "runtime control queue has reached its pending-entry limit",
                )
            position_row = self.backend.read_one(
                "SELECT COALESCE(MAX(position), 0) + 1 FROM runtime_control_queue "
                "WHERE session_id = ?",
                (session_id,),
            )
            position = int(position_row[0]) if position_row is not None else 1
            self.backend.executor().execute(
                f"INSERT INTO runtime_control_queue({_COLUMNS}) "
                "VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, NULL)",
                (
                    workspace_id,
                    session_id,
                    position,
                    kind.value,
                    client_message_id,
                    text,
                    _unix(created_at),
                ),
            )
            loaded = self.get(workspace_id, session_id, client_message_id)
            if loaded is None:
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE,
                    "runtime control entry could not be read",
                )
            return loaded

        return self.backend.transact(work)

    def get(
        self, workspace_id: str, session_id: str, client_message_id: str
    ) -> RuntimeControlEntry | None:
        row = self.backend.read_one(
            f"SELECT {_COLUMNS} FROM runtime_control_queue "
            "WHERE workspace_id = ? AND session_id = ? AND client_message_id = ?",
            (workspace_id, session_id, client_message_id),
        )
        return _entry_from_row(row) if row is not None else None

    def peek(
        self,
        workspace_id: str,
        session_id: str,
        *,
        kind: RuntimeControlKind,
    ) -> RuntimeControlEntry | None:
        row = self.backend.read_one(
            f"SELECT {_COLUMNS} FROM runtime_control_queue "
            "WHERE workspace_id = ? AND session_id = ? AND status = 'pending' AND kind = ? "
            "ORDER BY position ASC LIMIT 1",
            (workspace_id, session_id, kind.value),
        )
        return _entry_from_row(row) if row is not None else None

    def list_entries(
        self,
        workspace_id: str,
        session_id: str,
        *,
        status: RuntimeControlStatus | None = None,
    ) -> tuple[RuntimeControlEntry, ...]:
        sql = (
            f"SELECT {_COLUMNS} FROM runtime_control_queue "
            "WHERE workspace_id = ? AND session_id = ?"
        )
        parameters: list[object] = [workspace_id, session_id]
        if status is not None:
            sql += " AND status = ?"
            parameters.append(status.value)
        sql += " ORDER BY position ASC"
        return tuple(_entry_from_row(row) for row in self.backend.read_all(sql, tuple(parameters)))

    def consume(
        self,
        workspace_id: str,
        session_id: str,
        client_message_id: str,
        *,
        consumed_at: datetime,
    ) -> RuntimeControlEntry:
        def work() -> RuntimeControlEntry:
            current = self.get(workspace_id, session_id, client_message_id)
            if current is None:
                raise RuntimeControlError(
                    RuntimeControlErrorCode.NOT_FOUND,
                    "runtime control entry is missing",
                )
            if current.status is RuntimeControlStatus.CONSUMED:
                return current
            if current.status is not RuntimeControlStatus.PENDING:
                raise RuntimeControlError(
                    RuntimeControlErrorCode.CONFLICT,
                    "runtime control entry is not pending",
                )
            self.backend.executor().execute(
                "UPDATE runtime_control_queue SET status = 'consumed', consumed_at_unix = ? "
                "WHERE workspace_id = ? AND session_id = ? AND client_message_id = ? "
                "AND status = 'pending'",
                (_unix(consumed_at), workspace_id, session_id, client_message_id),
            )
            loaded = self.get(workspace_id, session_id, client_message_id)
            if loaded is None or loaded.status is not RuntimeControlStatus.CONSUMED:
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE,
                    "runtime control entry could not be consumed",
                )
            return loaded

        return self.backend.transact(work)


def _entry_from_row(row: tuple[object, ...]) -> RuntimeControlEntry:
    return RuntimeControlEntry(
        workspace_id=str(row[0]),
        session_id=str(row[1]),
        position=int(row[2]),
        kind=RuntimeControlKind(str(row[3])),
        client_message_id=str(row[4]),
        text=str(row[5]),
        status=RuntimeControlStatus(str(row[6])),
        created_at=_from_unix(row[7]),
        consumed_at=_from_unix(row[8]) if row[8] is not None else None,
    )


__all__ = ["SqliteRuntimeControlJournal"]
