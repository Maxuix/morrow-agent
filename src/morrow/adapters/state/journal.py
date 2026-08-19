"""SQLite adapter for Session lifecycle and the conversation journal."""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from datetime import UTC, datetime

from morrow.adapters.state.operational import OperationalStoreSession, SqliteExecutor
from morrow.core.artifacts import (
    TASK_ARTIFACT_MAX_BYTES,
    ArtifactBudgetError,
    ArtifactKind,
    ArtifactMetadata,
    ArtifactProvenanceRef,
    ArtifactRetention,
    ArtifactSensitivity,
    ArtifactState,
)
from morrow.core.context import (
    ContextCheckpoint,
    ContextCheckpointOmission,
    ContextCheckpointSection,
    SessionLineage,
)
from morrow.core.domain import (
    AgentRunSnapshot,
    ArtifactReference,
    DurableAgentRun,
    DurableConversationRecord,
    DurableSession,
    DurableTaskOutcome,
    DurableTaskRun,
    DurableTaskRunTransition,
    DurableTurn,
    SessionHealth,
    SessionLifecycle,
    TaskCommandDisposition,
    TaskCommandReceipt,
    TaskRunStatus,
    TurnSubmitDisposition,
    TurnSubmitReceipt,
    canonical_json_bytes,
    validate_task_transition,
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
from morrow.core.recovery import RecoveryReceipt, RecoveryReport, RecoveryResolution
from morrow.core.store import StorageError, StorageErrorCode

_SESSION_COLUMNS = (
    "session_id, workspace_id, lifecycle, health, current_task_run_id, "
    "conversation_position, parent_session_id, parent_cut_record_id, parent_cut_position, "
    "parent_checkpoint_id, fork_reason, created_at_unix, updated_at_unix"
)
_TASK_INSERT_COLUMNS = (
    "task_run_id, session_id, workspace_id, status, row_version, attempt, "
    "created_at_unix, updated_at_unix, accepted_at_unix, closed_at_unix"
)
_TASK_SELECT = (
    "t.task_run_id, t.session_id, t.workspace_id, t.status, t.row_version, t.attempt, "
    "t.created_at_unix, t.updated_at_unix, t.accepted_at_unix, t.closed_at_unix"
)
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
    "created_at_unix, executing_at_unix, handler_completed_at_unix, closed_at_unix, "
    "artifact_refs_json"
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
_REPORT_COLUMNS = (
    "report_id, workspace_id, session_id, turn_id, agent_run_id, status, "
    "payload_json, payload_bytes, created_at_unix, resolved_at_unix"
)
_RECOVERY_RECEIPT_COLUMNS = "session_id, command_id, request_digest, report_id, item_id, resolution"
_TRANSITION_COLUMNS = (
    "transition_id, workspace_id, session_id, task_run_id, from_status, to_status, "
    "reason, turn_id, command_id, attempt, created_at_unix"
)
_OUTCOME_COLUMNS = (
    "outcome_id, workspace_id, session_id, task_run_id, version, trigger, task_status, "
    "payload_json, payload_bytes, created_at_unix, artifact_refs_json"
)
_ARTIFACT_COLUMNS = (
    "artifact_id, workspace_id, session_id, task_run_id, kind, sensitivity, state, retention, "
    "sha256, byte_size, excerpt, provenance_json, row_version, created_at_unix, updated_at_unix"
)
_ARTIFACT_REFERENCE_COLUMNS = (
    "artifact_id, workspace_id, owner_kind, owner_id, role, created_at_unix"
)
_CHECKPOINT_ARTIFACT_REFERENCE_COLUMNS = (
    "artifact_id, workspace_id, checkpoint_id, role, created_at_unix"
)
_TASK_RECEIPT_COLUMNS = (
    "command_id, workspace_id, session_id, task_run_id, operation, request_digest, "
    "disposition, result_task_run_id, outcome_id, task_status, row_version, created_at_unix"
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
            if task.status is not TaskRunStatus.OPEN:
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE, "new operational task must start open"
                )
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
            journal._validate_session_lineage(session)
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

    def get_session_lineage(self, workspace_id: str, session_id: str) -> SessionLineage | None:
        session = self.get_session(workspace_id, session_id)
        if session is None or session.parent_session_id is None:
            return None
        return SessionLineage(
            workspace_id=workspace_id,
            child_session_id=session.session_id,
            parent_session_id=session.parent_session_id,
            cut_record_id=session.parent_cut_record_id,
            cut_position=session.parent_cut_position,
            checkpoint_id=session.parent_checkpoint_id,
            reason=session.fork_reason,
            created_at=session.created_at,
        )

    def get_lineage(self, workspace_id: str, session_id: str) -> SessionLineage | None:
        return self.get_session_lineage(workspace_id, session_id)

    def save_session(self, workspace_id: str, session: DurableSession) -> DurableSession:
        if session.workspace_id != workspace_id:
            raise StorageError(
                StorageErrorCode.UNAVAILABLE, "operational session is outside the workspace"
            )

        def work(journal: SqliteOperationalJournal) -> DurableSession:
            existing = journal.get_session(workspace_id, session.session_id)
            if existing is None:
                raise StorageError(StorageErrorCode.NOT_FOUND, "operational session is missing")
            if _session_lineage_fields(existing) != _session_lineage_fields(session):
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE, "operational Session lineage is immutable"
                )
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
            f"SELECT {_TASK_SELECT} FROM task_runs t "
            "WHERE t.task_run_id = ? AND t.workspace_id = ?",
            (task_run_id, workspace_id),
        )
        if row is None:
            return None
        return _task_from_row(row)

    def list_task_runs(self, workspace_id: str, session_id: str) -> tuple[DurableTaskRun, ...]:
        rows = self._read_all(
            f"SELECT {_TASK_SELECT} FROM task_runs t "
            "WHERE t.workspace_id = ? AND t.session_id = ? "
            "ORDER BY t.created_at_unix ASC, t.task_run_id ASC",
            (workspace_id, session_id),
        )
        return tuple(_task_from_row(row) for row in rows)

    def create_task_run(
        self, workspace_id: str, task: DurableTaskRun, *, make_current: bool = False
    ) -> DurableTaskRun:
        if task.workspace_id != workspace_id:
            raise StorageError(
                StorageErrorCode.UNAVAILABLE, "operational task is outside the workspace"
            )
        if task.status is not TaskRunStatus.OPEN:
            raise StorageError(StorageErrorCode.UNAVAILABLE, "new operational task must start open")

        def work(journal: SqliteOperationalJournal) -> DurableTaskRun:
            session = journal.get_session(workspace_id, task.session_id)
            if session is None:
                raise StorageError(StorageErrorCode.NOT_FOUND, "operational session is missing")
            if session.lifecycle is SessionLifecycle.DELETED:
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE, "deleted session cannot start a task"
                )
            if make_current and session.current_task_run_id not in {None, task.task_run_id}:
                current = journal.get_task_run(workspace_id, session.current_task_run_id)
                if current is not None and current.status in {
                    TaskRunStatus.OPEN,
                    TaskRunStatus.READY_FOR_ACCEPTANCE,
                }:
                    raise StorageError(
                        StorageErrorCode.UNAVAILABLE,
                        "an active operational task must be closed before replacement",
                    )
            journal._insert_task(task)
            if make_current or session.current_task_run_id is None:
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

    def transition_task_run(
        self,
        workspace_id: str,
        task_run_id: str,
        *,
        target: TaskRunStatus,
        transition: DurableTaskRunTransition,
        expected_row_version: int,
    ) -> DurableTaskRun:
        """Apply one optimistic, audited TaskRun transition."""

        if transition.workspace_id != workspace_id or transition.task_run_id != task_run_id:
            raise StorageError(
                StorageErrorCode.UNAVAILABLE, "operational task transition is outside the workspace"
            )

        def work(journal: SqliteOperationalJournal) -> DurableTaskRun:
            current = journal.get_task_run(workspace_id, task_run_id)
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
            if transition.turn_id is not None:
                turn = journal.get_turn(workspace_id, transition.turn_id)
                if turn is None or turn.task_run_id != task_run_id:
                    raise StorageError(
                        StorageErrorCode.UNAVAILABLE, "operational task transition turn is invalid"
                    )
            next_attempt = current.attempt + (
                1 if current.status is TaskRunStatus.FAILED and target is TaskRunStatus.OPEN else 0
            )
            session = journal.get_session(workspace_id, current.session_id)
            if session is None:
                raise StorageError(StorageErrorCode.NOT_FOUND, "operational session is missing")
            if target is TaskRunStatus.OPEN and session.current_task_run_id not in {
                None,
                task_run_id,
            }:
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE,
                    "another operational task is already current",
                )
            if transition.attempt != next_attempt:
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE, "operational task transition attempt is invalid"
                )
            now = transition.created_at
            accepted_at = now if target is TaskRunStatus.ACCEPTED else current.accepted_at
            closed_at = now if target.is_terminal else None
            executor = journal._executor_or_raise()
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
                    SET current_task_run_id = NULL, updated_at_unix = ?
                    WHERE session_id = ? AND workspace_id = ? AND current_task_run_id = ?
                    """,
                    (_unix(now), current.session_id, workspace_id, task_run_id),
                )
            elif target is TaskRunStatus.OPEN:
                executor.execute(
                    """
                    UPDATE sessions
                    SET current_task_run_id = ?, updated_at_unix = ?
                    WHERE session_id = ? AND workspace_id = ?
                    """,
                    (task_run_id, _unix(now), current.session_id, workspace_id),
                )
            loaded = journal.get_task_run(workspace_id, task_run_id)
            if loaded is None:
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE, "operational task could not be read"
                )
            return loaded

        return self.transact(work)

    def list_task_transitions(
        self, workspace_id: str, task_run_id: str
    ) -> tuple[DurableTaskRunTransition, ...]:
        rows = self._read_all(
            f"SELECT {_TRANSITION_COLUMNS} FROM task_run_transitions "
            "WHERE workspace_id = ? AND task_run_id = ? "
            "ORDER BY created_at_unix ASC, transition_id ASC",
            (workspace_id, task_run_id),
        )
        return tuple(_transition_from_row(row) for row in rows)

    def put_task_outcome(
        self, workspace_id: str, outcome: DurableTaskOutcome
    ) -> DurableTaskOutcome:
        if outcome.workspace_id != workspace_id:
            raise StorageError(
                StorageErrorCode.UNAVAILABLE, "operational outcome is outside the workspace"
            )

        def work(journal: SqliteOperationalJournal) -> DurableTaskOutcome:
            task = journal.get_task_run(workspace_id, outcome.task_run_id)
            if task is None or task.session_id != outcome.session_id:
                raise StorageError(StorageErrorCode.NOT_FOUND, "operational task is missing")
            if task.status is not outcome.task_status:
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE, "operational outcome status is stale"
                )
            previous = journal._read_one(
                "SELECT MAX(version) FROM task_outcomes WHERE task_run_id = ?",
                (outcome.task_run_id,),
            )
            next_version = (int(previous[0]) if previous and previous[0] is not None else 0) + 1
            if outcome.version != next_version:
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE, "operational outcome version is stale"
                )
            journal._validate_artifact_refs(
                workspace_id,
                outcome.artifact_refs,
                session_id=outcome.session_id,
                task_run_id=outcome.task_run_id,
            )
            payload = canonical_json_bytes(outcome.model_dump(mode="json"))
            journal._executor_or_raise().execute(
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
            journal._replace_artifact_references(
                workspace_id,
                owner_kind="task_outcome",
                owner_id=outcome.outcome_id,
                references=outcome.artifact_refs,
                created_at=outcome.created_at,
            )
            loaded = journal.get_task_outcome(workspace_id, outcome.outcome_id)
            if loaded is None:
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE, "operational outcome could not be read"
                )
            return loaded

        return self.transact(work)

    def get_task_outcome(self, workspace_id: str, outcome_id: str) -> DurableTaskOutcome | None:
        row = self._read_one(
            f"SELECT {_OUTCOME_COLUMNS} FROM task_outcomes "
            "WHERE outcome_id = ? AND workspace_id = ?",
            (outcome_id, workspace_id),
        )
        if row is None:
            return None
        return _outcome_from_row(row)

    def list_task_outcomes(
        self, workspace_id: str, task_run_id: str
    ) -> tuple[DurableTaskOutcome, ...]:
        rows = self._read_all(
            f"SELECT {_OUTCOME_COLUMNS} FROM task_outcomes "
            "WHERE workspace_id = ? AND task_run_id = ? ORDER BY version ASC",
            (workspace_id, task_run_id),
        )
        return tuple(_outcome_from_row(row) for row in rows)

    def reserve_artifact(self, workspace_id: str, metadata: ArtifactMetadata) -> ArtifactMetadata:
        """Reserve metadata before any managed file is published."""

        if metadata.workspace_id != workspace_id:
            raise StorageError(
                StorageErrorCode.UNAVAILABLE, "operational artifact is outside the workspace"
            )
        if metadata.state is not ArtifactState.STAGING:
            raise StorageError(
                StorageErrorCode.UNAVAILABLE, "new operational artifact must start staging"
            )

        def work(journal: SqliteOperationalJournal) -> ArtifactMetadata:
            journal._validate_artifact_scope(workspace_id, metadata)
            if metadata.task_run_id is not None:
                used = journal.artifact_bytes_for_task(workspace_id, metadata.task_run_id)
                if used + metadata.byte_size > TASK_ARTIFACT_MAX_BYTES:
                    raise ArtifactBudgetError("TaskRun artifact byte budget exceeded")
            journal._executor_or_raise().execute(
                f"INSERT INTO artifacts({_ARTIFACT_COLUMNS}) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    metadata.artifact_id,
                    metadata.workspace_id,
                    metadata.session_id,
                    metadata.task_run_id,
                    metadata.kind.value,
                    metadata.sensitivity.value,
                    metadata.state.value,
                    metadata.retention.value,
                    metadata.sha256,
                    metadata.byte_size,
                    metadata.excerpt,
                    _optional_json(metadata.provenance_refs),
                    metadata.row_version,
                    _unix(metadata.created_at),
                    _unix(metadata.updated_at),
                ),
            )
            loaded = journal.get_artifact(workspace_id, metadata.artifact_id)
            if loaded is None:
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE, "operational artifact could not be read"
                )
            return loaded

        return self.transact(work)

    def get_artifact(self, workspace_id: str, artifact_id: str) -> ArtifactMetadata | None:
        row = self._read_one(
            f"SELECT {_ARTIFACT_COLUMNS} FROM artifacts WHERE artifact_id = ? AND workspace_id = ?",
            (artifact_id, workspace_id),
        )
        if row is None:
            return None
        return _artifact_from_row(row)

    def list_artifacts(
        self,
        workspace_id: str,
        *,
        session_id: str | None = None,
        task_run_id: str | None = None,
    ) -> tuple[ArtifactMetadata, ...]:
        sql = f"SELECT {_ARTIFACT_COLUMNS} FROM artifacts WHERE workspace_id = ?"
        parameters: list[object] = [workspace_id]
        if session_id is not None:
            sql += " AND session_id = ?"
            parameters.append(session_id)
        if task_run_id is not None:
            sql += " AND task_run_id = ?"
            parameters.append(task_run_id)
        sql += " ORDER BY created_at_unix ASC, artifact_id ASC"
        rows = self._read_all(sql, tuple(parameters))
        return tuple(_artifact_from_row(row) for row in rows)

    def save_artifact(
        self,
        workspace_id: str,
        metadata: ArtifactMetadata,
        *,
        expected_row_version: int,
    ) -> ArtifactMetadata:
        if metadata.workspace_id != workspace_id:
            raise StorageError(
                StorageErrorCode.UNAVAILABLE, "operational artifact is outside the workspace"
            )

        def work(journal: SqliteOperationalJournal) -> ArtifactMetadata:
            existing = journal.get_artifact(workspace_id, metadata.artifact_id)
            if existing is None:
                raise StorageError(StorageErrorCode.NOT_FOUND, "operational artifact is missing")
            if existing.row_version != expected_row_version:
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE, "operational artifact row version is stale"
                )
            if metadata.row_version != expected_row_version + 1:
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE, "operational artifact row version is stale"
                )
            immutable_fields = (
                "workspace_id",
                "session_id",
                "task_run_id",
                "kind",
                "sensitivity",
                "sha256",
                "byte_size",
                "provenance_refs",
                "created_at",
            )
            if any(
                getattr(existing, field) != getattr(metadata, field) for field in immutable_fields
            ):
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE, "operational artifact identity is immutable"
                )
            _validate_artifact_state(existing.state, metadata.state)
            journal._executor_or_raise().execute(
                """
                UPDATE artifacts
                SET state = ?, retention = ?, excerpt = ?, row_version = ?, updated_at_unix = ?
                WHERE artifact_id = ? AND workspace_id = ? AND row_version = ?
                """,
                (
                    metadata.state.value,
                    metadata.retention.value,
                    metadata.excerpt,
                    metadata.row_version,
                    _unix(metadata.updated_at),
                    metadata.artifact_id,
                    workspace_id,
                    expected_row_version,
                ),
            )
            loaded = journal.get_artifact(workspace_id, metadata.artifact_id)
            if loaded is None or loaded.row_version != metadata.row_version:
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE, "operational artifact row version is stale"
                )
            return loaded

        return self.transact(work)

    def artifact_bytes_for_task(self, workspace_id: str, task_run_id: str) -> int:
        row = self._read_one(
            "SELECT COALESCE(SUM(byte_size), 0) FROM artifacts "
            "WHERE workspace_id = ? AND task_run_id = ?",
            (workspace_id, task_run_id),
        )
        return int(row[0]) if row is not None else 0

    def list_artifact_references(
        self, workspace_id: str, artifact_id: str | None = None
    ) -> tuple[tuple[str, str, str, str], ...]:
        sql = (
            f"SELECT {_ARTIFACT_REFERENCE_COLUMNS} FROM artifact_references WHERE workspace_id = ?"
        )
        parameters: list[object] = [workspace_id]
        if artifact_id is not None:
            sql += " AND artifact_id = ?"
            parameters.append(artifact_id)
        rows = self._read_all(sql, tuple(parameters))
        checkpoint_sql = (
            "SELECT artifact_id, workspace_id, checkpoint_id, role "
            "FROM checkpoint_artifact_references WHERE workspace_id = ?"
        )
        checkpoint_parameters: list[object] = [workspace_id]
        if artifact_id is not None:
            checkpoint_sql += " AND artifact_id = ?"
            checkpoint_parameters.append(artifact_id)
        checkpoint_rows = self._read_all(checkpoint_sql, tuple(checkpoint_parameters))
        references = [(str(row[0]), str(row[2]), str(row[3]), str(row[4])) for row in rows]
        references.extend(
            (str(row[0]), "context_checkpoint", str(row[2]), str(row[3])) for row in checkpoint_rows
        )
        return tuple(sorted(references, key=lambda item: (item[0], item[1], item[2], item[3])))

    def _validate_artifact_scope(self, workspace_id: str, metadata: ArtifactMetadata) -> None:
        if metadata.session_id is None:
            if metadata.task_run_id is not None:
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE, "operational artifact scope is inconsistent"
                )
            return
        session = self.get_session(workspace_id, metadata.session_id)
        if session is None:
            raise StorageError(
                StorageErrorCode.NOT_FOUND, "operational artifact session is missing"
            )
        if metadata.task_run_id is not None:
            task = self.get_task_run(workspace_id, metadata.task_run_id)
            if task is None or task.session_id != metadata.session_id:
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE, "operational artifact task scope is invalid"
                )

    def _replace_artifact_references(
        self,
        workspace_id: str,
        *,
        owner_kind: str,
        owner_id: str,
        references: tuple[ArtifactReference, ...],
        created_at: datetime,
    ) -> None:
        executor = self._executor_or_raise()
        executor.execute(
            "DELETE FROM artifact_references WHERE workspace_id = ? AND owner_kind = ? "
            "AND owner_id = ?",
            (workspace_id, owner_kind, owner_id),
        )
        for reference in references:
            executor.execute(
                f"INSERT INTO artifact_references({_ARTIFACT_REFERENCE_COLUMNS}) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    reference.artifact_id,
                    workspace_id,
                    owner_kind,
                    owner_id,
                    reference.role,
                    _unix(created_at),
                ),
            )

    def get_task_command_receipt(
        self, workspace_id: str, command_id: str
    ) -> TaskCommandReceipt | None:
        row = self._read_one(
            f"SELECT {_TASK_RECEIPT_COLUMNS} FROM task_command_receipts WHERE command_id = ?",
            (command_id,),
        )
        if row is None:
            return None
        receipt = _task_receipt_from_row(row)
        if receipt.workspace_id != workspace_id:
            raise StorageError(
                StorageErrorCode.UNAVAILABLE, "operational task command is outside the workspace"
            )
        return receipt

    def put_task_command_receipt(
        self, workspace_id: str, receipt: TaskCommandReceipt
    ) -> TaskCommandReceipt:
        if receipt.workspace_id != workspace_id:
            raise StorageError(
                StorageErrorCode.UNAVAILABLE, "operational task command is outside the workspace"
            )

        def work(journal: SqliteOperationalJournal) -> TaskCommandReceipt:
            if journal.get_session(workspace_id, receipt.session_id) is None:
                raise StorageError(StorageErrorCode.NOT_FOUND, "operational session is missing")
            if receipt.task_run_id is not None:
                task = journal.get_task_run(workspace_id, receipt.task_run_id)
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
                result_task = journal.get_task_run(workspace_id, receipt.result_task_run_id)
                if result_task is None or result_task.session_id != receipt.session_id:
                    raise StorageError(
                        StorageErrorCode.UNAVAILABLE,
                        "operational task command result is invalid",
                    )
            if receipt.outcome_id is not None:
                outcome = journal.get_task_outcome(workspace_id, receipt.outcome_id)
                if outcome is None or outcome.session_id != receipt.session_id:
                    raise StorageError(
                        StorageErrorCode.UNAVAILABLE,
                        "operational task command outcome is invalid",
                    )
            journal._executor_or_raise().execute(
                f"INSERT INTO task_command_receipts({_TASK_RECEIPT_COLUMNS}) "
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
            loaded = journal.get_task_command_receipt(workspace_id, receipt.command_id)
            if loaded is None:
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE,
                    "operational task command receipt could not be read",
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

    def list_task_turns(self, workspace_id: str, task_run_id: str) -> tuple[DurableTurn, ...]:
        rows = self._read_all(
            "SELECT t.turn_id, t.session_id, t.task_run_id, t.client_message_id, "
            "t.created_at_unix FROM turns t "
            "JOIN sessions s ON s.session_id = t.session_id "
            "WHERE t.task_run_id = ? AND s.workspace_id = ? "
            "ORDER BY t.created_at_unix ASC, t.turn_id ASC",
            (task_run_id, workspace_id),
        )
        return tuple(_turn_from_row(row) for row in rows)

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

    def put_context_checkpoint(
        self, workspace_id: str, checkpoint: ContextCheckpoint
    ) -> ContextCheckpoint:
        if checkpoint.workspace_id != workspace_id:
            raise StorageError(
                StorageErrorCode.UNAVAILABLE, "context checkpoint is outside the workspace"
            )

        def work(journal: SqliteOperationalJournal) -> ContextCheckpoint:
            existing = journal.get_context_checkpoint(workspace_id, checkpoint.checkpoint_id)
            if existing is not None:
                if existing.model_dump(mode="json") != checkpoint.model_dump(mode="json"):
                    raise StorageError(
                        StorageErrorCode.UNAVAILABLE, "context checkpoint identity is immutable"
                    )
                return existing
            journal._validate_checkpoint_scope(workspace_id, checkpoint)
            executor = journal._executor_or_raise()
            executor.execute(
                """
                INSERT INTO context_checkpoints(
                    checkpoint_id, workspace_id, session_id, task_run_id,
                    source_agent_run_id, codec, method_version, source_start_record_id,
                    source_start_position, source_end_record_id, source_end_position,
                    retained_record_ids_json, sections_json, omitted_sections_json,
                    artifact_refs_json, input_bytes, output_bytes, request_estimate_chars,
                    created_at_unix
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    checkpoint.checkpoint_id,
                    checkpoint.workspace_id,
                    checkpoint.session_id,
                    checkpoint.task_run_id,
                    checkpoint.source_agent_run_id,
                    checkpoint.codec,
                    checkpoint.method_version,
                    checkpoint.source_start_record_id,
                    checkpoint.source_start_position,
                    checkpoint.source_end_record_id,
                    checkpoint.source_end_position,
                    _optional_json(checkpoint.retained_record_ids),
                    _optional_json(checkpoint.sections),
                    _optional_json(checkpoint.omitted_sections),
                    _optional_json(checkpoint.artifact_refs),
                    checkpoint.input_bytes,
                    checkpoint.output_bytes,
                    checkpoint.request_estimate_chars,
                    _unix(checkpoint.created_at),
                ),
            )
            for reference in checkpoint.artifact_refs:
                executor.execute(
                    f"INSERT INTO checkpoint_artifact_references({_CHECKPOINT_ARTIFACT_REFERENCE_COLUMNS}) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        reference.artifact_id,
                        workspace_id,
                        checkpoint.checkpoint_id,
                        reference.role,
                        _unix(checkpoint.created_at),
                    ),
                )
            loaded = journal.get_context_checkpoint(workspace_id, checkpoint.checkpoint_id)
            if loaded is None:
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE, "context checkpoint could not be read"
                )
            return loaded

        return self.transact(work)

    def get_context_checkpoint(
        self, workspace_id: str, checkpoint_id: str
    ) -> ContextCheckpoint | None:
        row = self._read_one(
            "SELECT checkpoint_id, workspace_id, session_id, task_run_id, "
            "source_agent_run_id, codec, method_version, source_start_record_id, "
            "source_start_position, source_end_record_id, source_end_position, "
            "retained_record_ids_json, sections_json, omitted_sections_json, "
            "artifact_refs_json, input_bytes, output_bytes, request_estimate_chars, "
            "created_at_unix FROM context_checkpoints "
            "WHERE checkpoint_id = ? AND workspace_id = ?",
            (checkpoint_id, workspace_id),
        )
        if row is None:
            return None
        return _checkpoint_from_row(row)

    def list_context_checkpoints(
        self, workspace_id: str, session_id: str, *, task_run_id: str | None = None
    ) -> tuple[ContextCheckpoint, ...]:
        sql = (
            "SELECT checkpoint_id, workspace_id, session_id, task_run_id, "
            "source_agent_run_id, codec, method_version, source_start_record_id, "
            "source_start_position, source_end_record_id, source_end_position, "
            "retained_record_ids_json, sections_json, omitted_sections_json, "
            "artifact_refs_json, input_bytes, output_bytes, request_estimate_chars, "
            "created_at_unix FROM context_checkpoints "
            "WHERE workspace_id = ? AND session_id = ?"
        )
        parameters: list[object] = [workspace_id, session_id]
        if task_run_id is not None:
            sql += " AND task_run_id = ?"
            parameters.append(task_run_id)
        sql += " ORDER BY source_end_position ASC, created_at_unix ASC, checkpoint_id ASC"
        rows = self._read_all(sql, tuple(parameters))
        return tuple(_checkpoint_from_row(row) for row in rows)

    def _validate_checkpoint_scope(self, workspace_id: str, checkpoint: ContextCheckpoint) -> None:
        session = self.get_session(workspace_id, checkpoint.session_id)
        if session is None:
            raise StorageError(StorageErrorCode.NOT_FOUND, "checkpoint Session is missing")
        if checkpoint.task_run_id is not None:
            task = self.get_task_run(workspace_id, checkpoint.task_run_id)
            if task is None or task.session_id != checkpoint.session_id:
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE, "checkpoint TaskRun scope is invalid"
                )
        if checkpoint.source_agent_run_id is not None:
            run = self.get_agent_run(workspace_id, checkpoint.source_agent_run_id)
            if run is None or run.session_id != checkpoint.session_id:
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE, "checkpoint AgentRun scope is invalid"
                )
        records = self.load_effective_records(workspace_id, checkpoint.session_id)
        by_position = {record.conversation_position: record for record in records}
        if checkpoint.source_end_position > session.conversation_position + 1:
            raise StorageError(StorageErrorCode.UNAVAILABLE, "checkpoint source range is invalid")
        if checkpoint.source_end_position - 1 not in by_position:
            raise StorageError(StorageErrorCode.UNAVAILABLE, "checkpoint source end is missing")
        end_record = by_position[checkpoint.source_end_position - 1]
        if end_record.record_id != checkpoint.source_end_record_id or end_record.kind != "terminal":
            raise StorageError(StorageErrorCode.UNAVAILABLE, "checkpoint must end at a closed Turn")
        if checkpoint.source_start_position:
            start_record = by_position.get(checkpoint.source_start_position)
            if start_record is None or start_record.record_id != checkpoint.source_start_record_id:
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE, "checkpoint source start is invalid"
                )
        elif checkpoint.source_start_record_id is not None:
            raise StorageError(StorageErrorCode.UNAVAILABLE, "checkpoint source start is invalid")
        valid_ids = {
            record.record_id
            for record in records
            if checkpoint.source_start_position
            <= record.conversation_position
            < checkpoint.source_end_position
        }
        if not set(checkpoint.retained_record_ids).issubset(valid_ids):
            raise StorageError(
                StorageErrorCode.UNAVAILABLE, "checkpoint retained records are invalid"
            )
        for section in checkpoint.sections:
            self._validate_checkpoint_section(section, checkpoint, valid_ids)
        for omission in checkpoint.omitted_sections:
            if not (
                checkpoint.source_start_position <= omission.source_start_position
                and omission.source_end_position <= checkpoint.source_end_position
            ):
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE, "checkpoint omission range is invalid"
                )
            if not set(omission.record_ids).issubset(valid_ids):
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE, "checkpoint omission records are invalid"
                )
        self._validate_artifact_refs(
            workspace_id,
            checkpoint.artifact_refs,
            session_id=checkpoint.session_id,
            task_run_id=checkpoint.task_run_id,
            require_available=False,
        )

    def _validate_artifact_refs(
        self,
        workspace_id: str,
        references: tuple[ArtifactReference, ...],
        *,
        session_id: str,
        task_run_id: str | None,
        require_available: bool = True,
    ) -> None:
        for reference in references:
            artifact = self.get_artifact(workspace_id, reference.artifact_id)
            if artifact is None:
                raise StorageError(StorageErrorCode.NOT_FOUND, "operational artifact is missing")
            if artifact.session_id not in {None, session_id} or artifact.task_run_id not in {
                None,
                task_run_id,
            }:
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE, "operational artifact reference scope is invalid"
                )
            if require_available and artifact.state is not ArtifactState.AVAILABLE:
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE, "only available artifacts may be referenced"
                )

    def _validate_checkpoint_section(
        self,
        section: ContextCheckpointSection,
        checkpoint: ContextCheckpoint,
        valid_ids: set[str],
    ) -> None:
        if not (
            checkpoint.source_start_position <= section.source_start_position
            and section.source_end_position <= checkpoint.source_end_position
        ):
            raise StorageError(StorageErrorCode.UNAVAILABLE, "checkpoint section range is invalid")
        if not set(reference.artifact_id for reference in section.artifact_refs).issubset(
            {reference.artifact_id for reference in checkpoint.artifact_refs}
        ):
            raise StorageError(
                StorageErrorCode.UNAVAILABLE, "checkpoint section artifact is invalid"
            )
        if not valid_ids:
            raise StorageError(StorageErrorCode.UNAVAILABLE, "checkpoint source range is empty")

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

    def list_session_executions(
        self, workspace_id: str, session_id: str
    ) -> tuple[DurableToolExecution, ...]:
        rows = self._read_all(
            f"SELECT {_EXECUTION_COLUMNS} FROM tool_executions "
            "WHERE workspace_id = ? AND session_id = ? "
            "ORDER BY created_at_unix ASC, ordinal ASC",
            (workspace_id, session_id),
        )
        return tuple(_execution_from_row(row) for row in rows)

    def list_task_executions(
        self, workspace_id: str, task_run_id: str
    ) -> tuple[DurableToolExecution, ...]:
        rows = self._read_all(
            f"SELECT {_EXECUTION_COLUMNS} FROM tool_executions "
            "WHERE workspace_id = ? AND task_run_id = ? "
            "ORDER BY created_at_unix ASC, ordinal ASC, tool_execution_id ASC",
            (workspace_id, task_run_id),
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
            journal._validate_artifact_refs(
                workspace_id,
                execution.artifact_refs,
                session_id=execution.session_id,
                task_run_id=execution.task_run_id,
            )
            journal._executor_or_raise().execute(
                """
                UPDATE tool_executions
                SET state = ?, disposition = ?, row_version = ?, approval_id = ?,
                    result_envelope_json = ?, facts_json = ?, error_code = ?,
                    error_detail = ?, executing_at_unix = ?,
                    handler_completed_at_unix = ?, closed_at_unix = ?, artifact_refs_json = ?
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
                    _optional_json(execution.artifact_refs),
                    execution.tool_execution_id,
                    workspace_id,
                    expected_row_version,
                ),
            )
            journal._replace_artifact_references(
                workspace_id,
                owner_kind="tool_execution",
                owner_id=execution.tool_execution_id,
                references=execution.artifact_refs,
                created_at=execution.created_at,
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

    def put_report(self, workspace_id: str, report: RecoveryReport) -> RecoveryReport:
        if report.workspace_id != workspace_id:
            raise StorageError(
                StorageErrorCode.UNAVAILABLE, "operational recovery report is outside the workspace"
            )

        def work(journal: SqliteOperationalJournal) -> RecoveryReport:
            if journal.get_session(workspace_id, report.session_id) is None:
                raise StorageError(StorageErrorCode.NOT_FOUND, "operational session is missing")
            payload = canonical_json_bytes(report.model_dump(mode="json"))
            journal._executor_or_raise().execute(
                f"INSERT INTO recovery_reports({_REPORT_COLUMNS}) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    report.report_id,
                    report.workspace_id,
                    report.session_id,
                    report.turn_id,
                    report.agent_run_id,
                    report.status.value,
                    payload.decode("utf-8"),
                    len(payload),
                    _unix(report.created_at),
                    _optional_unix(report.resolved_at),
                ),
            )
            loaded = journal.get_open_report(workspace_id, report.session_id)
            if loaded is None:
                loaded = journal.get_report(workspace_id, report.report_id)
            if loaded is None:
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE, "operational recovery report could not be read"
                )
            return loaded

        return self.transact(work)

    def get_report(self, workspace_id: str, report_id: str) -> RecoveryReport | None:
        row = self._read_one(
            f"SELECT {_REPORT_COLUMNS} FROM recovery_reports "
            "WHERE report_id = ? AND workspace_id = ?",
            (report_id, workspace_id),
        )
        if row is None:
            return None
        return _report_from_row(row)

    def get_open_report(self, workspace_id: str, session_id: str) -> RecoveryReport | None:
        row = self._read_one(
            f"SELECT {_REPORT_COLUMNS} FROM recovery_reports "
            "WHERE workspace_id = ? AND session_id = ? AND status = 'open'",
            (workspace_id, session_id),
        )
        if row is None:
            return None
        return _report_from_row(row)

    def save_report(self, workspace_id: str, report: RecoveryReport) -> RecoveryReport:
        if report.workspace_id != workspace_id:
            raise StorageError(
                StorageErrorCode.UNAVAILABLE, "operational recovery report is outside the workspace"
            )

        def work(journal: SqliteOperationalJournal) -> RecoveryReport:
            existing = journal.get_report(workspace_id, report.report_id)
            if existing is None:
                raise StorageError(
                    StorageErrorCode.NOT_FOUND, "operational recovery report is missing"
                )
            payload = canonical_json_bytes(report.model_dump(mode="json"))
            journal._executor_or_raise().execute(
                """
                UPDATE recovery_reports
                SET status = ?, payload_json = ?, payload_bytes = ?, resolved_at_unix = ?
                WHERE report_id = ? AND workspace_id = ?
                """,
                (
                    report.status.value,
                    payload.decode("utf-8"),
                    len(payload),
                    _optional_unix(report.resolved_at),
                    report.report_id,
                    workspace_id,
                ),
            )
            loaded = journal.get_report(workspace_id, report.report_id)
            if loaded is None:
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE, "operational recovery report could not be read"
                )
            return loaded

        return self.transact(work)

    def get_recovery_receipt(
        self, workspace_id: str, session_id: str, command_id: str
    ) -> RecoveryReceipt | None:
        if self.get_session(workspace_id, session_id) is None:
            return None
        row = self._read_one(
            f"SELECT {_RECOVERY_RECEIPT_COLUMNS} FROM recovery_receipts "
            "WHERE session_id = ? AND command_id = ?",
            (session_id, command_id),
        )
        if row is None:
            return None
        return _recovery_receipt_from_row(row)

    def put_recovery_receipt(self, workspace_id: str, receipt: RecoveryReceipt) -> RecoveryReceipt:
        def work(journal: SqliteOperationalJournal) -> RecoveryReceipt:
            if journal.get_session(workspace_id, receipt.session_id) is None:
                raise StorageError(StorageErrorCode.NOT_FOUND, "operational session is missing")
            journal._executor_or_raise().execute(
                f"INSERT INTO recovery_receipts({_RECOVERY_RECEIPT_COLUMNS}) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    receipt.session_id,
                    receipt.command_id,
                    receipt.request_digest,
                    receipt.report_id,
                    receipt.item_id,
                    receipt.resolution.value,
                ),
            )
            loaded = journal.get_recovery_receipt(
                workspace_id, receipt.session_id, receipt.command_id
            )
            if loaded is None:
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE, "operational recovery receipt could not be read"
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
        self._validate_artifact_refs(
            workspace_id,
            execution.artifact_refs,
            session_id=execution.session_id,
            task_run_id=execution.task_run_id,
        )
        self._executor_or_raise().execute(
            f"INSERT INTO tool_executions({_EXECUTION_COLUMNS}) "
            f"VALUES ({', '.join('?' for _ in range(28))})",
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
                _optional_json(execution.artifact_refs),
            ),
        )
        self._replace_artifact_references(
            workspace_id,
            owner_kind="tool_execution",
            owner_id=execution.tool_execution_id,
            references=execution.artifact_refs,
            created_at=execution.created_at,
        )

    def _insert_session(self, session: DurableSession) -> None:
        self._executor_or_raise().execute(
            f"INSERT INTO sessions({_SESSION_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                session.session_id,
                session.workspace_id,
                session.lifecycle.value,
                session.health.value,
                session.current_task_run_id,
                session.conversation_position,
                session.parent_session_id,
                session.parent_cut_record_id,
                session.parent_cut_position,
                session.parent_checkpoint_id,
                session.fork_reason,
                _unix(session.created_at),
                _unix(session.updated_at),
            ),
        )

    def _validate_session_lineage(self, session: DurableSession) -> None:
        if session.parent_session_id is None:
            return
        parent = self.get_session(session.workspace_id, session.parent_session_id)
        if parent is None:
            raise StorageError(StorageErrorCode.NOT_FOUND, "fork parent Session is missing")
        if parent.lifecycle is SessionLifecycle.DELETED:
            raise StorageError(StorageErrorCode.UNAVAILABLE, "deleted Session cannot be forked")
        if session.current_task_run_id is not None:
            raise StorageError(StorageErrorCode.UNAVAILABLE, "forked Session cannot have a TaskRun")
        parent_records = self.load_effective_records(session.workspace_id, parent.session_id)
        cut = parent_records[: session.parent_cut_position]
        if len(cut) != session.parent_cut_position:
            raise StorageError(
                StorageErrorCode.UNAVAILABLE, "fork cut is outside the parent history"
            )
        cut_record = cut[-1]
        if cut_record.record_id != session.parent_cut_record_id or cut_record.kind != "terminal":
            raise StorageError(StorageErrorCode.UNAVAILABLE, "fork cut must end at a closed Turn")
        if session.parent_checkpoint_id is not None:
            checkpoint = self.get_context_checkpoint(
                session.workspace_id, session.parent_checkpoint_id
            )
            if checkpoint is None or checkpoint.session_id != parent.session_id:
                raise StorageError(StorageErrorCode.UNAVAILABLE, "fork checkpoint is invalid")
            if (
                checkpoint.source_end_position != session.parent_cut_position + 1
                or checkpoint.source_end_record_id != session.parent_cut_record_id
            ):
                raise StorageError(StorageErrorCode.UNAVAILABLE, "fork checkpoint cut is invalid")

    def create_fork_session(
        self, session: DurableSession, *, lineage: SessionLineage
    ) -> DurableSession:
        """Create a child Session after validating the immutable lineage contract."""

        if (
            lineage.workspace_id != session.workspace_id
            or lineage.child_session_id != session.session_id
            or lineage.parent_session_id != session.parent_session_id
            or lineage.cut_record_id != session.parent_cut_record_id
            or lineage.cut_position != session.parent_cut_position
            or lineage.checkpoint_id != session.parent_checkpoint_id
        ):
            raise StorageError(StorageErrorCode.UNAVAILABLE, "fork Session lineage is inconsistent")
        if session.fork_reason != lineage.reason:
            raise StorageError(StorageErrorCode.UNAVAILABLE, "fork Session reason is inconsistent")
        return self.create_session(session)

    def _insert_task(self, task: DurableTaskRun) -> None:
        self._executor_or_raise().execute(
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
        parent_session_id=str(row[6]) if row[6] is not None else None,
        parent_cut_record_id=str(row[7]) if row[7] is not None else None,
        parent_cut_position=int(row[8]) if row[8] is not None else None,
        parent_checkpoint_id=str(row[9]) if row[9] is not None else None,
        fork_reason=str(row[10]) if row[10] is not None else None,
        created_at=_from_unix(row[11]),
        updated_at=_from_unix(row[12]),
    )


def _session_lineage_fields(session: DurableSession) -> tuple[object, ...]:
    return (
        session.parent_session_id,
        session.parent_cut_record_id,
        session.parent_cut_position,
        session.parent_checkpoint_id,
        session.fork_reason,
    )


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


def _checkpoint_from_row(row: tuple[object, ...]) -> ContextCheckpoint:
    try:
        retained = json.loads(str(row[11]))
        sections = json.loads(str(row[12]))
        omissions = json.loads(str(row[13]))
        artifacts = json.loads(str(row[14]))
        if not all(isinstance(value, list) for value in (retained, sections, omissions, artifacts)):
            raise ValueError("checkpoint JSON columns are not lists")
        return ContextCheckpoint(
            checkpoint_id=str(row[0]),
            workspace_id=str(row[1]),
            session_id=str(row[2]),
            task_run_id=str(row[3]) if row[3] is not None else None,
            source_agent_run_id=str(row[4]) if row[4] is not None else None,
            codec=str(row[5]),
            method_version=str(row[6]),
            source_start_record_id=str(row[7]) if row[7] is not None else None,
            source_start_position=int(row[8]),
            source_end_record_id=str(row[9]),
            source_end_position=int(row[10]),
            retained_record_ids=tuple(str(item) for item in retained),
            sections=tuple(ContextCheckpointSection.model_validate(item) for item in sections),
            omitted_sections=tuple(
                ContextCheckpointOmission.model_validate(item) for item in omissions
            ),
            artifact_refs=tuple(ArtifactReference.model_validate(item) for item in artifacts),
            input_bytes=int(row[15]),
            output_bytes=int(row[16]),
            request_estimate_chars=int(row[17]),
            created_at=_from_unix(row[18]),
        )
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise StorageError(StorageErrorCode.NEEDS_REPAIR, "context checkpoint is invalid") from exc


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


def _task_receipt_from_row(row: tuple[object, ...]) -> TaskCommandReceipt:
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
    if hasattr(value, "model_dump"):
        dumped = value.model_dump(mode="json")
    elif isinstance(value, (tuple, list)):
        dumped = [
            item.model_dump(mode="json") if hasattr(item, "model_dump") else item for item in value
        ]
    else:
        dumped = value
    return canonical_json_bytes(dumped).decode("utf-8")


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


def _validate_artifact_state(current: ArtifactState, target: ArtifactState) -> None:
    if current is target:
        return
    allowed = {
        ArtifactState.STAGING: {
            ArtifactState.AVAILABLE,
            ArtifactState.MISSING,
            ArtifactState.CORRUPT,
        },
        ArtifactState.AVAILABLE: {ArtifactState.MISSING, ArtifactState.CORRUPT},
        ArtifactState.MISSING: set(),
        ArtifactState.CORRUPT: set(),
    }
    if target not in allowed[current]:
        raise StorageError(
            StorageErrorCode.UNAVAILABLE,
            "operational artifact state transition is invalid",
        )


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
    artifact_refs = _artifact_refs_from_raw(row[27])
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
        artifact_refs=artifact_refs,
        error_code=str(row[21]) if row[21] is not None else None,
        error_detail=str(row[22]) if row[22] is not None else None,
        created_at=_from_unix(row[23]),
        executing_at=_from_unix(row[24]) if row[24] is not None else None,
        handler_completed_at=_from_unix(row[25]) if row[25] is not None else None,
        closed_at=_from_unix(row[26]) if row[26] is not None else None,
    )


def _artifact_from_row(row: tuple[object, ...]) -> ArtifactMetadata:
    try:
        provenance_raw = json.loads(str(row[11]))
        if not isinstance(provenance_raw, list):
            raise ValueError("artifact provenance is not a list")
        metadata = ArtifactMetadata(
            artifact_id=str(row[0]),
            workspace_id=str(row[1]),
            session_id=str(row[2]) if row[2] is not None else None,
            task_run_id=str(row[3]) if row[3] is not None else None,
            kind=ArtifactKind(str(row[4])),
            sensitivity=ArtifactSensitivity(str(row[5])),
            state=ArtifactState(str(row[6])),
            retention=ArtifactRetention(str(row[7])),
            sha256=str(row[8]),
            byte_size=int(row[9]),
            excerpt=str(row[10]),
            provenance_refs=tuple(
                ArtifactProvenanceRef.model_validate(item) for item in provenance_raw
            ),
            row_version=int(row[12]),
            created_at=_from_unix(row[13]),
            updated_at=_from_unix(row[14]),
        )
        return metadata
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise StorageError(
            StorageErrorCode.NEEDS_REPAIR, "operational artifact metadata is invalid"
        ) from exc


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


def _report_from_row(row: tuple[object, ...]) -> RecoveryReport:
    payload = json.loads(str(row[6]))
    if not isinstance(payload, dict):
        raise StorageError(StorageErrorCode.NEEDS_REPAIR, "recovery report is not a mapping")
    return RecoveryReport.model_validate(payload)


def _recovery_receipt_from_row(row: tuple[object, ...]) -> RecoveryReceipt:
    return RecoveryReceipt(
        session_id=str(row[0]),
        command_id=str(row[1]),
        request_digest=str(row[2]),
        report_id=str(row[3]),
        item_id=str(row[4]) if row[4] is not None else None,
        resolution=RecoveryResolution(str(row[5])),
    )
