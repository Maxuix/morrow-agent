"""SQLite persistence for application events and command receipts."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime

from morrow.adapters.state.transaction import SqliteJournalBackend
from morrow.core.application import (
    ApplicationCommandDisposition,
    ApplicationCommandReceipt,
    ApplicationEvent,
)
from morrow.core.domain import canonical_json_bytes
from morrow.core.store import StorageError, StorageErrorCode

_EVENT_COLUMNS = (
    "event_id, workspace_id, cursor, schema_version, event_type, aggregate_kind, "
    "aggregate_id, payload_json, payload_bytes, created_at_unix"
)
_RECEIPT_COLUMNS = (
    "command_id, workspace_id, session_id, operation, request_digest, disposition, "
    "result_kind, result_id, event_cursor, row_version, created_at_unix"
)


def _unix(value: datetime) -> int:
    return int(value.timestamp())


def _from_unix(value: object) -> datetime:
    return datetime.fromtimestamp(int(value), UTC)


class SqliteApplicationJournal:
    """Bounded application-event repository sharing one outer transaction backend."""

    def __init__(
        self,
        backend: SqliteJournalBackend,
        *,
        session_exists: Callable[[str, str], bool],
    ) -> None:
        self.backend = backend
        self.session_exists = session_exists

    def get_event(self, workspace_id: str, event_id: str) -> ApplicationEvent | None:
        row = self.backend.read_one(
            f"SELECT {_EVENT_COLUMNS} FROM application_events WHERE event_id = ?",
            (event_id,),
        )
        if row is None:
            return None
        if str(row[1]) != workspace_id:
            raise StorageError(
                StorageErrorCode.UNAVAILABLE, "application event is outside the workspace"
            )
        return _event_from_row(row)

    def list_events(
        self, workspace_id: str, *, after_cursor: int = 0, limit: int = 100
    ) -> tuple[ApplicationEvent, ...]:
        if after_cursor < 0 or limit < 1 or limit > 100:
            raise StorageError(StorageErrorCode.UNAVAILABLE, "application event page is invalid")
        rows = self.backend.read_all(
            f"SELECT {_EVENT_COLUMNS} FROM application_events "
            "WHERE workspace_id = ? AND cursor > ? ORDER BY cursor ASC LIMIT ?",
            (workspace_id, after_cursor, limit),
        )
        return tuple(_event_from_row(row) for row in rows)

    def put_event(self, workspace_id: str, event: ApplicationEvent) -> ApplicationEvent:
        return self.backend.transact(lambda: self.put_event_in_txn(workspace_id, event))

    def put_event_in_txn(self, workspace_id: str, event: ApplicationEvent) -> ApplicationEvent:
        if event.workspace_id != workspace_id:
            raise StorageError(
                StorageErrorCode.UNAVAILABLE, "application event is outside the workspace"
            )
        executor = self.backend.executor()
        if self.get_event(workspace_id, event.event_id) is not None:
            raise StorageError(StorageErrorCode.UNAVAILABLE, "application event already exists")
        next_cursor_row = executor.execute(
            "SELECT COALESCE(MAX(cursor), 0) + 1 FROM application_events WHERE workspace_id = ?",
            (workspace_id,),
        )
        next_cursor = int(next_cursor_row[0][0])
        if event.cursor not in {0, next_cursor}:
            raise StorageError(
                StorageErrorCode.UNAVAILABLE, "application event cursor is not monotonic"
            )
        stored = event.model_copy(update={"cursor": next_cursor})
        payload = canonical_json_bytes(stored.payload)
        executor.execute(
            f"INSERT INTO application_events({_EVENT_COLUMNS}) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                stored.event_id,
                stored.workspace_id,
                stored.cursor,
                stored.schema_version,
                stored.event_type,
                stored.aggregate_kind,
                stored.aggregate_id,
                payload.decode("utf-8"),
                len(payload),
                _unix(stored.created_at),
            ),
        )
        loaded = self.get_event(workspace_id, stored.event_id)
        if loaded is None:
            raise StorageError(StorageErrorCode.UNAVAILABLE, "application event could not be read")
        return loaded

    def get_receipt(self, workspace_id: str, command_id: str) -> ApplicationCommandReceipt | None:
        row = self.backend.read_one(
            f"SELECT {_RECEIPT_COLUMNS} FROM application_command_receipts WHERE command_id = ?",
            (command_id,),
        )
        if row is None:
            return None
        if str(row[1]) != workspace_id:
            raise StorageError(
                StorageErrorCode.UNAVAILABLE,
                "application command receipt is outside the workspace",
            )
        return _receipt_from_row(row)

    def put_receipt(
        self, workspace_id: str, receipt: ApplicationCommandReceipt
    ) -> ApplicationCommandReceipt:
        return self.backend.transact(lambda: self.put_receipt_in_txn(workspace_id, receipt))

    def put_receipt_in_txn(
        self, workspace_id: str, receipt: ApplicationCommandReceipt
    ) -> ApplicationCommandReceipt:
        if receipt.workspace_id != workspace_id:
            raise StorageError(
                StorageErrorCode.UNAVAILABLE,
                "application command receipt is outside the workspace",
            )
        existing = self.get_receipt(workspace_id, receipt.command_id)
        if existing is not None:
            if existing.request_digest != receipt.request_digest:
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE,
                    "application command receipt conflicts with an existing command",
                )
            return existing
        if receipt.session_id is not None and not self.session_exists(
            workspace_id, receipt.session_id
        ):
            raise StorageError(StorageErrorCode.NOT_FOUND, "operational session is missing")
        self.backend.executor().execute(
            f"INSERT INTO application_command_receipts({_RECEIPT_COLUMNS}) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                receipt.command_id,
                receipt.workspace_id,
                receipt.session_id,
                receipt.operation,
                receipt.request_digest,
                receipt.disposition.value,
                receipt.result_kind,
                receipt.result_id,
                receipt.event_cursor,
                receipt.row_version,
                _unix(receipt.created_at),
            ),
        )
        loaded = self.get_receipt(workspace_id, receipt.command_id)
        if loaded is None:
            raise StorageError(
                StorageErrorCode.UNAVAILABLE, "application command receipt could not be read"
            )
        return loaded


def _event_from_row(row: tuple[object, ...]) -> ApplicationEvent:
    try:
        payload = json.loads(str(row[7]))
        if not isinstance(payload, dict) or len(canonical_json_bytes(payload)) != int(row[8]):
            raise ValueError("application event payload is invalid")
        event = ApplicationEvent(
            event_id=str(row[0]),
            workspace_id=str(row[1]),
            cursor=int(row[2]),
            schema_version=int(row[3]),
            event_type=str(row[4]),
            aggregate_kind=str(row[5]),
            aggregate_id=str(row[6]),
            payload=payload,
            created_at=_from_unix(row[9]),
        )
        if event.cursor < 1:
            raise ValueError("application event cursor is invalid")
        return event
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise StorageError(StorageErrorCode.NEEDS_REPAIR, "application event is invalid") from exc


def _receipt_from_row(row: tuple[object, ...]) -> ApplicationCommandReceipt:
    try:
        return ApplicationCommandReceipt(
            command_id=str(row[0]),
            workspace_id=str(row[1]),
            session_id=str(row[2]) if row[2] is not None else None,
            operation=str(row[3]),
            request_digest=str(row[4]),
            disposition=ApplicationCommandDisposition(str(row[5])),
            result_kind=str(row[6]) if row[6] is not None else None,
            result_id=str(row[7]) if row[7] is not None else None,
            event_cursor=int(row[8]) if row[8] is not None else None,
            row_version=int(row[9]) if row[9] is not None else None,
            created_at=_from_unix(row[10]),
        )
    except (TypeError, ValueError) as exc:
        raise StorageError(
            StorageErrorCode.NEEDS_REPAIR, "application command receipt is invalid"
        ) from exc
