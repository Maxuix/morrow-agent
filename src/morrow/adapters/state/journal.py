"""SQLite adapter for Session lifecycle and the conversation journal."""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from datetime import UTC, datetime

from morrow.adapters.state.operational import OperationalStoreSession, SqliteExecutor
from morrow.core.domain import (
    AgentRunSnapshot,
    DurableAgentRun,
    DurableConversationRecord,
    DurableSession,
    DurableTaskRun,
    DurableTurn,
    SessionHealth,
    SessionLifecycle,
    TaskRunStatus,
    TurnSubmitDisposition,
    TurnSubmitReceipt,
    canonical_json_bytes,
)
from morrow.core.store import StorageError, StorageErrorCode

_SESSION_COLUMNS = (
    "session_id, workspace_id, lifecycle, health, current_task_run_id, "
    "conversation_position, created_at_unix, updated_at_unix"
)
_TASK_COLUMNS = "task_run_id, session_id, workspace_id, status, created_at_unix"
_TURN_COLUMNS = "turn_id, session_id, task_run_id, client_message_id, created_at_unix"
_AGENT_COLUMNS = (
    "agent_run_id, turn_id, session_id, resume_of_agent_run_id, snapshot_json, created_at_unix"
)
_RECORD_COLUMNS = "record_id, session_id, conversation_position, kind, payload_json, payload_bytes"
_RECEIPT_COLUMNS = "session_id, client_message_id, request_digest, disposition, turn_id, command_id"


def _unix(value: datetime) -> int:
    return int(value.timestamp())


def _from_unix(value: object) -> datetime:
    return datetime.fromtimestamp(int(value), UTC)


class SqliteOperationalJournal:
    """One SQLite adapter exposing the narrow lifecycle and journal ports."""

    def __init__(self, session: OperationalStoreSession) -> None:
        self._session = session
        self._executor: SqliteExecutor | None = None

    def transact[T](self, work: Callable[[SqliteOperationalJournal], T]) -> T:
        if self._executor is not None:
            return work(self)

        def body(executor: SqliteExecutor) -> T:
            self._executor = executor
            try:
                return work(self)
            finally:
                self._executor = None

        return self._session.run_write(body)

    def create_session(
        self, session: DurableSession, *, task: DurableTaskRun | None = None
    ) -> DurableSession:
        if task is not None:
            if task.session_id != session.session_id or task.workspace_id != session.workspace_id:
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE, "operational task does not belong to the session"
                )
            if session.current_task_run_id not in {None, task.task_run_id}:
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE, "operational session task pointer is inconsistent"
                )
            session = session.model_copy(update={"current_task_run_id": task.task_run_id})

        def work(journal: SqliteOperationalJournal) -> DurableSession:
            journal._insert_session(session)
            if task is not None:
                journal._insert_task(task)
            loaded = journal.get_session(session.workspace_id, session.session_id)
            if loaded is None:
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE, "operational session could not be read"
                )
            return loaded

        return self.transact(work)

    def get_session(self, workspace_id: str, session_id: str) -> DurableSession | None:
        row = self._read_one(
            f"SELECT {_SESSION_COLUMNS} FROM sessions WHERE session_id = ? AND workspace_id = ?",
            (session_id, workspace_id),
        )
        if row is None:
            return None
        return _session_from_row(row)

    def list_sessions(self, workspace_id: str) -> tuple[DurableSession, ...]:
        rows = self._read_all(
            f"SELECT {_SESSION_COLUMNS} FROM sessions "
            "WHERE workspace_id = ? AND lifecycle != 'deleted' "
            "ORDER BY created_at_unix ASC, session_id ASC",
            (workspace_id,),
        )
        return tuple(_session_from_row(row) for row in rows)

    def save_session(self, workspace_id: str, session: DurableSession) -> DurableSession:
        if session.workspace_id != workspace_id:
            raise StorageError(
                StorageErrorCode.UNAVAILABLE, "operational session is outside the workspace"
            )

        def work(journal: SqliteOperationalJournal) -> DurableSession:
            existing = journal.get_session(workspace_id, session.session_id)
            if existing is None:
                raise StorageError(StorageErrorCode.NOT_FOUND, "operational session is missing")
            if session.current_task_run_id is not None:
                task = journal.get_task_run(workspace_id, session.current_task_run_id)
                if task is None or task.session_id != session.session_id:
                    raise StorageError(
                        StorageErrorCode.UNAVAILABLE,
                        "operational session task pointer is inconsistent",
                    )
            journal._executor_or_raise().execute(
                """
                UPDATE sessions
                SET lifecycle = ?, health = ?, current_task_run_id = ?,
                    conversation_position = ?, updated_at_unix = ?
                WHERE session_id = ? AND workspace_id = ?
                """,
                (
                    session.lifecycle.value,
                    session.health.value,
                    session.current_task_run_id,
                    session.conversation_position,
                    _unix(session.updated_at),
                    session.session_id,
                    workspace_id,
                ),
            )
            loaded = journal.get_session(workspace_id, session.session_id)
            if loaded is None:
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE, "operational session could not be read"
                )
            return loaded

        return self.transact(work)

    def get_task_run(self, workspace_id: str, task_run_id: str) -> DurableTaskRun | None:
        row = self._read_one(
            f"SELECT {_TASK_COLUMNS} FROM task_runs WHERE task_run_id = ? AND workspace_id = ?",
            (task_run_id, workspace_id),
        )
        if row is None:
            return None
        return _task_from_row(row)

    def create_task_run(self, workspace_id: str, task: DurableTaskRun) -> DurableTaskRun:
        if task.workspace_id != workspace_id:
            raise StorageError(
                StorageErrorCode.UNAVAILABLE, "operational task is outside the workspace"
            )

        def work(journal: SqliteOperationalJournal) -> DurableTaskRun:
            session = journal.get_session(workspace_id, task.session_id)
            if session is None:
                raise StorageError(StorageErrorCode.NOT_FOUND, "operational session is missing")
            journal._insert_task(task)
            if session.current_task_run_id is None:
                journal._executor_or_raise().execute(
                    """
                    UPDATE sessions
                    SET current_task_run_id = ?, updated_at_unix = ?
                    WHERE session_id = ? AND workspace_id = ?
                    """,
                    (task.task_run_id, _unix(session.updated_at), task.session_id, workspace_id),
                )
            loaded = journal.get_task_run(workspace_id, task.task_run_id)
            if loaded is None:
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE, "operational task could not be read"
                )
            return loaded

        return self.transact(work)

    def create_turn(self, workspace_id: str, turn: DurableTurn) -> DurableTurn:
        def work(journal: SqliteOperationalJournal) -> DurableTurn:
            task = journal.get_task_run(workspace_id, turn.task_run_id)
            if task is None or task.session_id != turn.session_id:
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE, "operational turn does not belong to the session"
                )
            if journal.get_session(workspace_id, turn.session_id) is None:
                raise StorageError(StorageErrorCode.NOT_FOUND, "operational session is missing")
            journal._executor_or_raise().execute(
                f"INSERT INTO turns({_TURN_COLUMNS}) VALUES (?, ?, ?, ?, ?)",
                (
                    turn.turn_id,
                    turn.session_id,
                    turn.task_run_id,
                    turn.client_message_id,
                    _unix(turn.created_at),
                ),
            )
            loaded = journal.get_turn(workspace_id, turn.turn_id)
            if loaded is None:
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE, "operational turn could not be read"
                )
            return loaded

        return self.transact(work)

    def get_turn(self, workspace_id: str, turn_id: str) -> DurableTurn | None:
        row = self._read_one(
            "SELECT t.turn_id, t.session_id, t.task_run_id, t.client_message_id, "
            "t.created_at_unix FROM turns t "
            "JOIN sessions s ON s.session_id = t.session_id "
            "WHERE t.turn_id = ? AND s.workspace_id = ?",
            (turn_id, workspace_id),
        )
        if row is None:
            return None
        return _turn_from_row(row)

    def create_agent_run(self, workspace_id: str, run: DurableAgentRun) -> DurableAgentRun:
        def work(journal: SqliteOperationalJournal) -> DurableAgentRun:
            turn = journal.get_turn(workspace_id, run.turn_id)
            if turn is None or turn.session_id != run.session_id:
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE, "operational run does not belong to the turn"
                )
            if run.resume_of_agent_run_id is not None:
                previous = journal.get_agent_run(workspace_id, run.resume_of_agent_run_id)
                if previous is None or previous.turn_id != run.turn_id:
                    raise StorageError(
                        StorageErrorCode.UNAVAILABLE,
                        "operational run resume target is missing",
                    )
            snapshot = canonical_json_bytes(run.snapshot.model_dump(mode="json")).decode("utf-8")
            journal._executor_or_raise().execute(
                f"INSERT INTO agent_runs({_AGENT_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    run.agent_run_id,
                    run.turn_id,
                    run.session_id,
                    run.resume_of_agent_run_id,
                    snapshot,
                    _unix(run.created_at),
                ),
            )
            loaded = journal.get_agent_run(workspace_id, run.agent_run_id)
            if loaded is None:
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE, "operational run could not be read"
                )
            return loaded

        return self.transact(work)

    def get_agent_run(self, workspace_id: str, agent_run_id: str) -> DurableAgentRun | None:
        row = self._read_one(
            "SELECT r.agent_run_id, r.turn_id, r.session_id, r.resume_of_agent_run_id, "
            "r.snapshot_json, r.created_at_unix FROM agent_runs r "
            "JOIN sessions s ON s.session_id = r.session_id "
            "WHERE r.agent_run_id = ? AND s.workspace_id = ?",
            (agent_run_id, workspace_id),
        )
        if row is None:
            return None
        return _agent_from_row(row)

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

        def work(journal: SqliteOperationalJournal) -> DurableSession:
            session = journal.get_session(workspace_id, session_id)
            if session is None:
                raise StorageError(StorageErrorCode.NOT_FOUND, "operational session is missing")
            expected = session.conversation_position + 1
            executor = journal._executor_or_raise()
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
            executor.execute(
                """
                UPDATE sessions
                SET conversation_position = ?, updated_at_unix = ?
                WHERE session_id = ? AND workspace_id = ?
                """,
                (new_position, _unix(session.updated_at), session_id, workspace_id),
            )
            loaded = journal.get_session(workspace_id, session_id)
            if loaded is None:
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE, "operational session could not be read"
                )
            return loaded

        return self.transact(work)

    def load_records(
        self, workspace_id: str, session_id: str
    ) -> tuple[DurableConversationRecord, ...]:
        if self.get_session(workspace_id, session_id) is None:
            return ()
        rows = self._read_all(
            f"SELECT {_RECORD_COLUMNS} FROM conversation_records "
            "WHERE session_id = ? ORDER BY conversation_position ASC",
            (session_id,),
        )
        return tuple(_record_from_row(row) for row in rows)

    def get_receipt(
        self, workspace_id: str, session_id: str, client_message_id: str
    ) -> TurnSubmitReceipt | None:
        if self.get_session(workspace_id, session_id) is None:
            return None
        row = self._read_one(
            f"SELECT {_RECEIPT_COLUMNS} FROM turn_submit_receipts "
            "WHERE session_id = ? AND client_message_id = ?",
            (session_id, client_message_id),
        )
        if row is None:
            return None
        return _receipt_from_row(row)

    def put_receipt(self, workspace_id: str, receipt: TurnSubmitReceipt) -> TurnSubmitReceipt:
        def work(journal: SqliteOperationalJournal) -> TurnSubmitReceipt:
            if journal.get_session(workspace_id, receipt.session_id) is None:
                raise StorageError(StorageErrorCode.NOT_FOUND, "operational session is missing")
            if receipt.turn_id is not None:
                turn = journal.get_turn(workspace_id, receipt.turn_id)
                if turn is None or turn.session_id != receipt.session_id:
                    raise StorageError(
                        StorageErrorCode.UNAVAILABLE,
                        "operational receipt turn does not belong to the session",
                    )
            journal._executor_or_raise().execute(
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
            loaded = journal.get_receipt(
                workspace_id, receipt.session_id, receipt.client_message_id
            )
            if loaded is None:
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE, "operational receipt could not be read"
                )
            return loaded

        return self.transact(work)

    def update_receipt(self, workspace_id: str, receipt: TurnSubmitReceipt) -> TurnSubmitReceipt:
        def work(journal: SqliteOperationalJournal) -> TurnSubmitReceipt:
            existing = journal.get_receipt(
                workspace_id, receipt.session_id, receipt.client_message_id
            )
            if existing is None:
                raise StorageError(StorageErrorCode.NOT_FOUND, "operational receipt is missing")
            journal._executor_or_raise().execute(
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
            loaded = journal.get_receipt(
                workspace_id, receipt.session_id, receipt.client_message_id
            )
            if loaded is None:
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE, "operational receipt could not be read"
                )
            return loaded

        return self.transact(work)

    def _insert_session(self, session: DurableSession) -> None:
        self._executor_or_raise().execute(
            f"INSERT INTO sessions({_SESSION_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                session.session_id,
                session.workspace_id,
                session.lifecycle.value,
                session.health.value,
                session.current_task_run_id,
                session.conversation_position,
                _unix(session.created_at),
                _unix(session.updated_at),
            ),
        )

    def _insert_task(self, task: DurableTaskRun) -> None:
        self._executor_or_raise().execute(
            f"INSERT INTO task_runs({_TASK_COLUMNS}) VALUES (?, ?, ?, ?, ?)",
            (
                task.task_run_id,
                task.session_id,
                task.workspace_id,
                task.status.value,
                _unix(task.created_at),
            ),
        )

    def _executor_or_raise(self) -> SqliteExecutor:
        if self._executor is None:
            raise StorageError(
                StorageErrorCode.UNAVAILABLE, "operational journal write is not active"
            )
        return self._executor

    def _read_one(self, sql: str, parameters: tuple[object, ...]) -> tuple[object, ...] | None:
        rows = self._read_all(sql, parameters)
        if not rows:
            return None
        return rows[0]

    def _read_all(
        self, sql: str, parameters: tuple[object, ...] = ()
    ) -> tuple[tuple[object, ...], ...]:
        if self._executor is not None:
            return self._executor.execute(sql, parameters)
        return self._session.run_read(lambda executor: executor.execute(sql, parameters))


def _session_from_row(row: tuple[object, ...]) -> DurableSession:
    return DurableSession(
        session_id=str(row[0]),
        workspace_id=str(row[1]),
        lifecycle=SessionLifecycle(str(row[2])),
        health=SessionHealth(str(row[3])),
        current_task_run_id=str(row[4]) if row[4] is not None else None,
        conversation_position=int(row[5]),
        created_at=_from_unix(row[6]),
        updated_at=_from_unix(row[7]),
    )


def _task_from_row(row: tuple[object, ...]) -> DurableTaskRun:
    return DurableTaskRun(
        task_run_id=str(row[0]),
        session_id=str(row[1]),
        workspace_id=str(row[2]),
        status=TaskRunStatus(str(row[3])),
        created_at=_from_unix(row[4]),
    )


def _turn_from_row(row: tuple[object, ...]) -> DurableTurn:
    return DurableTurn(
        turn_id=str(row[0]),
        session_id=str(row[1]),
        task_run_id=str(row[2]),
        client_message_id=str(row[3]),
        created_at=_from_unix(row[4]),
    )


def _agent_from_row(row: tuple[object, ...]) -> DurableAgentRun:
    snapshot = AgentRunSnapshot.model_validate(json.loads(str(row[4])))
    return DurableAgentRun(
        agent_run_id=str(row[0]),
        turn_id=str(row[1]),
        session_id=str(row[2]),
        resume_of_agent_run_id=str(row[3]) if row[3] is not None else None,
        snapshot=snapshot,
        created_at=_from_unix(row[5]),
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
