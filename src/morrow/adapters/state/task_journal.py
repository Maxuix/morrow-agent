"""SQLite persistence for TaskRun lifecycle, outcomes, and command receipts."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime

from morrow.adapters.state.transaction import SqliteJournalBackend
from morrow.core.domain import (
    ArtifactReference,
    DurableSession,
    DurableTaskOutcome,
    DurableTaskRun,
    DurableTaskRunTransition,
    TaskCommandDisposition,
    TaskCommandReceipt,
    TaskRunStatus,
    canonical_json_bytes,
    session_can_start_work,
    validate_task_transition,
)
from morrow.core.store import StorageError, StorageErrorCode

_TASK_INSERT_COLUMNS = (
    "task_run_id, session_id, workspace_id, status, row_version, attempt, "
    "created_at_unix, updated_at_unix, accepted_at_unix, closed_at_unix"
)
_TASK_SELECT = (
    "t.task_run_id, t.session_id, t.workspace_id, t.status, t.row_version, t.attempt, "
    "t.created_at_unix, t.updated_at_unix, t.accepted_at_unix, t.closed_at_unix"
)
_TRANSITION_COLUMNS = (
    "transition_id, workspace_id, session_id, task_run_id, from_status, to_status, "
    "reason, turn_id, command_id, attempt, created_at_unix"
)
_OUTCOME_COLUMNS = (
    "outcome_id, workspace_id, session_id, task_run_id, version, trigger, task_status, "
    "payload_json, payload_bytes, created_at_unix, artifact_refs_json"
)
_RECEIPT_COLUMNS = (
    "command_id, workspace_id, session_id, task_run_id, operation, request_digest, "
    "disposition, result_task_run_id, outcome_id, task_status, row_version, created_at_unix"
)


def _unix(value: datetime) -> int:
    return int(value.timestamp())


def _from_unix(value: object) -> datetime:
    return datetime.fromtimestamp(int(value), UTC)


def _optional_unix(value: datetime | None) -> int | None:
    return _unix(value) if value is not None else None


class SqliteTaskJournal:
    """Bounded Task repository sharing one outer transaction backend."""

    def __init__(
        self,
        backend: SqliteJournalBackend,
        *,
        get_session: Callable[[str, str], DurableSession | None],
        turn_belongs_to_task: Callable[[str, str, str], bool],
        validate_artifact_refs: Callable[..., None],
        replace_artifact_refs: Callable[..., None],
    ) -> None:
        self.backend = backend
        self.get_session = get_session
        self.turn_belongs_to_task = turn_belongs_to_task
        self.validate_artifact_refs = validate_artifact_refs
        self.replace_artifact_refs = replace_artifact_refs

    def get(self, workspace_id: str, task_run_id: str) -> DurableTaskRun | None:
        row = self.backend.read_one(
            f"SELECT {_TASK_SELECT} FROM task_runs t "
            "WHERE t.task_run_id = ? AND t.workspace_id = ?",
            (task_run_id, workspace_id),
        )
        return _task_from_row(row) if row is not None else None

    def list(self, workspace_id: str, session_id: str) -> tuple[DurableTaskRun, ...]:
        rows = self.backend.read_all(
            f"SELECT {_TASK_SELECT} FROM task_runs t "
            "WHERE t.workspace_id = ? AND t.session_id = ? "
            "ORDER BY t.created_at_unix ASC, t.task_run_id ASC",
            (workspace_id, session_id),
        )
        return tuple(_task_from_row(row) for row in rows)

    def create(
        self, workspace_id: str, task: DurableTaskRun, *, make_current: bool = False
    ) -> DurableTaskRun:
        if task.workspace_id != workspace_id:
            raise StorageError(
                StorageErrorCode.UNAVAILABLE, "operational task is outside the workspace"
            )
        if task.status is not TaskRunStatus.OPEN:
            raise StorageError(StorageErrorCode.UNAVAILABLE, "new operational task must start open")

        def work() -> DurableTaskRun:
            session = self.get_session(workspace_id, task.session_id)
            if session is None:
                raise StorageError(StorageErrorCode.NOT_FOUND, "operational session is missing")
            if not session_can_start_work(session.lifecycle, session.health):
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE,
                    "only an active healthy session can start a task",
                )
            if make_current and session.current_task_run_id not in {None, task.task_run_id}:
                current = self.get(workspace_id, session.current_task_run_id)
                if current is not None and current.status in {
                    TaskRunStatus.OPEN,
                    TaskRunStatus.READY_FOR_ACCEPTANCE,
                }:
                    raise StorageError(
                        StorageErrorCode.UNAVAILABLE,
                        "an active operational task must be closed before replacement",
                    )
            self.insert(task)
            updated_at = self._session_mutation_time(session, requested=task.updated_at)
            if make_current or session.current_task_run_id is None:
                self.backend.executor().execute(
                    """
                    UPDATE sessions
                    SET current_task_run_id = ?, updated_at_unix = ?
                    WHERE session_id = ? AND workspace_id = ?
                    """,
                    (task.task_run_id, _unix(updated_at), task.session_id, workspace_id),
                )
            else:
                self.backend.executor().execute(
                    """
                    UPDATE sessions
                    SET updated_at_unix = ?
                    WHERE session_id = ? AND workspace_id = ?
                    """,
                    (_unix(updated_at), task.session_id, workspace_id),
                )
            loaded = self.get(workspace_id, task.task_run_id)
            if loaded is None:
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE, "operational task could not be read"
                )
            return loaded

        return self.backend.transact(work)

    def transition(
        self,
        workspace_id: str,
        task_run_id: str,
        *,
        target: TaskRunStatus,
        transition: DurableTaskRunTransition,
        expected_row_version: int,
    ) -> DurableTaskRun:
        if transition.workspace_id != workspace_id or transition.task_run_id != task_run_id:
            raise StorageError(
                StorageErrorCode.UNAVAILABLE, "operational task transition is outside the workspace"
            )

        def work() -> DurableTaskRun:
            current = self.get(workspace_id, task_run_id)
            if current is None:
                raise StorageError(StorageErrorCode.NOT_FOUND, "operational task is missing")
            if current.row_version != expected_row_version:
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE, "operational task row version is stale"
                )
            if transition.to_status is not target:
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE, "operational task transition target is invalid"
                )
            if transition.from_status is not current.status:
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE, "operational task transition source is stale"
                )
            try:
                validate_task_transition(current.status, target)
            except ValueError as exc:
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE, "operational task transition is not allowed"
                ) from exc
            if transition.session_id != current.session_id:
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE, "operational task transition session is invalid"
                )
            if transition.turn_id is not None and not self.turn_belongs_to_task(
                workspace_id, transition.turn_id, task_run_id
            ):
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE, "operational task transition turn is invalid"
                )
            next_attempt = current.attempt + (
                1 if current.status is TaskRunStatus.FAILED and target is TaskRunStatus.OPEN else 0
            )
            session = self.get_session(workspace_id, current.session_id)
            if session is None:
                raise StorageError(StorageErrorCode.NOT_FOUND, "operational session is missing")
            if target is TaskRunStatus.OPEN and not session_can_start_work(
                session.lifecycle, session.health
            ):
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE,
                    "only an active healthy session can resume a task",
                )
            if target is TaskRunStatus.OPEN and session.current_task_run_id not in {
                None,
                task_run_id,
            }:
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE, "another operational task is already current"
                )
            if transition.attempt != next_attempt:
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE, "operational task transition attempt is invalid"
                )
            now = transition.created_at
            accepted_at = now if target is TaskRunStatus.ACCEPTED else current.accepted_at
            closed_at = now if target.is_terminal else None
            executor = self.backend.executor()
            executor.execute(
                """
                UPDATE task_runs
                SET status = ?, row_version = ?, attempt = ?, updated_at_unix = ?,
                    accepted_at_unix = ?, closed_at_unix = ?
                WHERE task_run_id = ? AND row_version = ?
                """,
                (
                    target.value,
                    current.row_version + 1,
                    next_attempt,
                    _unix(now),
                    _optional_unix(accepted_at),
                    _optional_unix(closed_at),
                    task_run_id,
                    expected_row_version,
                ),
            )
            executor.execute(
                f"INSERT INTO task_run_transitions({_TRANSITION_COLUMNS}) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    transition.transition_id,
                    transition.workspace_id,
                    transition.session_id,
                    transition.task_run_id,
                    current.status.value,
                    target.value,
                    transition.reason,
                    transition.turn_id,
                    transition.command_id,
                    next_attempt,
                    _unix(now),
                ),
            )
            if target.is_terminal:
                executor.execute(
                    """
                    UPDATE sessions
                    SET current_task_run_id = NULL
                    WHERE session_id = ? AND workspace_id = ? AND current_task_run_id = ?
                    """,
                    (current.session_id, workspace_id, task_run_id),
                )
            elif target is TaskRunStatus.OPEN:
                executor.execute(
                    """
                    UPDATE sessions
                    SET current_task_run_id = ?
                    WHERE session_id = ? AND workspace_id = ?
                    """,
                    (task_run_id, current.session_id, workspace_id),
                )
            updated_at = self._session_mutation_time(session, requested=now)
            executor.execute(
                """
                UPDATE sessions
                SET updated_at_unix = ?
                WHERE session_id = ? AND workspace_id = ?
                """,
                (_unix(updated_at), current.session_id, workspace_id),
            )
            loaded = self.get(workspace_id, task_run_id)
            if loaded is None:
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE, "operational task could not be read"
                )
            return loaded

        return self.backend.transact(work)

    def list_transitions(
        self, workspace_id: str, task_run_id: str
    ) -> tuple[DurableTaskRunTransition, ...]:
        rows = self.backend.read_all(
            f"SELECT {_TRANSITION_COLUMNS} FROM task_run_transitions "
            "WHERE workspace_id = ? AND task_run_id = ? "
            "ORDER BY created_at_unix ASC, transition_id ASC",
            (workspace_id, task_run_id),
        )
        return tuple(_transition_from_row(row) for row in rows)

    def put_outcome(self, workspace_id: str, outcome: DurableTaskOutcome) -> DurableTaskOutcome:
        if outcome.workspace_id != workspace_id:
            raise StorageError(
                StorageErrorCode.UNAVAILABLE, "operational outcome is outside the workspace"
            )

        def work() -> DurableTaskOutcome:
            task = self.get(workspace_id, outcome.task_run_id)
            if task is None or task.session_id != outcome.session_id:
                raise StorageError(StorageErrorCode.NOT_FOUND, "operational task is missing")
            if task.status is not outcome.task_status:
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE, "operational outcome status is stale"
                )
            previous = self.backend.read_one(
                "SELECT MAX(version) FROM task_outcomes WHERE task_run_id = ?",
                (outcome.task_run_id,),
            )
            next_version = (int(previous[0]) if previous and previous[0] is not None else 0) + 1
            if outcome.version != next_version:
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE, "operational outcome version is stale"
                )
            self.validate_artifact_refs(
                workspace_id,
                outcome.artifact_refs,
                session_id=outcome.session_id,
                task_run_id=outcome.task_run_id,
            )
            payload = canonical_json_bytes(outcome.model_dump(mode="json"))
            self.backend.executor().execute(
                f"INSERT INTO task_outcomes({_OUTCOME_COLUMNS}) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    outcome.outcome_id,
                    outcome.workspace_id,
                    outcome.session_id,
                    outcome.task_run_id,
                    outcome.version,
                    outcome.trigger.value,
                    outcome.task_status.value,
                    payload.decode("utf-8"),
                    len(payload),
                    _unix(outcome.created_at),
                    _optional_json(outcome.artifact_refs),
                ),
            )
            self.replace_artifact_refs(
                workspace_id,
                owner_kind="task_outcome",
                owner_id=outcome.outcome_id,
                references=outcome.artifact_refs,
                created_at=outcome.created_at,
            )
            loaded = self.get_outcome(workspace_id, outcome.outcome_id)
            if loaded is None:
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE, "operational outcome could not be read"
                )
            return loaded

        return self.backend.transact(work)

    def get_outcome(self, workspace_id: str, outcome_id: str) -> DurableTaskOutcome | None:
        row = self.backend.read_one(
            f"SELECT {_OUTCOME_COLUMNS} FROM task_outcomes "
            "WHERE outcome_id = ? AND workspace_id = ?",
            (outcome_id, workspace_id),
        )
        return _outcome_from_row(row) if row is not None else None

    def list_outcomes(self, workspace_id: str, task_run_id: str) -> tuple[DurableTaskOutcome, ...]:
        rows = self.backend.read_all(
            f"SELECT {_OUTCOME_COLUMNS} FROM task_outcomes "
            "WHERE workspace_id = ? AND task_run_id = ? ORDER BY version ASC",
            (workspace_id, task_run_id),
        )
        return tuple(_outcome_from_row(row) for row in rows)

    def get_command_receipt(self, workspace_id: str, command_id: str) -> TaskCommandReceipt | None:
        row = self.backend.read_one(
            f"SELECT {_RECEIPT_COLUMNS} FROM task_command_receipts WHERE command_id = ?",
            (command_id,),
        )
        if row is None:
            return None
        receipt = _receipt_from_row(row)
        if receipt.workspace_id != workspace_id:
            raise StorageError(
                StorageErrorCode.UNAVAILABLE, "operational task command is outside the workspace"
            )
        return receipt

    def put_command_receipt(
        self, workspace_id: str, receipt: TaskCommandReceipt
    ) -> TaskCommandReceipt:
        if receipt.workspace_id != workspace_id:
            raise StorageError(
                StorageErrorCode.UNAVAILABLE, "operational task command is outside the workspace"
            )

        def work() -> TaskCommandReceipt:
            if self.get_session(workspace_id, receipt.session_id) is None:
                raise StorageError(StorageErrorCode.NOT_FOUND, "operational session is missing")
            if receipt.task_run_id is not None:
                task = self.get(workspace_id, receipt.task_run_id)
                if task is None or task.session_id != receipt.session_id:
                    raise StorageError(
                        StorageErrorCode.UNAVAILABLE, "operational task command target is invalid"
                    )
                if receipt.task_status is not None and receipt.task_status is not task.status:
                    raise StorageError(
                        StorageErrorCode.UNAVAILABLE,
                        "operational task command status is inconsistent",
                    )
                if receipt.row_version is not None and receipt.row_version != task.row_version:
                    raise StorageError(
                        StorageErrorCode.UNAVAILABLE,
                        "operational task command version is inconsistent",
                    )
            if receipt.result_task_run_id is not None:
                result_task = self.get(workspace_id, receipt.result_task_run_id)
                if result_task is None or result_task.session_id != receipt.session_id:
                    raise StorageError(
                        StorageErrorCode.UNAVAILABLE, "operational task command result is invalid"
                    )
            if receipt.outcome_id is not None:
                outcome = self.get_outcome(workspace_id, receipt.outcome_id)
                if outcome is None or outcome.session_id != receipt.session_id:
                    raise StorageError(
                        StorageErrorCode.UNAVAILABLE, "operational task command outcome is invalid"
                    )
            self.backend.executor().execute(
                f"INSERT INTO task_command_receipts({_RECEIPT_COLUMNS}) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    receipt.command_id,
                    receipt.workspace_id,
                    receipt.session_id,
                    receipt.task_run_id,
                    receipt.operation,
                    receipt.request_digest,
                    receipt.disposition.value,
                    receipt.result_task_run_id,
                    receipt.outcome_id,
                    receipt.task_status.value if receipt.task_status is not None else None,
                    receipt.row_version,
                    _unix(receipt.created_at),
                ),
            )
            loaded = self.get_command_receipt(workspace_id, receipt.command_id)
            if loaded is None:
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE,
                    "operational task command receipt could not be read",
                )
            return loaded

        return self.backend.transact(work)

    def insert(self, task: DurableTaskRun) -> None:
        self.backend.executor().execute(
            f"INSERT INTO task_runs({_TASK_INSERT_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                task.task_run_id,
                task.session_id,
                task.workspace_id,
                task.status.value,
                task.row_version,
                task.attempt,
                _unix(task.created_at),
                _unix(task.updated_at),
                _optional_unix(task.accepted_at),
                _optional_unix(task.closed_at),
            ),
        )

    def _session_mutation_time(
        self, session: DurableSession, *, requested: datetime | None
    ) -> datetime:
        return self.backend.session_mutation_time(
            session,
            requested=requested,
            load_current=lambda: self.get_session(session.workspace_id, session.session_id),
        )


def _optional_json(value: object | None) -> str | None:
    if value is None:
        return None
    if hasattr(value, "model_dump"):
        dumped = value.model_dump(mode="json")
    elif isinstance(value, (tuple, list)):
        dumped = [
            item.model_dump(mode="json") if hasattr(item, "model_dump") else item for item in value
        ]
    else:
        dumped = value
    return canonical_json_bytes(dumped).decode("utf-8")


def _task_from_row(row: tuple[object, ...]) -> DurableTaskRun:
    return DurableTaskRun(
        task_run_id=str(row[0]),
        session_id=str(row[1]),
        workspace_id=str(row[2]),
        status=TaskRunStatus(str(row[3])),
        row_version=int(row[4]),
        attempt=int(row[5]),
        created_at=_from_unix(row[6]),
        updated_at=_from_unix(row[7]),
        accepted_at=_from_unix(row[8]) if row[8] is not None else None,
        closed_at=_from_unix(row[9]) if row[9] is not None else None,
    )


def _transition_from_row(row: tuple[object, ...]) -> DurableTaskRunTransition:
    return DurableTaskRunTransition(
        transition_id=str(row[0]),
        workspace_id=str(row[1]),
        session_id=str(row[2]),
        task_run_id=str(row[3]),
        from_status=TaskRunStatus(str(row[4])) if row[4] is not None else None,
        to_status=TaskRunStatus(str(row[5])),
        reason=str(row[6]),
        turn_id=str(row[7]) if row[7] is not None else None,
        command_id=str(row[8]) if row[8] is not None else None,
        attempt=int(row[9]),
        created_at=_from_unix(row[10]),
    )


def _outcome_from_row(row: tuple[object, ...]) -> DurableTaskOutcome:
    try:
        payload = json.loads(str(row[7]))
        if not isinstance(payload, dict):
            raise ValueError("operational outcome is not a mapping")
        outcome = DurableTaskOutcome.model_validate(payload)
        artifact_refs = _artifact_refs_from_raw(row[10])
        if (
            outcome.outcome_id != str(row[0])
            or outcome.workspace_id != str(row[1])
            or outcome.session_id != str(row[2])
            or outcome.task_run_id != str(row[3])
            or outcome.version != int(row[4])
            or outcome.trigger.value != str(row[5])
            or outcome.task_status.value != str(row[6])
            or len(canonical_json_bytes(payload)) != int(row[8])
            or _unix(outcome.created_at) != int(row[9])
            or tuple(outcome.artifact_refs) != artifact_refs
        ):
            raise ValueError("operational outcome metadata does not match its payload")
        return outcome
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise StorageError(
            StorageErrorCode.NEEDS_REPAIR, "operational outcome payload is invalid"
        ) from exc


def _artifact_refs_from_raw(raw: object) -> tuple[ArtifactReference, ...]:
    try:
        payload = json.loads(str(raw))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise StorageError(
            StorageErrorCode.NEEDS_REPAIR, "operational artifact references are invalid"
        ) from exc
    if not isinstance(payload, list):
        raise StorageError(
            StorageErrorCode.NEEDS_REPAIR, "operational artifact references are invalid"
        )
    try:
        return tuple(ArtifactReference.model_validate(item) for item in payload)
    except (TypeError, ValueError) as exc:
        raise StorageError(
            StorageErrorCode.NEEDS_REPAIR, "operational artifact references are invalid"
        ) from exc


def _receipt_from_row(row: tuple[object, ...]) -> TaskCommandReceipt:
    return TaskCommandReceipt(
        command_id=str(row[0]),
        workspace_id=str(row[1]),
        session_id=str(row[2]),
        task_run_id=str(row[3]) if row[3] is not None else None,
        operation=str(row[4]),
        request_digest=str(row[5]),
        disposition=TaskCommandDisposition(str(row[6])),
        result_task_run_id=str(row[7]) if row[7] is not None else None,
        outcome_id=str(row[8]) if row[8] is not None else None,
        task_status=TaskRunStatus(str(row[9])) if row[9] is not None else None,
        row_version=int(row[10]) if row[10] is not None else None,
        created_at=_from_unix(row[11]),
    )
