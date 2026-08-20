"""SQLite adapter for Session lifecycle and the conversation journal."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import UTC, datetime

from morrow.adapters.state.application_journal import SqliteApplicationJournal
from morrow.adapters.state.artifact_journal import SqliteArtifactJournal
from morrow.adapters.state.context_journal import SqliteContextJournal
from morrow.adapters.state.conversation_journal import SqliteConversationJournal
from morrow.adapters.state.operational import OperationalStoreSession, SqliteExecutor
from morrow.adapters.state.permission_journal import SqliteRunPermissionJournal
from morrow.adapters.state.recovery_journal import SqliteRecoveryJournal
from morrow.adapters.state.task_journal import SqliteTaskJournal
from morrow.adapters.state.tool_journal import SqliteToolJournal
from morrow.adapters.state.transaction import SqliteJournalBackend
from morrow.core.application import (
    ApplicationCommandReceipt,
    ApplicationEvent,
)
from morrow.core.artifacts import (
    ArtifactMetadata,
    ArtifactState,
)
from morrow.core.context import (
    ContextCheckpoint,
    SessionLineage,
)
from morrow.core.domain import (
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
    TaskCommandReceipt,
    TaskRunStatus,
    TurnSubmitReceipt,
    session_can_start_work,
)
from morrow.core.execution import (
    DurableApproval,
    DurableToolExecution,
)
from morrow.core.permissions import (
    CapabilityGrant,
    PermissionSnapshot,
)
from morrow.core.recovery import RecoveryReceipt, RecoveryReport
from morrow.core.store import StorageError, StorageErrorCode

_SESSION_COLUMNS = (
    "session_id, workspace_id, lifecycle, health, current_task_run_id, "
    "conversation_position, parent_session_id, parent_cut_record_id, parent_cut_position, "
    "parent_checkpoint_id, fork_reason, created_at_unix, updated_at_unix"
)


def _unix(value: datetime) -> int:
    return int(value.timestamp())


def _from_unix(value: object) -> datetime:
    return datetime.fromtimestamp(int(value), UTC)


class SqliteOperationalJournal:
    """One SQLite adapter exposing the narrow lifecycle and journal ports."""

    def __init__(
        self,
        session: OperationalStoreSession,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._backend = SqliteJournalBackend(session, clock=clock)
        self._recovery_journal = SqliteRecoveryJournal(
            self._backend,
            session_exists=lambda workspace_id, session_id: (
                self.get_session(workspace_id, session_id) is not None
            ),
        )
        self._application_journal = SqliteApplicationJournal(
            self._backend,
            session_exists=lambda workspace_id, session_id: (
                self.get_session(workspace_id, session_id) is not None
            ),
        )
        self._artifact_journal = SqliteArtifactJournal(
            self._backend,
            session_exists=lambda workspace_id, session_id: (
                self.get_session(workspace_id, session_id) is not None
            ),
            task_belongs_to_session=self._task_belongs_to_session,
        )
        self._task_journal = SqliteTaskJournal(
            self._backend,
            get_session=self.get_session,
            turn_belongs_to_task=self._turn_belongs_to_task,
            validate_artifact_refs=self._validate_artifact_refs,
            replace_artifact_refs=self._replace_artifact_references,
        )
        self._conversation_journal = SqliteConversationJournal(
            self._backend,
            get_session=self.get_session,
            get_task=self.get_task_run,
            session_mutation_time=self._session_mutation_time,
        )
        self._permission_journal = SqliteRunPermissionJournal(
            self._backend,
            get_turn=self.get_turn,
            get_task=self.get_task_run,
        )
        self._context_journal = SqliteContextJournal(
            self._backend,
            get_session=self.get_session,
            get_task=self.get_task_run,
            get_agent_run=self.get_agent_run,
            load_effective_records=self.load_effective_records,
            validate_artifact_refs=self._validate_artifact_refs,
        )
        self._tool_journal = SqliteToolJournal(
            self._backend,
            get_agent_run=self.get_agent_run,
            get_task=self.get_task_run,
            get_permission_snapshot=self.get_permission_snapshot,
            get_capability_grant=self.get_capability_grant,
            validate_artifact_refs=self._validate_artifact_refs,
            replace_artifact_refs=self._replace_artifact_references,
        )

    def now(self) -> datetime:
        return self._backend.now()

    def supports_writes(self) -> bool:
        return self._backend.supports_writes()

    def schema_version(self) -> int:
        return self._backend.schema_version()

    def transaction_is_active(self) -> bool:
        return self._backend.transaction.active

    def transact[T](self, work: Callable[[SqliteOperationalJournal], T]) -> T:
        return self._backend.transact(lambda: work(self))

    def transact_once[T](self, work: Callable[[SqliteOperationalJournal], T]) -> T:
        """Run a non-replayable journal transaction for filesystem-coupled maintenance."""

        return self._backend.transact(lambda: work(self), replayable=False)

    def create_session(
        self, session: DurableSession, *, task: DurableTaskRun | None = None
    ) -> DurableSession:
        if (
            session.lifecycle is not SessionLifecycle.ACTIVE
            and session.current_task_run_id is not None
        ):
            raise StorageError(
                StorageErrorCode.UNAVAILABLE,
                "inactive session cannot retain a current task",
            )
        if task is not None:
            if not session_can_start_work(session.lifecycle, session.health):
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE,
                    "only an active healthy session can start a task",
                )
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

    def list_workspace_ids(self) -> tuple[str, ...]:
        rows = self._read_all(
            "SELECT workspace_id FROM sessions "
            "UNION SELECT workspace_id FROM artifacts "
            "UNION SELECT workspace_id FROM artifact_references "
            "UNION SELECT workspace_id FROM checkpoint_artifact_references "
            "UNION SELECT workspace_id FROM application_events "
            "ORDER BY workspace_id"
        )
        return tuple(str(row[0]) for row in rows)

    def has_global_artifact_authority(self, artifact_id: str) -> bool:
        """Check root-wide metadata/reference authority for one cleanup candidate."""

        row = self._read_one(
            """
            SELECT EXISTS (
                SELECT artifact_id FROM artifacts WHERE artifact_id = ?
                UNION ALL
                SELECT artifact_id FROM artifact_references WHERE artifact_id = ?
                UNION ALL
                SELECT artifact_id FROM checkpoint_artifact_references WHERE artifact_id = ?
            )
            """,
            (artifact_id, artifact_id, artifact_id),
        )
        return bool(row and row[0])

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
                if task.status.is_terminal:
                    raise StorageError(
                        StorageErrorCode.UNAVAILABLE,
                        "operational session cannot point at a terminal task",
                    )
            if (
                session.lifecycle is not SessionLifecycle.ACTIVE
                and session.current_task_run_id is not None
            ):
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE,
                    "inactive session cannot retain a current task",
                )
            visible_changed = (
                existing.lifecycle != session.lifecycle
                or existing.health != session.health
                or existing.current_task_run_id != session.current_task_run_id
                or existing.conversation_position != session.conversation_position
            )
            updated_at = (
                journal._session_mutation_time(existing, requested=session.updated_at)
                if visible_changed
                else existing.updated_at
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
                    _unix(updated_at),
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
        return self._task_journal.get(workspace_id, task_run_id)

    def _task_belongs_to_session(
        self, workspace_id: str, task_run_id: str, session_id: str
    ) -> bool:
        task = self.get_task_run(workspace_id, task_run_id)
        return task is not None and task.session_id == session_id

    def list_task_runs(self, workspace_id: str, session_id: str) -> tuple[DurableTaskRun, ...]:
        return self._task_journal.list(workspace_id, session_id)

    def create_task_run(
        self, workspace_id: str, task: DurableTaskRun, *, make_current: bool = False
    ) -> DurableTaskRun:
        return self._task_journal.create(workspace_id, task, make_current=make_current)

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

        return self._task_journal.transition(
            workspace_id,
            task_run_id,
            target=target,
            transition=transition,
            expected_row_version=expected_row_version,
        )

    def list_task_transitions(
        self, workspace_id: str, task_run_id: str
    ) -> tuple[DurableTaskRunTransition, ...]:
        return self._task_journal.list_transitions(workspace_id, task_run_id)

    def put_task_outcome(
        self, workspace_id: str, outcome: DurableTaskOutcome
    ) -> DurableTaskOutcome:
        return self._task_journal.put_outcome(workspace_id, outcome)

    def get_task_outcome(self, workspace_id: str, outcome_id: str) -> DurableTaskOutcome | None:
        return self._task_journal.get_outcome(workspace_id, outcome_id)

    def list_task_outcomes(
        self, workspace_id: str, task_run_id: str
    ) -> tuple[DurableTaskOutcome, ...]:
        return self._task_journal.list_outcomes(workspace_id, task_run_id)

    def reserve_artifact(self, workspace_id: str, metadata: ArtifactMetadata) -> ArtifactMetadata:
        """Reserve metadata before any managed file is published."""

        return self._artifact_journal.reserve(workspace_id, metadata)

    def get_artifact(self, workspace_id: str, artifact_id: str) -> ArtifactMetadata | None:
        return self._artifact_journal.get(workspace_id, artifact_id)

    def list_artifacts(
        self,
        workspace_id: str,
        *,
        session_id: str | None = None,
        task_run_id: str | None = None,
    ) -> tuple[ArtifactMetadata, ...]:
        return self._artifact_journal.list(
            workspace_id, session_id=session_id, task_run_id=task_run_id
        )

    def save_artifact(
        self,
        workspace_id: str,
        metadata: ArtifactMetadata,
        *,
        expected_row_version: int,
    ) -> ArtifactMetadata:
        return self._artifact_journal.save(
            workspace_id, metadata, expected_row_version=expected_row_version
        )

    def artifact_bytes_for_task(self, workspace_id: str, task_run_id: str) -> int:
        return self._artifact_journal.bytes_for_task(workspace_id, task_run_id)

    def list_artifact_references(
        self, workspace_id: str, artifact_id: str | None = None
    ) -> tuple[tuple[str, str, str, str], ...]:
        return self._artifact_journal.list_references(workspace_id, artifact_id)

    def _validate_artifact_scope(self, workspace_id: str, metadata: ArtifactMetadata) -> None:
        self._artifact_journal.validate_scope(workspace_id, metadata)

    def _replace_artifact_references(
        self,
        workspace_id: str,
        *,
        owner_kind: str,
        owner_id: str,
        references: tuple[ArtifactReference, ...],
        created_at: datetime,
    ) -> None:
        self._artifact_journal.replace_references(
            workspace_id,
            owner_kind=owner_kind,
            owner_id=owner_id,
            references=references,
            created_at=created_at,
        )

    def get_task_command_receipt(
        self, workspace_id: str, command_id: str
    ) -> TaskCommandReceipt | None:
        return self._task_journal.get_command_receipt(workspace_id, command_id)

    def put_task_command_receipt(
        self, workspace_id: str, receipt: TaskCommandReceipt
    ) -> TaskCommandReceipt:
        return self._task_journal.put_command_receipt(workspace_id, receipt)

    def get_application_event(self, workspace_id: str, event_id: str) -> ApplicationEvent | None:
        return self._application_journal.get_event(workspace_id, event_id)

    def list_application_events(
        self, workspace_id: str, *, after_cursor: int = 0, limit: int = 100
    ) -> tuple[ApplicationEvent, ...]:
        return self._application_journal.list_events(
            workspace_id, after_cursor=after_cursor, limit=limit
        )

    def put_application_event(self, workspace_id: str, event: ApplicationEvent) -> ApplicationEvent:
        return self._application_journal.put_event(workspace_id, event)

    def put_application_event_in_txn(
        self, workspace_id: str, event: ApplicationEvent
    ) -> ApplicationEvent:
        return self._application_journal.put_event_in_txn(workspace_id, event)

    def get_application_command_receipt(
        self, workspace_id: str, command_id: str
    ) -> ApplicationCommandReceipt | None:
        return self._application_journal.get_receipt(workspace_id, command_id)

    def put_application_command_receipt(
        self, workspace_id: str, receipt: ApplicationCommandReceipt
    ) -> ApplicationCommandReceipt:
        return self._application_journal.put_receipt(workspace_id, receipt)

    def put_application_command_receipt_in_txn(
        self, workspace_id: str, receipt: ApplicationCommandReceipt
    ) -> ApplicationCommandReceipt:
        return self._application_journal.put_receipt_in_txn(workspace_id, receipt)

    def create_turn(self, workspace_id: str, turn: DurableTurn) -> DurableTurn:
        return self._conversation_journal.create_turn(workspace_id, turn)

    def get_turn(self, workspace_id: str, turn_id: str) -> DurableTurn | None:
        return self._conversation_journal.get_turn(workspace_id, turn_id)

    def _turn_belongs_to_task(self, workspace_id: str, turn_id: str, task_run_id: str) -> bool:
        turn = self.get_turn(workspace_id, turn_id)
        return turn is not None and turn.task_run_id == task_run_id

    def list_task_turns(self, workspace_id: str, task_run_id: str) -> tuple[DurableTurn, ...]:
        return self._conversation_journal.list_task_turns(workspace_id, task_run_id)

    def list_session_turns(self, workspace_id: str, session_id: str) -> tuple[DurableTurn, ...]:
        return self._conversation_journal.list_session_turns(workspace_id, session_id)

    def create_agent_run(self, workspace_id: str, run: DurableAgentRun) -> DurableAgentRun:
        return self._permission_journal.create_agent_run(workspace_id, run)

    def create_agent_run_with_permission_snapshot(
        self,
        workspace_id: str,
        run: DurableAgentRun,
        permission_snapshot: PermissionSnapshot,
    ) -> DurableAgentRun:
        return self._permission_journal.create_agent_run_with_permission_snapshot(
            workspace_id, run, permission_snapshot
        )

    def freeze_agent_run_permission_snapshot(
        self,
        workspace_id: str,
        agent_run_id: str,
        permission_snapshot: PermissionSnapshot,
    ) -> DurableAgentRun:
        return self._permission_journal.freeze_agent_run_permission_snapshot(
            workspace_id, agent_run_id, permission_snapshot
        )

    def get_agent_run(self, workspace_id: str, agent_run_id: str) -> DurableAgentRun | None:
        return self._permission_journal.get_agent_run(workspace_id, agent_run_id)

    def list_session_agent_runs(
        self, workspace_id: str, session_id: str
    ) -> tuple[DurableAgentRun, ...]:
        return self._permission_journal.list_session_agent_runs(workspace_id, session_id)

    def get_permission_snapshot(
        self, workspace_id: str, permission_snapshot_id: str
    ) -> PermissionSnapshot | None:
        return self._permission_journal.get_permission_snapshot(
            workspace_id, permission_snapshot_id
        )

    def get_permission_snapshot_for_run(
        self, workspace_id: str, agent_run_id: str
    ) -> PermissionSnapshot | None:
        return self._permission_journal.get_permission_snapshot_for_run(workspace_id, agent_run_id)

    def list_permission_snapshots(
        self, workspace_id: str, *, agent_run_id: str | None = None
    ) -> tuple[PermissionSnapshot, ...]:
        return self._permission_journal.list_permission_snapshots(
            workspace_id, agent_run_id=agent_run_id
        )

    def put_permission_snapshot(
        self, workspace_id: str, permission_snapshot: PermissionSnapshot
    ) -> PermissionSnapshot:
        return self._permission_journal.put_permission_snapshot(workspace_id, permission_snapshot)

    def link_agent_run_permission_snapshot(
        self, workspace_id: str, agent_run_id: str, permission_snapshot_id: str
    ) -> DurableAgentRun:
        return self._permission_journal.link_agent_run_permission_snapshot(
            workspace_id, agent_run_id, permission_snapshot_id
        )

    def append_records(
        self, workspace_id: str, records: Sequence[DurableConversationRecord]
    ) -> DurableSession:
        return self._conversation_journal.append_records(workspace_id, records)

    def load_records(
        self, workspace_id: str, session_id: str
    ) -> tuple[DurableConversationRecord, ...]:
        return self._conversation_journal.load_records(workspace_id, session_id)

    def load_effective_records(
        self, workspace_id: str, session_id: str
    ) -> tuple[DurableConversationRecord, ...]:
        return self._conversation_journal.load_effective_records(workspace_id, session_id)

    def put_context_checkpoint(
        self, workspace_id: str, checkpoint: ContextCheckpoint
    ) -> ContextCheckpoint:
        return self._context_journal.put(workspace_id, checkpoint)

    def get_context_checkpoint(
        self, workspace_id: str, checkpoint_id: str
    ) -> ContextCheckpoint | None:
        return self._context_journal.get(workspace_id, checkpoint_id)

    def list_context_checkpoints(
        self, workspace_id: str, session_id: str, *, task_run_id: str | None = None
    ) -> tuple[ContextCheckpoint, ...]:
        return self._context_journal.list(workspace_id, session_id, task_run_id=task_run_id)

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

    def get_receipt(
        self, workspace_id: str, session_id: str, client_message_id: str
    ) -> TurnSubmitReceipt | None:
        return self._conversation_journal.get_receipt(workspace_id, session_id, client_message_id)

    def put_receipt(self, workspace_id: str, receipt: TurnSubmitReceipt) -> TurnSubmitReceipt:
        return self._conversation_journal.put_receipt(workspace_id, receipt)

    def update_receipt(self, workspace_id: str, receipt: TurnSubmitReceipt) -> TurnSubmitReceipt:
        return self._conversation_journal.update_receipt(workspace_id, receipt)

    def put_execution(
        self, workspace_id: str, execution: DurableToolExecution
    ) -> DurableToolExecution:
        return self._tool_journal.put_execution(workspace_id, execution)

    def get_execution(
        self, workspace_id: str, tool_execution_id: str
    ) -> DurableToolExecution | None:
        return self._tool_journal.get_execution(workspace_id, tool_execution_id)

    def list_executions(
        self, workspace_id: str, *, agent_run_id: str
    ) -> tuple[DurableToolExecution, ...]:
        return self._tool_journal.list_executions(workspace_id, agent_run_id=agent_run_id)

    def list_executions_for_grant(
        self, workspace_id: str, grant_id: str
    ) -> tuple[DurableToolExecution, ...]:
        return self._tool_journal.list_executions_for_grant(workspace_id, grant_id)

    def list_session_executions(
        self, workspace_id: str, session_id: str
    ) -> tuple[DurableToolExecution, ...]:
        return self._tool_journal.list_session_executions(workspace_id, session_id)

    def list_task_executions(
        self, workspace_id: str, task_run_id: str
    ) -> tuple[DurableToolExecution, ...]:
        return self._tool_journal.list_task_executions(workspace_id, task_run_id)

    def save_execution(
        self,
        workspace_id: str,
        execution: DurableToolExecution,
        *,
        expected_row_version: int,
    ) -> DurableToolExecution:
        return self._tool_journal.save_execution(
            workspace_id, execution, expected_row_version=expected_row_version
        )

    def request_execution_cancellation_in_txn(
        self,
        workspace_id: str,
        tool_execution_id: str,
        *,
        now: datetime,
        reason: str,
    ) -> DurableToolExecution | None:
        return self._tool_journal.request_cancellation_in_txn(
            workspace_id, tool_execution_id, now=now, reason=reason
        )

    def _save_execution_in_txn(
        self,
        workspace_id: str,
        execution: DurableToolExecution,
        *,
        expected_row_version: int,
    ) -> DurableToolExecution:
        return self._tool_journal.save_execution_in_txn(
            workspace_id, execution, expected_row_version=expected_row_version
        )

    def put_approval(self, workspace_id: str, approval: DurableApproval) -> DurableApproval:
        return self._tool_journal.put_approval(workspace_id, approval)

    def get_approval(self, workspace_id: str, approval_id: str) -> DurableApproval | None:
        return self._tool_journal.get_approval(workspace_id, approval_id)

    def get_approval_for_execution(
        self, workspace_id: str, tool_execution_id: str
    ) -> DurableApproval | None:
        return self._tool_journal.get_approval_for_execution(workspace_id, tool_execution_id)

    def save_approval(
        self,
        workspace_id: str,
        approval: DurableApproval,
        *,
        expected_row_version: int,
    ) -> DurableApproval:
        return self._tool_journal.save_approval(
            workspace_id, approval, expected_row_version=expected_row_version
        )

    def list_approvals_for_grant(
        self, workspace_id: str, grant_id: str
    ) -> tuple[DurableApproval, ...]:
        return self._tool_journal.list_approvals_for_grant(workspace_id, grant_id)

    def revoke_approval_in_txn(
        self,
        workspace_id: str,
        approval_id: str,
        *,
        now: datetime,
        reason: str,
    ) -> DurableApproval | None:
        return self._tool_journal.revoke_approval_in_txn(
            workspace_id, approval_id, now=now, reason=reason
        )

    def _save_approval_in_txn(
        self,
        workspace_id: str,
        approval: DurableApproval,
        *,
        expected_row_version: int,
    ) -> DurableApproval:
        return self._tool_journal.save_approval_in_txn(
            workspace_id, approval, expected_row_version=expected_row_version
        )

    def put_capability_grant(self, workspace_id: str, grant: CapabilityGrant) -> CapabilityGrant:
        return self._permission_journal.put_capability_grant(workspace_id, grant)

    def get_capability_grant(self, workspace_id: str, grant_id: str) -> CapabilityGrant | None:
        return self._permission_journal.get_capability_grant(workspace_id, grant_id)

    def list_capability_grants(
        self, workspace_id: str, *, agent_run_id: str | None = None
    ) -> tuple[CapabilityGrant, ...]:
        return self._permission_journal.list_capability_grants(
            workspace_id, agent_run_id=agent_run_id
        )

    def save_capability_grant(
        self,
        workspace_id: str,
        grant: CapabilityGrant,
        *,
        expected_row_version: int,
    ) -> CapabilityGrant:
        return self._permission_journal.save_capability_grant(
            workspace_id, grant, expected_row_version=expected_row_version
        )

    def put_report(self, workspace_id: str, report: RecoveryReport) -> RecoveryReport:
        return self._recovery_journal.put_report(workspace_id, report)

    def get_report(self, workspace_id: str, report_id: str) -> RecoveryReport | None:
        return self._recovery_journal.get_report(workspace_id, report_id)

    def get_open_report(self, workspace_id: str, session_id: str) -> RecoveryReport | None:
        return self._recovery_journal.get_open_report(workspace_id, session_id)

    def list_recovery_reports(
        self, workspace_id: str, session_id: str
    ) -> tuple[RecoveryReport, ...]:
        return self._recovery_journal.list_recovery_reports(workspace_id, session_id)

    def save_report(self, workspace_id: str, report: RecoveryReport) -> RecoveryReport:
        return self._recovery_journal.save_report(workspace_id, report)

    def get_recovery_receipt(
        self, workspace_id: str, session_id: str, command_id: str
    ) -> RecoveryReceipt | None:
        return self._recovery_journal.get_recovery_receipt(workspace_id, session_id, command_id)

    def put_recovery_receipt(self, workspace_id: str, receipt: RecoveryReceipt) -> RecoveryReceipt:
        return self._recovery_journal.put_recovery_receipt(workspace_id, receipt)

    def _insert_execution(self, workspace_id: str, execution: DurableToolExecution) -> None:
        self._tool_journal.insert_execution(workspace_id, execution)

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
        self._task_journal.insert(task)

    def _executor_or_raise(self) -> SqliteExecutor:
        return self._backend.executor()

    def _session_mutation_time(
        self,
        session: DurableSession,
        *,
        requested: datetime | None = None,
    ) -> datetime:
        """Return one strictly monotonic Session token per outer transaction.

        Session timestamps are stored as whole Unix seconds. Advancing by at least one second keeps
        optimistic concurrency reliable even when multiple commands use the same injected clock
        value or occur within one wall-clock second. Nested journal calls in one transaction share
        the same token so a turn commit remains one atomic Session mutation.
        """

        return self._backend.session_mutation_time(
            session,
            requested=requested,
            load_current=lambda: self.get_session(session.workspace_id, session.session_id),
        )

    def _read_one(self, sql: str, parameters: tuple[object, ...]) -> tuple[object, ...] | None:
        return self._backend.read_one(sql, parameters)

    def _read_all(
        self, sql: str, parameters: tuple[object, ...] = ()
    ) -> tuple[tuple[object, ...], ...]:
        return self._backend.read_all(sql, parameters)


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
