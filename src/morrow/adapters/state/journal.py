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
from morrow.core.execution import (
    ApprovalResolution,
    DurableApproval,
    DurableToolExecution,
    DurableToolFacts,
    HandlerResultEnvelope,
    PreparedIntent,
    ToolExecutionDisposition,
    ToolExecutionState,
    intent_hash,
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
_EXECUTION_COLUMNS = (
    "tool_execution_id, workspace_id, session_id, task_run_id, turn_id, agent_run_id, "
    "assistant_record_id, call_id, ordinal, tool_name, state, disposition, row_version, "
    "retry_of_execution_id, approval_id, intent_json, intent_hash, schema_digest, "
    "permission_context_digest, result_envelope_json, facts_json, error_code, error_detail, "
    "created_at_unix, executing_at_unix, handler_completed_at_unix, closed_at_unix"
)
_APPROVAL_COLUMNS = (
    "approval_id, tool_execution_id, intent_hash, tool_schema_digest, "
    "permission_context_digest, requested_scope, granted_scope, preview_json, preview_digest, "
    "row_version, created_at_unix, expires_at_unix, resolution, resolved_at_unix, "
    "consumed_at_unix, command_id"
)
_APPROVAL_SELECT = (
    "a.approval_id, a.tool_execution_id, a.intent_hash, a.tool_schema_digest, "
    "a.permission_context_digest, a.requested_scope, a.granted_scope, a.preview_json, "
    "a.preview_digest, a.row_version, a.created_at_unix, a.expires_at_unix, a.resolution, "
    "a.resolved_at_unix, a.consumed_at_unix, a.command_id"
)


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

    def put_execution(
        self, workspace_id: str, execution: DurableToolExecution
    ) -> DurableToolExecution:
        def work(journal: SqliteOperationalJournal) -> DurableToolExecution:
            journal._insert_execution(workspace_id, execution)
            loaded = journal.get_execution(workspace_id, execution.tool_execution_id)
            if loaded is None:
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE, "operational execution could not be read"
                )
            return loaded

        return self.transact(work)

    def get_execution(
        self, workspace_id: str, tool_execution_id: str
    ) -> DurableToolExecution | None:
        row = self._read_one(
            f"SELECT {_EXECUTION_COLUMNS} FROM tool_executions "
            "WHERE tool_execution_id = ? AND workspace_id = ?",
            (tool_execution_id, workspace_id),
        )
        if row is None:
            return None
        return _execution_from_row(row)

    def list_executions(
        self, workspace_id: str, *, agent_run_id: str
    ) -> tuple[DurableToolExecution, ...]:
        rows = self._read_all(
            f"SELECT {_EXECUTION_COLUMNS} FROM tool_executions "
            "WHERE workspace_id = ? AND agent_run_id = ? ORDER BY ordinal ASC",
            (workspace_id, agent_run_id),
        )
        return tuple(_execution_from_row(row) for row in rows)

    def save_execution(
        self,
        workspace_id: str,
        execution: DurableToolExecution,
        *,
        expected_row_version: int,
    ) -> DurableToolExecution:
        if execution.workspace_id != workspace_id:
            raise StorageError(
                StorageErrorCode.UNAVAILABLE, "operational execution is outside the workspace"
            )

        def work(journal: SqliteOperationalJournal) -> DurableToolExecution:
            existing = journal.get_execution(workspace_id, execution.tool_execution_id)
            if existing is None:
                raise StorageError(StorageErrorCode.NOT_FOUND, "operational execution is missing")
            if existing.row_version != expected_row_version:
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE, "operational execution row version is stale"
                )
            if execution.row_version != expected_row_version + 1:
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE, "operational execution row version is stale"
                )
            journal._executor_or_raise().execute(
                """
                UPDATE tool_executions
                SET state = ?, disposition = ?, row_version = ?, approval_id = ?,
                    result_envelope_json = ?, facts_json = ?, error_code = ?,
                    error_detail = ?, executing_at_unix = ?,
                    handler_completed_at_unix = ?, closed_at_unix = ?
                WHERE tool_execution_id = ? AND workspace_id = ? AND row_version = ?
                """,
                (
                    execution.state.value,
                    execution.disposition.value,
                    execution.row_version,
                    execution.approval_id,
                    _optional_json(execution.result_envelope),
                    _optional_json(execution.facts),
                    execution.error_code,
                    execution.error_detail,
                    _optional_unix(execution.executing_at),
                    _optional_unix(execution.handler_completed_at),
                    _optional_unix(execution.closed_at),
                    execution.tool_execution_id,
                    workspace_id,
                    expected_row_version,
                ),
            )
            loaded = journal.get_execution(workspace_id, execution.tool_execution_id)
            if loaded is None or loaded.row_version != execution.row_version:
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE, "operational execution row version is stale"
                )
            return loaded

        return self.transact(work)

    def put_approval(self, workspace_id: str, approval: DurableApproval) -> DurableApproval:
        def work(journal: SqliteOperationalJournal) -> DurableApproval:
            execution = journal.get_execution(workspace_id, approval.tool_execution_id)
            if execution is None:
                raise StorageError(StorageErrorCode.NOT_FOUND, "operational execution is missing")
            stored_hash = intent_hash(execution.intent)
            if (
                approval.intent_hash != stored_hash
                or approval.tool_schema_digest != execution.intent.schema_digest
                or approval.permission_context_digest != execution.intent.permission_context_digest
            ):
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE,
                    "operational approval does not match the execution",
                )
            journal._executor_or_raise().execute(
                f"INSERT INTO approvals({_APPROVAL_COLUMNS}) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    approval.approval_id,
                    approval.tool_execution_id,
                    approval.intent_hash,
                    approval.tool_schema_digest,
                    approval.permission_context_digest,
                    approval.requested_scope,
                    approval.granted_scope,
                    canonical_json_bytes(list(approval.preview)).decode("utf-8"),
                    approval.preview_digest,
                    approval.row_version,
                    _unix(approval.created_at),
                    _unix(approval.expires_at),
                    approval.resolution.value,
                    _optional_unix(approval.resolved_at),
                    _optional_unix(approval.consumed_at),
                    approval.command_id,
                ),
            )
            loaded = journal.get_approval(workspace_id, approval.approval_id)
            if loaded is None:
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE, "operational approval could not be read"
                )
            return loaded

        return self.transact(work)

    def get_approval(self, workspace_id: str, approval_id: str) -> DurableApproval | None:
        row = self._read_one(
            f"SELECT {_APPROVAL_SELECT} FROM approvals a "
            "JOIN tool_executions e ON e.tool_execution_id = a.tool_execution_id "
            "WHERE a.approval_id = ? AND e.workspace_id = ?",
            (approval_id, workspace_id),
        )
        if row is None:
            return None
        return _approval_from_row(row)

    def get_approval_for_execution(
        self, workspace_id: str, tool_execution_id: str
    ) -> DurableApproval | None:
        row = self._read_one(
            f"SELECT {_APPROVAL_SELECT} FROM approvals a "
            "JOIN tool_executions e ON e.tool_execution_id = a.tool_execution_id "
            "WHERE a.tool_execution_id = ? AND e.workspace_id = ?",
            (tool_execution_id, workspace_id),
        )
        if row is None:
            return None
        return _approval_from_row(row)

    def save_approval(
        self,
        workspace_id: str,
        approval: DurableApproval,
        *,
        expected_row_version: int,
    ) -> DurableApproval:
        def work(journal: SqliteOperationalJournal) -> DurableApproval:
            existing = journal.get_approval(workspace_id, approval.approval_id)
            if existing is None:
                raise StorageError(StorageErrorCode.NOT_FOUND, "operational approval is missing")
            if existing.row_version != expected_row_version:
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE, "operational approval row version is stale"
                )
            if approval.row_version != expected_row_version + 1:
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE, "operational approval row version is stale"
                )
            journal._executor_or_raise().execute(
                """
                UPDATE approvals
                SET granted_scope = ?, row_version = ?, resolution = ?,
                    resolved_at_unix = ?, consumed_at_unix = ?, command_id = ?
                WHERE approval_id = ? AND row_version = ?
                """,
                (
                    approval.granted_scope,
                    approval.row_version,
                    approval.resolution.value,
                    _optional_unix(approval.resolved_at),
                    _optional_unix(approval.consumed_at),
                    approval.command_id,
                    approval.approval_id,
                    expected_row_version,
                ),
            )
            loaded = journal.get_approval(workspace_id, approval.approval_id)
            if loaded is None or loaded.row_version != approval.row_version:
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE, "operational approval row version is stale"
                )
            return loaded

        return self.transact(work)

    def _insert_execution(self, workspace_id: str, execution: DurableToolExecution) -> None:
        if execution.workspace_id != workspace_id:
            raise StorageError(
                StorageErrorCode.UNAVAILABLE, "operational execution is outside the workspace"
            )
        run = self.get_agent_run(workspace_id, execution.agent_run_id)
        if (
            run is None
            or run.session_id != execution.session_id
            or run.turn_id != execution.turn_id
        ):
            raise StorageError(
                StorageErrorCode.UNAVAILABLE, "operational execution does not belong to the run"
            )
        task = self.get_task_run(workspace_id, execution.task_run_id)
        if task is None or task.session_id != execution.session_id:
            raise StorageError(
                StorageErrorCode.UNAVAILABLE, "operational execution does not belong to the task"
            )
        self._executor_or_raise().execute(
            f"INSERT INTO tool_executions({_EXECUTION_COLUMNS}) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                execution.tool_execution_id,
                execution.workspace_id,
                execution.session_id,
                execution.task_run_id,
                execution.turn_id,
                execution.agent_run_id,
                execution.assistant_record_id,
                execution.call_id,
                execution.ordinal,
                execution.tool_name,
                execution.state.value,
                execution.disposition.value,
                execution.row_version,
                execution.retry_of_execution_id,
                execution.approval_id,
                canonical_json_bytes(execution.intent.model_dump(mode="json")).decode("utf-8"),
                intent_hash(execution.intent),
                execution.intent.schema_digest,
                execution.intent.permission_context_digest,
                _optional_json(execution.result_envelope),
                _optional_json(execution.facts),
                execution.error_code,
                execution.error_detail,
                _unix(execution.created_at),
                _optional_unix(execution.executing_at),
                _optional_unix(execution.handler_completed_at),
                _optional_unix(execution.closed_at),
            ),
        )

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


def _optional_unix(value: datetime | None) -> int | None:
    if value is None:
        return None
    return _unix(value)


def _optional_json(value: object | None) -> str | None:
    if value is None:
        return None
    dumped = value.model_dump(mode="json") if hasattr(value, "model_dump") else value
    return canonical_json_bytes(dumped).decode("utf-8")


def _load_mapping(raw: object, *, label: str) -> dict:
    payload = json.loads(str(raw))
    if not isinstance(payload, dict):
        raise StorageError(StorageErrorCode.NEEDS_REPAIR, f"{label} is not a mapping")
    return payload


def _execution_from_row(row: tuple[object, ...]) -> DurableToolExecution:
    intent = PreparedIntent.model_validate(_load_mapping(row[15], label="prepared intent"))
    envelope = None
    if row[19] is not None:
        envelope = HandlerResultEnvelope.model_validate(
            _load_mapping(row[19], label="tool result envelope")
        )
    facts = None
    if row[20] is not None:
        facts = DurableToolFacts.model_validate(
            _load_mapping(row[20], label="structured tool facts")
        )
    return DurableToolExecution(
        tool_execution_id=str(row[0]),
        workspace_id=str(row[1]),
        session_id=str(row[2]),
        task_run_id=str(row[3]),
        turn_id=str(row[4]),
        agent_run_id=str(row[5]),
        assistant_record_id=str(row[6]) if row[6] is not None else None,
        call_id=str(row[7]),
        ordinal=int(row[8]),
        tool_name=str(row[9]),
        state=ToolExecutionState(str(row[10])),
        disposition=ToolExecutionDisposition(str(row[11])),
        row_version=int(row[12]),
        retry_of_execution_id=str(row[13]) if row[13] is not None else None,
        approval_id=str(row[14]) if row[14] is not None else None,
        intent=intent,
        result_envelope=envelope,
        facts=facts,
        error_code=str(row[21]) if row[21] is not None else None,
        error_detail=str(row[22]) if row[22] is not None else None,
        created_at=_from_unix(row[23]),
        executing_at=_from_unix(row[24]) if row[24] is not None else None,
        handler_completed_at=_from_unix(row[25]) if row[25] is not None else None,
        closed_at=_from_unix(row[26]) if row[26] is not None else None,
    )


def _approval_from_row(row: tuple[object, ...]) -> DurableApproval:
    preview_raw = json.loads(str(row[7]))
    if not isinstance(preview_raw, list) or any(not isinstance(item, str) for item in preview_raw):
        raise StorageError(StorageErrorCode.NEEDS_REPAIR, "approval preview is not a string list")
    return DurableApproval(
        approval_id=str(row[0]),
        tool_execution_id=str(row[1]),
        intent_hash=str(row[2]),
        tool_schema_digest=str(row[3]),
        permission_context_digest=str(row[4]),
        requested_scope=str(row[5]),
        granted_scope=str(row[6]) if row[6] is not None else None,
        preview=tuple(str(item) for item in preview_raw),
        preview_digest=str(row[8]),
        row_version=int(row[9]),
        created_at=_from_unix(row[10]),
        expires_at=_from_unix(row[11]),
        resolution=ApprovalResolution(str(row[12])),
        resolved_at=_from_unix(row[13]) if row[13] is not None else None,
        consumed_at=_from_unix(row[14]) if row[14] is not None else None,
        command_id=str(row[15]) if row[15] is not None else None,
    )
