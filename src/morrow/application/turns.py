"""Turn-submit coordination. ConversationLog remains the only chat writer."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from morrow.adapters.state.journal import SqliteOperationalJournal
from morrow.adapters.state.operational import OperationalStoreSession
from morrow.application.recovery import RecoveryService
from morrow.application.tasks import TaskOutcomeAssembler, TaskService
from morrow.application.tool_persistence import (
    DurableToolExecutionCoordinator,
    ToolConversationPersistence,
)
from morrow.application.turn_permissions import RunPermissionCoordinator
from morrow.core.application import ApplicationError, ApplicationErrorCode
from morrow.core.domain import (
    AGENT_RUN_ID_PREFIX,
    COMMAND_ID_PREFIX,
    TASK_RUN_ID_PREFIX,
    AgentRunSnapshot,
    DurableAgentRun,
    DurableSession,
    DurableTaskRun,
    DurableTurn,
    SessionHealth,
    SessionLifecycle,
    SourceRevisionRef,
    TaskOutcomeTrigger,
    TaskRunStatus,
    TurnSubmitDisposition,
    TurnSubmitReceipt,
    canonical_json_bytes,
    session_can_start_work,
    sha256_digest,
)
from morrow.core.execution import (
    DurableApproval,
    DurableToolExecution,
    ToolExecutionDisposition,
)
from morrow.core.faults import FaultInjector, FaultPoint, NoOpFaultInjector
from morrow.core.models import (
    AssistantMessage,
    FinishReason,
    ModelRef,
    Preferences,
    UserMessage,
    utc_now,
)
from morrow.core.permissions import PermissionSnapshot
from morrow.core.ports import Clock, IdSource
from morrow.core.recovery import RecoveryReport
from morrow.core.store import StorageError
from morrow.runtime.conversation import (
    ConversationAppend,
    ConversationLog,
    ConversationLogError,
    TurnTerminalRecord,
)
from morrow.runtime.durable_log import (
    DurableConversationWriter,
    restore_conversation_log,
)
from morrow.runtime.session import Session


@dataclass(frozen=True)
class TurnSubmitResult:
    kind: Literal["accepted", "closed_replay", "recovery", "conflict"]
    turn_id: str | None
    receipt: TurnSubmitReceipt | None = None
    assistant_text: str | None = None


def request_digest(user_input: str) -> str:
    return sha256_digest(canonical_json_bytes({"content": user_input}))


def _turn_health_error(health: SessionHealth) -> ApplicationError:
    codes = {
        SessionHealth.QUARANTINED: ApplicationErrorCode.QUARANTINED,
        SessionHealth.READ_ONLY: ApplicationErrorCode.READ_ONLY,
    }
    return ApplicationError(
        codes[health],
        f"Session health {health.value} cannot start a Turn",
    )


def build_agent_run_snapshot(
    session: Session,
    *,
    model: ModelRef,
    run_policy,
    tools,
    runtime_instance_id: str,
) -> AgentRunSnapshot:
    revisions: list[SourceRevisionRef] = []
    if session.profile is not None:
        revisions.append(
            SourceRevisionRef(
                kind="workspace_profile",
                revision=session.profile_revision,
                content_sha256=sha256_digest(
                    canonical_json_bytes(session.profile.model_dump(mode="json"))
                ),
            )
        )
    if session.workspace_preferences != Preferences():
        revisions.append(
            SourceRevisionRef(
                kind="workspace_preferences",
                revision=session.preferences_revision,
                content_sha256=sha256_digest(
                    canonical_json_bytes(session.workspace_preferences.model_dump(mode="json"))
                ),
            )
        )
    tool_payload = [tool.model_dump(mode="json") for tool in tools]
    return AgentRunSnapshot(
        profile=session.profile,
        preferences=session.preferences,
        model=model,
        provider_id=model.provider_id,
        source_revisions=tuple(revisions),
        run_policy_digest=sha256_digest(canonical_json_bytes(run_policy.model_dump(mode="json"))),
        tool_schema_digest=sha256_digest(canonical_json_bytes(tool_payload)),
        permission_profile_digest=sha256_digest(
            canonical_json_bytes(session.permission_profile.model_dump(mode="json"))
        ),
        runtime_instance_id=runtime_instance_id,
    )


class SessionPersistence:
    """Journal-backed Session committer and turn-submit coordinator."""

    def __init__(
        self,
        *,
        workspace_id: str,
        journal: SqliteOperationalJournal,
        store_session: OperationalStoreSession,
        id_source: IdSource,
        model: ModelRef,
        run_policy,
        runtime_instance_id: str,
        mutation=None,
        artifacts=None,
        recovery: RecoveryService | None = None,
        faults: FaultInjector | None = None,
        clock: Clock | None = None,
    ) -> None:
        self.workspace_id = workspace_id
        self.journal = journal
        self.store_session = store_session
        self.id_source = id_source
        self.model = model
        self.run_policy = run_policy
        self.runtime_instance_id = runtime_instance_id
        self.mutation = mutation
        self.artifacts = artifacts
        workspace_root = mutation.files.resolver.root if mutation is not None else None
        self.recovery = recovery or RecoveryService(
            journal,
            workspace_id=workspace_id,
            id_source=id_source,
            workspace_root=workspace_root,
        )
        self.faults = faults or NoOpFaultInjector()
        self.clock = clock or store_session
        self.permissions = RunPermissionCoordinator(
            journal,
            workspace_id=workspace_id,
            id_source=id_source,
            clock=self._now,
        )
        self.tool_executions = DurableToolExecutionCoordinator(
            journal,
            workspace_id=workspace_id,
            id_source=id_source,
            permissions=self.permissions,
            faults=self.faults,
            clock=self._now,
            artifacts=artifacts,
        )
        self.tool_conversation = ToolConversationPersistence(
            journal,
            workspace_id=workspace_id,
            id_source=id_source,
            mutation=mutation,
            faults=self.faults,
            clock=self._now,
        )
        self.tasks = TaskService(
            journal=journal,
            workspace_id=workspace_id,
            id_source=id_source,
            clock=self._now,
        )
        self.outcomes = TaskOutcomeAssembler(
            journal,
            workspace_id=workspace_id,
            id_source=id_source,
            clock=self._now,
        )
        self.writer: DurableConversationWriter | None = None
        self._session: Session | None = None
        self._last_client_message_id: str | None = None
        self.current_turn_id: str | None = None
        self.current_task_run_id: str | None = None
        self.current_agent_run_id: str | None = None
        self.current_permission_snapshot_id: str | None = None
        self.open_report: RecoveryReport | None = None
        self.pending_resume = False

    def attach(self, session: Session) -> None:
        self._session = session
        self.writer = DurableConversationWriter(
            session.log,
            self.journal,
            workspace_id=self.workspace_id,
            session_id=session.session_id,
            id_source=self.id_source,
        )
        session.committer = self
        session.durable_runtime = self

    def _now(self) -> datetime:
        return self.clock.now() if self.clock is not None else utc_now()

    def now(self) -> datetime:
        """Return the durable lifecycle clock exposed to AgentLoop."""

        return self._now()

    def check_fault(self, point: FaultPoint) -> None:
        """Keep fault-injection ownership behind the durable runtime contract."""

        self.faults.check(point)

    def has_active_unconfined_grant(
        self, execution: DurableToolExecution, *, now: datetime
    ) -> bool:
        return self.permissions.has_active_unconfined_grant(execution, now=now)

    def freeze_permission_snapshot(
        self,
        session: Session,
        *,
        tools: tuple = (),
        now: datetime | None = None,
    ) -> PermissionSnapshot:
        """Freeze base permissions and any already-explicit run-bound grant once."""

        del tools
        snapshot = self.permissions.freeze(
            session,
            agent_run_id=self.current_agent_run_id,
            task_run_id=self.current_task_run_id,
            turn_id=self.current_turn_id,
            now=now,
        )
        self.current_permission_snapshot_id = snapshot.permission_snapshot_id
        return snapshot

    def commit(self, planned: ConversationAppend) -> None:
        if self.writer is None:
            raise RuntimeError("session persistence is not attached")
        terminal = planned.added[-1] if planned.added else None
        if not isinstance(terminal, TurnTerminalRecord):
            self.writer.commit(planned)
            return

        def work(txn: SqliteOperationalJournal) -> None:
            self.writer.persist_with_records(planned)
            self._apply_task_terminal_in_txn(txn, terminal)
            self._close_open_receipt_in_txn(txn)

        self.journal.transact(work)
        self._session_log_apply(planned)

    def _session_log_apply(self, planned: ConversationAppend) -> None:
        if self._session is None:
            raise RuntimeError("session persistence is not attached")
        self._session.log.apply_committed(planned)
        self._session.dirty = self._session.log.has_active_turn

    def _apply_task_terminal_in_txn(
        self, txn: SqliteOperationalJournal, terminal: TurnTerminalRecord
    ) -> None:
        if self._session is None or self.current_task_run_id is None:
            return
        task = txn.get_task_run(self.workspace_id, self.current_task_run_id)
        if task is None:
            raise RuntimeError("durable TaskRun is missing for the active turn")
        if terminal.finish_reason is FinishReason.STOP:
            target = TaskRunStatus.READY_FOR_ACCEPTANCE
            trigger = None
            reason = "assistant_answer_presented"
        elif terminal.finish_reason is FinishReason.CANCELLED:
            target = TaskRunStatus.CANCELLED
            trigger = TaskOutcomeTrigger.TERMINAL_CLOSE
            reason = "turn_cancelled"
        else:
            target = TaskRunStatus.FAILED
            trigger = TaskOutcomeTrigger.TERMINAL_CLOSE
            reason = "turn_failed"
        updated = self.tasks._transition_in_txn(
            txn,
            task,
            target,
            reason=reason,
            turn_id=self.current_turn_id,
        )
        if trigger is not None:
            self.outcomes = TaskOutcomeAssembler(
                txn,
                workspace_id=self.workspace_id,
                id_source=self.id_source,
                clock=self._now,
            )
            outcome = self.outcomes.build(updated, trigger=trigger)
            txn.put_task_outcome(self.workspace_id, outcome)
        if target.is_terminal:
            self.current_task_run_id = None

    def _close_open_receipt_in_txn(self, txn: SqliteOperationalJournal) -> None:
        if self._session is None or self._last_client_message_id is None:
            return
        receipt = txn.get_receipt(
            self.workspace_id, self._session.session_id, self._last_client_message_id
        )
        if receipt is None or receipt.disposition is TurnSubmitDisposition.ACCEPTED_CLOSED:
            return
        txn.update_receipt(
            self.workspace_id,
            receipt.model_copy(update={"disposition": TurnSubmitDisposition.ACCEPTED_CLOSED}),
        )

    def close_open_receipt(self) -> None:
        if self._session is None or self._last_client_message_id is None:
            return
        receipt = self.journal.get_receipt(
            self.workspace_id, self._session.session_id, self._last_client_message_id
        )
        if receipt is None or receipt.disposition is TurnSubmitDisposition.ACCEPTED_CLOSED:
            return
        self.journal.update_receipt(
            self.workspace_id,
            receipt.model_copy(update={"disposition": TurnSubmitDisposition.ACCEPTED_CLOSED}),
        )

    def submit_user(
        self,
        session: Session,
        user_input: str,
        client_message_id: str,
        *,
        turn_id: str,
        agent_run_id: str,
        tools: tuple = (),
    ) -> TurnSubmitResult:
        digest = request_digest(user_input)
        existing = self.journal.get_receipt(
            self.workspace_id, session.session_id, client_message_id
        )
        if existing is not None:
            if existing.request_digest != digest:
                return TurnSubmitResult("conflict", existing.turn_id, existing)
            if existing.disposition is TurnSubmitDisposition.ACCEPTED_CLOSED:
                return TurnSubmitResult(
                    "closed_replay",
                    existing.turn_id,
                    existing,
                    assistant_text=_last_assistant_text(session.log),
                )
            session.health = SessionHealth.NEEDS_RECOVERY
            row = self.journal.get_session(self.workspace_id, session.session_id)
            if row is not None and row.health is not SessionHealth.NEEDS_RECOVERY:
                self.journal.save_session(
                    self.workspace_id,
                    row.model_copy(update={"health": SessionHealth.NEEDS_RECOVERY}),
                )
            return TurnSubmitResult("recovery", existing.turn_id, existing)
        if session.health is SessionHealth.NEEDS_RECOVERY:
            return TurnSubmitResult("recovery", self.current_turn_id, None)
        if session.lifecycle is not SessionLifecycle.ACTIVE:
            raise ApplicationError(
                ApplicationErrorCode.INVALID,
                "only an active Session can start a Turn",
            )
        if session.health is not SessionHealth.OK:
            raise _turn_health_error(session.health)

        planned = session.log.plan_begin_turn(UserMessage(content=user_input))
        snapshot = build_agent_run_snapshot(
            session,
            model=self.model,
            run_policy=self.run_policy,
            tools=tools,
            runtime_instance_id=self.runtime_instance_id,
        )
        command_id = self.id_source.new_id(COMMAND_ID_PREFIX)
        writer = self.writer
        if writer is None:
            raise RuntimeError("session persistence is not attached")

        def work(txn: SqliteOperationalJournal) -> str | TurnSubmitResult:
            row = txn.get_session(self.workspace_id, session.session_id)
            if row is None:
                stamp = self._now()
                row = txn.create_session(
                    DurableSession(
                        session_id=session.session_id,
                        workspace_id=self.workspace_id,
                        created_at=stamp,
                        updated_at=stamp,
                    )
                )
            if not session_can_start_work(row.lifecycle, row.health):
                if row.lifecycle is not SessionLifecycle.ACTIVE:
                    raise ApplicationError(
                        ApplicationErrorCode.INVALID,
                        "only an active Session can start a Turn",
                    )
                if row.health is SessionHealth.NEEDS_RECOVERY:
                    return TurnSubmitResult("recovery", self.current_turn_id, None)
                raise _turn_health_error(row.health)
            task_id = row.current_task_run_id
            follow_up_task = None
            if task_id is not None:
                current_task = txn.get_task_run(self.workspace_id, task_id)
                if current_task is None:
                    raise RuntimeError("current durable TaskRun is missing")
                if current_task.status is TaskRunStatus.READY_FOR_ACCEPTANCE:
                    follow_up_task = current_task
                elif current_task.status is TaskRunStatus.FAILED:
                    raise RuntimeError("failed TaskRun requires explicit resume")
                elif current_task.status.is_terminal:
                    task_id = None
            if task_id is None:
                stamp = self._now()
                task = txn.create_task_run(
                    self.workspace_id,
                    DurableTaskRun(
                        task_run_id=self.id_source.new_id(TASK_RUN_ID_PREFIX),
                        session_id=session.session_id,
                        workspace_id=self.workspace_id,
                        created_at=stamp,
                        updated_at=stamp,
                    ),
                    make_current=True,
                )
                task_id = task.task_run_id
            stamp = self._now()
            txn.create_turn(
                self.workspace_id,
                DurableTurn(
                    turn_id=turn_id,
                    session_id=session.session_id,
                    task_run_id=task_id,
                    client_message_id=client_message_id,
                    created_at=stamp,
                ),
            )
            if follow_up_task is not None:
                self.tasks._transition_in_txn(
                    txn,
                    follow_up_task,
                    TaskRunStatus.OPEN,
                    reason="ordinary_follow_up",
                    turn_id=turn_id,
                )
            stored_agent_run_id = agent_run_id or self.id_source.new_id(AGENT_RUN_ID_PREFIX)
            txn.create_agent_run(
                self.workspace_id,
                DurableAgentRun(
                    agent_run_id=stored_agent_run_id,
                    turn_id=turn_id,
                    session_id=session.session_id,
                    snapshot=snapshot,
                    created_at=stamp,
                ),
            )
            txn.put_receipt(
                self.workspace_id,
                TurnSubmitReceipt(
                    session_id=session.session_id,
                    client_message_id=client_message_id,
                    request_digest=digest,
                    disposition=TurnSubmitDisposition.ACCEPTED_OPEN,
                    turn_id=turn_id,
                    command_id=command_id,
                ),
            )
            writer.persist(planned)
            self.current_turn_id = turn_id
            self.current_task_run_id = task_id
            self.current_agent_run_id = stored_agent_run_id
            self.current_permission_snapshot_id = None
            return turn_id

        accepted_turn = self.journal.transact(work)
        if isinstance(accepted_turn, TurnSubmitResult):
            session.health = SessionHealth.NEEDS_RECOVERY
            return accepted_turn
        session.log.apply_committed(planned)
        self._last_client_message_id = client_message_id
        session.dirty = True
        receipt = self.journal.get_receipt(self.workspace_id, session.session_id, client_message_id)
        return TurnSubmitResult("accepted", accepted_turn, receipt)

    def start_new_session(self, session: Session, session_id: str) -> None:
        stamp = self._now()
        self.journal.create_session(
            DurableSession(
                session_id=session_id,
                workspace_id=self.workspace_id,
                created_at=stamp,
                updated_at=stamp,
            )
        )
        session.reset(session_id)
        session.log = ConversationLog()
        self.current_turn_id = None
        self.current_task_run_id = None
        self.current_agent_run_id = None
        self.current_permission_snapshot_id = None
        self.open_report = None
        self.pending_resume = False
        self.context_checkpoint = None
        self.attach(session)

    def restore_into(self, session: Session) -> None:
        row = self.journal.get_session(self.workspace_id, session.session_id)
        if row is None:
            stamp = self._now()
            self.journal.create_session(
                DurableSession(
                    session_id=session.session_id,
                    workspace_id=self.workspace_id,
                    created_at=stamp,
                    updated_at=stamp,
                )
            )
            self.attach(session)
            return
        session.lifecycle = row.lifecycle
        session.health = row.health
        try:
            checkpoints = self.journal.list_context_checkpoints(
                self.workspace_id, session.session_id
            )
            session.context_checkpoint = checkpoints[-1] if checkpoints else None
            if session.context_checkpoint is None and row.parent_checkpoint_id is not None:
                session.context_checkpoint = self.journal.get_context_checkpoint(
                    self.workspace_id, row.parent_checkpoint_id
                )
        except StorageError:
            session.context_checkpoint = None
        self.current_task_run_id = row.current_task_run_id
        self.current_permission_snapshot_id = None
        self._last_client_message_id = None
        self.pending_resume = False
        try:
            session.log = restore_conversation_log(
                self.journal, self.workspace_id, session.session_id
            )
        except ConversationLogError:
            session.log = ConversationLog()
            session.health = SessionHealth.QUARANTINED
        else:
            report = self.recovery.discover(session.session_id, session.log)
            self.open_report = report
            if report is not None:
                self.current_turn_id = report.turn_id
                self.current_agent_run_id = report.agent_run_id
                if report.turn_id is not None:
                    turn = self.journal.get_turn(self.workspace_id, report.turn_id)
                    if turn is not None:
                        self._last_client_message_id = turn.client_message_id
                interrupted = (
                    self.journal.get_agent_run(self.workspace_id, report.agent_run_id)
                    if report.agent_run_id is not None
                    else None
                )
                self.current_permission_snapshot_id = (
                    interrupted.permission_snapshot_id if interrupted is not None else None
                )
            if report is not None and session.health is not SessionHealth.QUARANTINED:
                session.health = SessionHealth.NEEDS_RECOVERY
            elif (
                session.lifecycle is SessionLifecycle.ACTIVE
                and session.log.has_active_turn
                and session.health is SessionHealth.OK
            ):
                turns = self.journal.list_session_turns(self.workspace_id, session.session_id)
                runs = self.journal.list_session_agent_runs(self.workspace_id, session.session_id)
                self.current_turn_id = turns[-1].turn_id if turns else None
                self.current_agent_run_id = runs[-1].agent_run_id if runs else None
                if turns:
                    self._last_client_message_id = turns[-1].client_message_id
                self.pending_resume = True
        if session.health is SessionHealth.NEEDS_RECOVERY:
            self.journal.save_session(
                self.workspace_id,
                row.model_copy(update={"health": SessionHealth.NEEDS_RECOVERY}),
            )
        elif session.health is SessionHealth.QUARANTINED:
            self.journal.save_session(
                self.workspace_id,
                row.model_copy(update={"health": SessionHealth.QUARANTINED}),
            )
        self.attach(session)

    def prepare_and_commit_assistant(
        self,
        planned: ConversationAppend,
        message: AssistantMessage,
        *,
        run_context,
        tool_executor,
    ) -> tuple[DurableToolExecution, ...]:
        if self.writer is None or self._session is None:
            raise RuntimeError("session persistence is not attached")
        if (
            self.current_turn_id is None
            or self.current_task_run_id is None
            or self.current_agent_run_id is None
        ):
            raise RuntimeError("durable tool intents require an open AgentRun")
        snapshot = self.freeze_permission_snapshot(
            self._session,
            tools=tool_executor.definitions if tool_executor is not None else (),
        )
        grant_id, isolation_label = self.permissions.active_grant_evidence(
            snapshot, now=self._now()
        )
        return self.tool_conversation.prepare_and_commit_assistant(
            planned,
            message,
            session=self._session,
            writer=self.writer,
            run_context=run_context,
            tool_executor=tool_executor,
            task_run_id=self.current_task_run_id,
            turn_id=self.current_turn_id,
            agent_run_id=self.current_agent_run_id,
            permission_snapshot_id=snapshot.permission_snapshot_id,
            grant_id=grant_id,
            isolation_label=isolation_label,
        )

    def execution_is_visible(self, tool_execution_id: str) -> bool:
        return self.tool_executions.execution_is_visible(tool_execution_id)

    def create_pending_approval(
        self, execution: DurableToolExecution, *, now: datetime | None = None
    ) -> DurableApproval:
        return self.tool_executions.create_pending_approval(execution, now=now)

    def consume_and_mark_executing(
        self,
        execution: DurableToolExecution,
        approval: DurableApproval,
        *,
        approved: bool,
        now: datetime | None = None,
        command_id: str | None = None,
    ) -> tuple[DurableToolExecution, DurableApproval, bool]:
        return self.tool_executions.consume_and_mark_executing(
            execution,
            approval,
            approved=approved,
            now=now,
            command_id=command_id,
        )

    def mark_executing(
        self, execution: DurableToolExecution, *, now: datetime | None = None
    ) -> DurableToolExecution:
        return self.tool_executions.mark_executing(execution, now=now)

    def get_execution(self, tool_execution_id: str) -> DurableToolExecution | None:
        return self.tool_executions.get_execution(tool_execution_id)

    def deny_execution_before_handler(
        self, execution: DurableToolExecution, *, now: datetime | None = None
    ) -> DurableToolExecution:
        return self.tool_executions.deny_execution_before_handler(execution, now=now)

    def cancel_execution_before_handler(
        self, execution: DurableToolExecution, *, now: datetime | None = None
    ) -> DurableToolExecution:
        return self.tool_executions.cancel_execution_before_handler(execution, now=now)

    def assert_handler_may_enter(
        self, execution: DurableToolExecution, *, now: datetime | None = None
    ) -> DurableToolExecution:
        """Re-read immutable evidence immediately before a side-effecting handler."""

        return self.permissions.assert_handler_may_enter(execution, now=now or self._now())

    def record_handler_completed(
        self,
        execution: DurableToolExecution,
        result,
        *,
        now: datetime | None = None,
        disposition: ToolExecutionDisposition | None = None,
    ) -> DurableToolExecution:
        return self.tool_executions.record_handler_completed(
            execution,
            result,
            now=now,
            disposition=disposition,
        )

    def commit_tool_message(
        self,
        planned: ConversationAppend,
        execution: DurableToolExecution,
        *,
        now: datetime | None = None,
        disposition: ToolExecutionDisposition | None = None,
    ) -> DurableToolExecution:
        if self.writer is None or self._session is None:
            raise RuntimeError("session persistence is not attached")
        return self.tool_conversation.commit_tool_message(
            planned,
            execution,
            session=self._session,
            writer=self.writer,
            now=now,
            disposition=disposition,
        )

    def close(self) -> None:
        self.store_session.close()


def _last_assistant_text(log: ConversationLog) -> str | None:
    for message in reversed(log.messages_view()):
        if message.role == "assistant" and message.content:
            return message.content
    return None
