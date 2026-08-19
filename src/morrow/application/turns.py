"""Turn-submit coordination. ConversationLog remains the only chat writer."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from morrow.adapters.state.journal import SqliteOperationalJournal
from morrow.adapters.state.operational import OperationalStoreSession
from morrow.application.prepared import prepare_cycle_executions
from morrow.application.recovery import RecoveryService
from morrow.application.tasks import TaskOutcomeAssembler, TaskService
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
    SourceRevisionRef,
    TaskOutcomeTrigger,
    TaskRunStatus,
    TurnSubmitDisposition,
    TurnSubmitReceipt,
    canonical_json_bytes,
    sha256_digest,
)
from morrow.core.execution import (
    APPROVAL_ID_PREFIX,
    ApprovalResolution,
    DurableApproval,
    DurableToolExecution,
    HandlerResultEnvelope,
    ToolExecutionDisposition,
    ToolExecutionState,
    approval_preview_digest,
    consume_approval,
    intent_hash,
    resolve_approval,
    transition_execution,
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
from morrow.core.ports import IdSource
from morrow.core.recovery import RecoveryReport, RecoveryReportStatus, RecoveryResolution
from morrow.runtime.conversation import (
    ConversationAppend,
    ConversationLog,
    ConversationLogError,
    TurnTerminalRecord,
)
from morrow.runtime.durable_log import DurableConversationWriter, restore_conversation_log
from morrow.runtime.session import Session


@dataclass(frozen=True)
class TurnSubmitResult:
    kind: Literal["accepted", "closed_replay", "recovery", "conflict"]
    turn_id: str | None
    receipt: TurnSubmitReceipt | None = None
    assistant_text: str | None = None


def request_digest(user_input: str) -> str:
    return sha256_digest(canonical_json_bytes({"content": user_input}))


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
        faults: FaultInjector | None = None,
    ) -> None:
        self.workspace_id = workspace_id
        self.journal = journal
        self.store_session = store_session
        self.id_source = id_source
        self.model = model
        self.run_policy = run_policy
        self.runtime_instance_id = runtime_instance_id
        self.mutation = mutation
        self.faults = faults or NoOpFaultInjector()
        self.tasks = TaskService(
            journal=journal,
            workspace_id=workspace_id,
            id_source=id_source,
        )
        self.outcomes = TaskOutcomeAssembler(
            journal,
            workspace_id=workspace_id,
            id_source=id_source,
        )
        self.writer: DurableConversationWriter | None = None
        self._session: Session | None = None
        self._last_client_message_id: str | None = None
        self.current_turn_id: str | None = None
        self.current_task_run_id: str | None = None
        self.current_agent_run_id: str | None = None
        self.open_report: RecoveryReport | None = None

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
            return TurnSubmitResult("recovery", existing.turn_id, existing)
        if session.health is SessionHealth.NEEDS_RECOVERY:
            return TurnSubmitResult("recovery", self.current_turn_id, None)

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

        def work(txn: SqliteOperationalJournal) -> str:
            row = txn.get_session(self.workspace_id, session.session_id)
            if row is None:
                row = txn.create_session(
                    DurableSession(session_id=session.session_id, workspace_id=self.workspace_id)
                )
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
                task = txn.create_task_run(
                    self.workspace_id,
                    DurableTaskRun(
                        task_run_id=self.id_source.new_id(TASK_RUN_ID_PREFIX),
                        session_id=session.session_id,
                        workspace_id=self.workspace_id,
                    ),
                    make_current=True,
                )
                task_id = task.task_run_id
            txn.create_turn(
                self.workspace_id,
                DurableTurn(
                    turn_id=turn_id,
                    session_id=session.session_id,
                    task_run_id=task_id,
                    client_message_id=client_message_id,
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
            return turn_id

        accepted_turn = self.journal.transact(work)
        session.log.apply_committed(planned)
        self._last_client_message_id = client_message_id
        session.dirty = True
        receipt = self.journal.get_receipt(self.workspace_id, session.session_id, client_message_id)
        return TurnSubmitResult("accepted", accepted_turn, receipt)

    def start_new_session(self, session: Session, session_id: str) -> None:
        self.journal.create_session(
            DurableSession(session_id=session_id, workspace_id=self.workspace_id)
        )
        session.reset(session_id)
        session.log = ConversationLog()
        self.current_turn_id = None
        self.current_task_run_id = None
        self.current_agent_run_id = None
        self.open_report = None
        self.attach(session)

    def restore_into(self, session: Session) -> None:
        row = self.journal.get_session(self.workspace_id, session.session_id)
        if row is None:
            self.journal.create_session(
                DurableSession(session_id=session.session_id, workspace_id=self.workspace_id)
            )
            self.attach(session)
            return
        session.lifecycle = row.lifecycle
        session.health = row.health
        self.current_task_run_id = row.current_task_run_id
        try:
            session.log = restore_conversation_log(
                self.journal, self.workspace_id, session.session_id
            )
        except ConversationLogError:
            session.log = ConversationLog()
            session.health = SessionHealth.QUARANTINED
        else:
            root = None
            if self.mutation is not None:
                root = self.mutation.files.resolver.root
            service = RecoveryService(
                self.journal,
                workspace_id=self.workspace_id,
                id_source=self.id_source,
                workspace_root=root,
            )
            report = service.discover(session.session_id, session.log)
            self.open_report = report
            if report is not None:
                self.current_turn_id = report.turn_id
                self.current_agent_run_id = report.agent_run_id
            if report is not None and session.health is not SessionHealth.QUARANTINED:
                session.health = SessionHealth.NEEDS_RECOVERY
            elif session.log.has_active_turn and session.health is SessionHealth.OK:
                session.health = SessionHealth.NEEDS_RECOVERY
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
        executions = prepare_cycle_executions(
            message,
            session=self._session,
            tool_executor=tool_executor,
            run_context=run_context,
            id_source=self.id_source,
            workspace_id=self.workspace_id,
            task_run_id=self.current_task_run_id,
            turn_id=self.current_turn_id,
            agent_run_id=self.current_agent_run_id,
            mutation=self.mutation,
            isolation=self._session.permission_profile.process_isolation,
        )
        writer = self.writer
        self.faults.check(FaultPoint.CONVERSATION_BEFORE_COMMIT)

        def work(txn: SqliteOperationalJournal) -> tuple[DurableToolExecution, ...]:
            durables, _snapshot = writer.persist_with_records(planned)
            assistant_id = durables[0].record_id if durables else None
            aliases = writer.call_aliases
            stored: list[DurableToolExecution] = []
            for execution in executions:
                alias = aliases.get(execution.call_id, execution.call_id)
                stored.append(
                    txn.put_execution(
                        self.workspace_id,
                        execution.model_copy(
                            update={
                                "assistant_record_id": assistant_id,
                                "call_id": alias,
                                "intent": execution.intent.model_copy(update={"call_id": alias}),
                            }
                        ),
                    )
                )
            self.faults.check(FaultPoint.EXECUTION_INTENT_AFTER_COMMIT)
            return tuple(stored)

        committed = self.journal.transact(work)
        self.faults.check(FaultPoint.CONVERSATION_AFTER_COMMIT)
        self._session.log.apply_committed(planned)
        self._session.dirty = self._session.log.has_active_turn
        return committed

    def apply_recovery(
        self,
        session,
        *,
        command_id: str,
        resolution: RecoveryResolution,
        item_id: str | None = None,
    ) -> RecoveryReport:
        if self.open_report is None:
            raise RuntimeError("no open recovery report")
        root = None
        if self.mutation is not None:
            root = self.mutation.files.resolver.root
        service = RecoveryService(
            self.journal,
            workspace_id=self.workspace_id,
            id_source=self.id_source,
            workspace_root=root,
        )
        updated, receipt, planned = service.decide(
            self.open_report,
            command_id=command_id,
            resolution=resolution,
            item_id=item_id,
            log=session.log,
        )
        if receipt.kind == "conflict":
            raise RuntimeError("recovery command conflicts with a previous request")
        if receipt.kind == "replay":
            return self.open_report
        saved = service.commit_decision(
            updated,
            receipt,
            planned=planned,
            log=session.log,
            writer=self.writer,
            close_all=resolution is RecoveryResolution.ABORT,
        )
        self.open_report = saved if saved.status is not RecoveryReportStatus.RESOLVED else None
        if saved.status is RecoveryReportStatus.QUARANTINED:
            session.health = SessionHealth.QUARANTINED
        elif saved.status is RecoveryReportStatus.RESOLVED:
            session.health = SessionHealth.OK
            if resolution is RecoveryResolution.RESUME and self.current_turn_id is not None:
                self.current_agent_run_id = self.id_source.new_id(AGENT_RUN_ID_PREFIX)
                self.journal.create_agent_run(
                    self.workspace_id,
                    DurableAgentRun(
                        agent_run_id=self.current_agent_run_id,
                        turn_id=self.current_turn_id,
                        session_id=session.session_id,
                        resume_of_agent_run_id=saved.agent_run_id,
                        snapshot=build_agent_run_snapshot(
                            session,
                            model=self.model,
                            run_policy=self.run_policy,
                            tools=(),
                            runtime_instance_id=self.runtime_instance_id,
                        ),
                    ),
                )
        else:
            session.health = SessionHealth.NEEDS_RECOVERY
        row = self.journal.get_session(self.workspace_id, session.session_id)
        if row is not None:
            self.journal.save_session(
                self.workspace_id, row.model_copy(update={"health": session.health})
            )
        return saved

    def execution_is_visible(self, tool_execution_id: str) -> bool:
        return self.journal.get_execution(self.workspace_id, tool_execution_id) is not None

    def create_pending_approval(
        self, execution: DurableToolExecution, *, now: datetime | None = None
    ) -> DurableApproval:
        stamp = now or utc_now()
        preview = execution.intent.preview
        approval = DurableApproval(
            approval_id=self.id_source.new_id(APPROVAL_ID_PREFIX),
            tool_execution_id=execution.tool_execution_id,
            intent_hash=intent_hash(execution.intent),
            tool_schema_digest=execution.intent.schema_digest,
            permission_context_digest=execution.intent.permission_context_digest,
            requested_scope=f"{execution.intent.effect_class.value}:{execution.tool_name}",
            preview=preview,
            preview_digest=approval_preview_digest(preview),
            created_at=stamp,
            expires_at=stamp + APPROVAL_TTL,
        )
        stored = self.journal.put_approval(self.workspace_id, approval)
        self.faults.check(FaultPoint.APPROVAL_AFTER_CREATE)
        return stored

    def consume_and_mark_executing(
        self,
        execution: DurableToolExecution,
        approval: DurableApproval,
        *,
        approved: bool,
        now: datetime | None = None,
        command_id: str | None = None,
    ) -> tuple[DurableToolExecution, DurableApproval, bool]:
        stamp = now or utc_now()
        resolved = resolve_approval(
            approval,
            approved=approved,
            expected_row_version=approval.row_version,
            now=stamp,
            command_id=command_id,
        )
        if resolved.resolution is not ApprovalResolution.APPROVED:
            denied = transition_execution(
                execution,
                ToolExecutionState.CLOSED,
                expected_row_version=execution.row_version,
                disposition=ToolExecutionDisposition.DENIED,
                now=stamp,
            )

            def deny(txn: SqliteOperationalJournal) -> tuple[DurableToolExecution, DurableApproval]:
                saved_approval = txn.save_approval(
                    self.workspace_id, resolved, expected_row_version=approval.row_version
                )
                saved_execution = txn.save_execution(
                    self.workspace_id, denied, expected_row_version=execution.row_version
                )
                return saved_execution, saved_approval

            closed, stored_approval = self.journal.transact(deny)
            return closed, stored_approval, False

        executing = transition_execution(
            execution,
            ToolExecutionState.EXECUTING,
            expected_row_version=execution.row_version,
            now=stamp,
            approval_id=approval.approval_id,
        )

        def work(txn: SqliteOperationalJournal) -> tuple[DurableToolExecution, DurableApproval]:
            saved_resolved = txn.save_approval(
                self.workspace_id, resolved, expected_row_version=approval.row_version
            )
            consumed = consume_approval(
                saved_resolved, expected_row_version=saved_resolved.row_version, now=stamp
            )
            saved_approval = txn.save_approval(
                self.workspace_id, consumed, expected_row_version=saved_resolved.row_version
            )
            saved_execution = txn.save_execution(
                self.workspace_id, executing, expected_row_version=execution.row_version
            )
            self.faults.check(FaultPoint.APPROVAL_AFTER_CONSUME)
            return saved_execution, saved_approval

        saved_execution, saved_approval = self.journal.transact(work)
        return saved_execution, saved_approval, True

    def mark_executing(
        self, execution: DurableToolExecution, *, now: datetime | None = None
    ) -> DurableToolExecution:
        stamp = now or utc_now()
        executing = transition_execution(
            execution,
            ToolExecutionState.EXECUTING,
            expected_row_version=execution.row_version,
            now=stamp,
        )
        return self.journal.save_execution(
            self.workspace_id, executing, expected_row_version=execution.row_version
        )

    def record_handler_completed(
        self, execution: DurableToolExecution, result, *, now: datetime | None = None
    ) -> DurableToolExecution:
        stamp = now or utc_now()
        disposition = (
            ToolExecutionDisposition.SUCCEEDED if result.ok else ToolExecutionDisposition.FAILED
        )
        completed = transition_execution(
            execution,
            ToolExecutionState.HANDLER_COMPLETED,
            expected_row_version=execution.row_version,
            disposition=disposition,
            now=stamp,
            result_envelope=_envelope_from_outcome(result),
            error_code=result.error_code.value if result.error_code is not None else None,
        )
        stored = self.journal.save_execution(
            self.workspace_id, completed, expected_row_version=execution.row_version
        )
        self.faults.check(FaultPoint.EXECUTION_AFTER_HANDLER_COMPLETED)
        return stored

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
        stamp = now or utc_now()
        writer = self.writer
        self.faults.check(FaultPoint.CONVERSATION_BEFORE_TOOL_MESSAGE_COMMIT)
        if execution.state is ToolExecutionState.CLOSED:

            def persist_only(txn: SqliteOperationalJournal) -> DurableToolExecution:
                del txn
                writer.persist_with_records(planned)
                return execution

            stored = self.journal.transact(persist_only)
            self.faults.check(FaultPoint.CONVERSATION_AFTER_TOOL_MESSAGE_COMMIT)
            self._session.log.apply_committed(planned)
            self._session.dirty = self._session.log.has_active_turn
            return stored
        closed = transition_execution(
            execution,
            ToolExecutionState.CLOSED,
            expected_row_version=execution.row_version,
            disposition=disposition,
            now=stamp,
        )

        def work(txn: SqliteOperationalJournal) -> DurableToolExecution:
            writer.persist_with_records(planned)
            stored = txn.save_execution(
                self.workspace_id, closed, expected_row_version=execution.row_version
            )
            return stored

        stored = self.journal.transact(work)
        self.faults.check(FaultPoint.CONVERSATION_AFTER_TOOL_MESSAGE_COMMIT)
        self._session.log.apply_committed(planned)
        self._session.dirty = self._session.log.has_active_turn
        return stored

    def close(self) -> None:
        self.store_session.close()


APPROVAL_TTL = timedelta(minutes=5)


def _envelope_from_outcome(result) -> HandlerResultEnvelope:
    error_code = result.error_code.value if getattr(result, "error_code", None) else None
    return HandlerResultEnvelope(
        ok=bool(result.ok),
        truncated=bool(getattr(result, "truncated", False)),
        summary={"chars": len(getattr(result, "envelope", "") or "")},
        error_code=error_code,
    )


def _last_assistant_text(log: ConversationLog) -> str | None:
    for message in reversed(log.messages_view()):
        if message.role == "assistant" and message.content:
            return message.content
    return None
