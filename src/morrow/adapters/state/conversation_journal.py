"""SQLite persistence for Turns, conversation records, and submit receipts."""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from datetime import UTC, datetime

from morrow.adapters.state.transaction import SqliteJournalBackend
from morrow.core.domain import (
    DurableConversationRecord,
    DurableSession,
    DurableTaskRun,
    DurableTurn,
    TurnSubmitDisposition,
    TurnSubmitReceipt,
    canonical_json_bytes,
    session_can_start_work,
)
from morrow.core.store import StorageError, StorageErrorCode

_TURN_COLUMNS = "turn_id, session_id, task_run_id, client_message_id, created_at_unix"
_RECORD_COLUMNS = "record_id, session_id, conversation_position, kind, payload_json, payload_bytes"
_RECEIPT_COLUMNS = "session_id, client_message_id, request_digest, disposition, turn_id, command_id"


def _unix(value: datetime) -> int:
    return int(value.timestamp())


def _from_unix(value: object) -> datetime:
    return datetime.fromtimestamp(int(value), UTC)


class SqliteConversationJournal:
    """Bounded conversation repository sharing one outer transaction backend."""

    def __init__(
        self,
        backend: SqliteJournalBackend,
        *,
        get_session: Callable[[str, str], DurableSession | None],
        get_task: Callable[[str, str], DurableTaskRun | None],
        session_mutation_time: Callable[..., datetime],
    ) -> None:
        self.backend = backend
        self.get_session = get_session
        self.get_task = get_task
        self.session_mutation_time = session_mutation_time

    def create_turn(self, workspace_id: str, turn: DurableTurn) -> DurableTurn:
        def work() -> DurableTurn:
            task = self.get_task(workspace_id, turn.task_run_id)
            if task is None or task.session_id != turn.session_id:
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE, "operational turn does not belong to the session"
                )
            session = self.get_session(workspace_id, turn.session_id)
            if session is None:
                raise StorageError(StorageErrorCode.NOT_FOUND, "operational session is missing")
            if not session_can_start_work(session.lifecycle, session.health):
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE,
                    "only an active healthy session can start a turn",
                )
            self.backend.executor().execute(
                f"INSERT INTO turns({_TURN_COLUMNS}) VALUES (?, ?, ?, ?, ?)",
                (
                    turn.turn_id,
                    turn.session_id,
                    turn.task_run_id,
                    turn.client_message_id,
                    _unix(turn.created_at),
                ),
            )
            updated_at = self.session_mutation_time(session, requested=turn.created_at)
            self.backend.executor().execute(
                """
                UPDATE sessions
                SET updated_at_unix = ?
                WHERE session_id = ? AND workspace_id = ?
                """,
                (_unix(updated_at), turn.session_id, workspace_id),
            )
            loaded = self.get_turn(workspace_id, turn.turn_id)
            if loaded is None:
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE, "operational turn could not be read"
                )
            return loaded

        return self.backend.transact(work)

    def get_turn(self, workspace_id: str, turn_id: str) -> DurableTurn | None:
        row = self.backend.read_one(
            "SELECT t.turn_id, t.session_id, t.task_run_id, t.client_message_id, "
            "t.created_at_unix FROM turns t "
            "JOIN sessions s ON s.session_id = t.session_id "
            "WHERE t.turn_id = ? AND s.workspace_id = ?",
            (turn_id, workspace_id),
        )
        return _turn_from_row(row) if row is not None else None

    def list_task_turns(self, workspace_id: str, task_run_id: str) -> tuple[DurableTurn, ...]:
        rows = self.backend.read_all(
            "SELECT t.turn_id, t.session_id, t.task_run_id, t.client_message_id, "
            "t.created_at_unix FROM turns t "
            "JOIN sessions s ON s.session_id = t.session_id "
            "WHERE t.task_run_id = ? AND s.workspace_id = ? "
            "ORDER BY t.created_at_unix ASC, t.turn_id ASC",
            (task_run_id, workspace_id),
        )
        return tuple(_turn_from_row(row) for row in rows)

    def list_session_turns(self, workspace_id: str, session_id: str) -> tuple[DurableTurn, ...]:
        rows = self.backend.read_all(
            "SELECT t.turn_id, t.session_id, t.task_run_id, t.client_message_id, "
            "t.created_at_unix FROM turns t "
            "JOIN sessions s ON s.session_id = t.session_id "
            "WHERE t.session_id = ? AND s.workspace_id = ? "
            "ORDER BY t.created_at_unix ASC, t.turn_id ASC",
            (session_id, workspace_id),
        )
        return tuple(_turn_from_row(row) for row in rows)

    def append_records(
        self, workspace_id: str, records: Sequence[DurableConversationRecord]
    ) -> DurableSession:
        if not records:
            raise StorageError(
                StorageErrorCode.UNAVAILABLE, "operational conversation append is empty"
            )
        session_id = records[0].session_id
        if any(record.session_id != session_id for record in records):
            raise StorageError(
                StorageErrorCode.UNAVAILABLE,
                "operational conversation records must share a session",
            )

        def work() -> DurableSession:
            session = self.get_session(workspace_id, session_id)
            if session is None:
                raise StorageError(StorageErrorCode.NOT_FOUND, "operational session is missing")
            expected = session.conversation_position + 1
            executor = self.backend.executor()
            for offset, record in enumerate(records):
                position = expected + offset
                if record.conversation_position != position:
                    raise StorageError(
                        StorageErrorCode.UNAVAILABLE, "conversation position conflict"
                    )
                payload = canonical_json_bytes(record.payload)
                executor.execute(
                    f"INSERT INTO conversation_records({_RECORD_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        record.record_id,
                        record.session_id,
                        record.conversation_position,
                        record.kind,
                        payload.decode("utf-8"),
                        len(payload),
                    ),
                )
            new_position = expected + len(records) - 1
            updated_at = self.session_mutation_time(session)
            executor.execute(
                """
                UPDATE sessions
                SET conversation_position = ?, updated_at_unix = ?
                WHERE session_id = ? AND workspace_id = ?
                """,
                (new_position, _unix(updated_at), session_id, workspace_id),
            )
            loaded = self.get_session(workspace_id, session_id)
            if loaded is None:
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE, "operational session could not be read"
                )
            return loaded

        return self.backend.transact(work)

    def load_records(
        self, workspace_id: str, session_id: str
    ) -> tuple[DurableConversationRecord, ...]:
        if self.get_session(workspace_id, session_id) is None:
            return ()
        rows = self.backend.read_all(
            f"SELECT {_RECORD_COLUMNS} FROM conversation_records "
            "WHERE session_id = ? ORDER BY conversation_position ASC",
            (session_id,),
        )
        return tuple(_record_from_row(row) for row in rows)

    def load_effective_records(
        self, workspace_id: str, session_id: str
    ) -> tuple[DurableConversationRecord, ...]:
        """Return the immutable parent prefix followed by local child records."""

        visited: set[str] = set()

        def visit(current_session_id: str, depth: int) -> tuple[DurableConversationRecord, ...]:
            if depth > 32 or current_session_id in visited:
                raise StorageError(
                    StorageErrorCode.NEEDS_REPAIR, "operational Session lineage is cyclic"
                )
            visited.add(current_session_id)
            session = self.get_session(workspace_id, current_session_id)
            if session is None:
                raise StorageError(StorageErrorCode.NOT_FOUND, "operational session is missing")
            local = self.load_records(workspace_id, current_session_id)
            if session.parent_session_id is None:
                _validate_record_sequence(
                    local, expected_start=1, expected_count=session.conversation_position
                )
                visited.remove(current_session_id)
                return local
            parent_records = visit(session.parent_session_id, depth + 1)
            if len(parent_records) < session.parent_cut_position:
                raise StorageError(
                    StorageErrorCode.NEEDS_REPAIR, "operational Session lineage cut is missing"
                )
            prefix = parent_records[: session.parent_cut_position]
            cut = prefix[-1]
            if (
                cut.conversation_position != session.parent_cut_position
                or cut.record_id != session.parent_cut_record_id
                or cut.kind != "terminal"
            ):
                raise StorageError(
                    StorageErrorCode.NEEDS_REPAIR, "operational Session lineage cut is invalid"
                )
            expected = session.parent_cut_position + 1
            _validate_record_sequence(
                local,
                expected_start=expected,
                expected_count=session.conversation_position - session.parent_cut_position,
            )
            visited.remove(current_session_id)
            return (*prefix, *local)

        return visit(session_id, 0)

    def get_receipt(
        self, workspace_id: str, session_id: str, client_message_id: str
    ) -> TurnSubmitReceipt | None:
        if self.get_session(workspace_id, session_id) is None:
            return None
        row = self.backend.read_one(
            f"SELECT {_RECEIPT_COLUMNS} FROM turn_submit_receipts "
            "WHERE session_id = ? AND client_message_id = ?",
            (session_id, client_message_id),
        )
        return _receipt_from_row(row) if row is not None else None

    def put_receipt(self, workspace_id: str, receipt: TurnSubmitReceipt) -> TurnSubmitReceipt:
        def work() -> TurnSubmitReceipt:
            if self.get_session(workspace_id, receipt.session_id) is None:
                raise StorageError(StorageErrorCode.NOT_FOUND, "operational session is missing")
            if receipt.turn_id is not None:
                turn = self.get_turn(workspace_id, receipt.turn_id)
                if turn is None or turn.session_id != receipt.session_id:
                    raise StorageError(
                        StorageErrorCode.UNAVAILABLE,
                        "operational receipt turn does not belong to the session",
                    )
            self.backend.executor().execute(
                f"INSERT INTO turn_submit_receipts({_RECEIPT_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    receipt.session_id,
                    receipt.client_message_id,
                    receipt.request_digest,
                    receipt.disposition.value,
                    receipt.turn_id,
                    receipt.command_id,
                ),
            )
            loaded = self.get_receipt(workspace_id, receipt.session_id, receipt.client_message_id)
            if loaded is None:
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE, "operational receipt could not be read"
                )
            return loaded

        return self.backend.transact(work)

    def update_receipt(self, workspace_id: str, receipt: TurnSubmitReceipt) -> TurnSubmitReceipt:
        def work() -> TurnSubmitReceipt:
            existing = self.get_receipt(workspace_id, receipt.session_id, receipt.client_message_id)
            if existing is None:
                raise StorageError(StorageErrorCode.NOT_FOUND, "operational receipt is missing")
            self.backend.executor().execute(
                """
                UPDATE turn_submit_receipts
                SET request_digest = ?, disposition = ?, turn_id = ?, command_id = ?
                WHERE session_id = ? AND client_message_id = ?
                """,
                (
                    receipt.request_digest,
                    receipt.disposition.value,
                    receipt.turn_id,
                    receipt.command_id,
                    receipt.session_id,
                    receipt.client_message_id,
                ),
            )
            loaded = self.get_receipt(workspace_id, receipt.session_id, receipt.client_message_id)
            if loaded is None:
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE, "operational receipt could not be read"
                )
            return loaded

        return self.backend.transact(work)


def _validate_record_sequence(
    records: tuple[DurableConversationRecord, ...], *, expected_start: int, expected_count: int
) -> None:
    if expected_count < 0 or len(records) != expected_count:
        raise StorageError(
            StorageErrorCode.NEEDS_REPAIR, "operational conversation position is inconsistent"
        )
    for offset, record in enumerate(records):
        if record.conversation_position != expected_start + offset:
            raise StorageError(
                StorageErrorCode.NEEDS_REPAIR,
                "operational conversation positions are invalid",
            )


def _turn_from_row(row: tuple[object, ...]) -> DurableTurn:
    return DurableTurn(
        turn_id=str(row[0]),
        session_id=str(row[1]),
        task_run_id=str(row[2]),
        client_message_id=str(row[3]),
        created_at=_from_unix(row[4]),
    )


def _record_from_row(row: tuple[object, ...]) -> DurableConversationRecord:
    payload = json.loads(str(row[4]))
    if not isinstance(payload, dict):
        raise StorageError(
            StorageErrorCode.NEEDS_REPAIR, "operational conversation record is not a mapping"
        )
    return DurableConversationRecord(
        record_id=str(row[0]),
        session_id=str(row[1]),
        conversation_position=int(row[2]),
        kind="terminal" if str(row[3]) == "terminal" else "message",
        payload=payload,
    )


def _receipt_from_row(row: tuple[object, ...]) -> TurnSubmitReceipt:
    return TurnSubmitReceipt(
        session_id=str(row[0]),
        client_message_id=str(row[1]),
        request_digest=str(row[2]),
        disposition=TurnSubmitDisposition(str(row[3])),
        turn_id=str(row[4]) if row[4] is not None else None,
        command_id=str(row[5]) if row[5] is not None else None,
    )
