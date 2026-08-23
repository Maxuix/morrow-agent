"""Durable Turn submission, terminal commit, and Session restoration boundaries."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from morrow.adapters.state.preference_migration import legacy_entries_from_preferences
from morrow.application.preferences.jobs import PreferenceReviewJobEnqueuer
from morrow.application.preferences.run_projection import select_run_preferences
from morrow.application.recovery import RecoveryService
from morrow.application.tasks import TaskOutcomeAssembler, TaskService
from morrow.core.application import ApplicationError, ApplicationErrorCode
from morrow.core.context import ContextCheckpoint
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
from morrow.core.journal import TurnLifecycleJournalPort
from morrow.core.memory_selection import MemoryQuery, MemorySelection
from morrow.core.memory_selection_ports import (
    MemoryRunProjectionJournalPort,
    MemorySelectionAdmissionPort,
)
from morrow.core.models import (
    FinishReason,
    ModelRef,
    StatePresence,
    ToolDefinition,
    UserMessage,
)
from morrow.core.ports import IdSource
from morrow.core.preference_documents import PreferenceDocument
from morrow.core.recovery import RecoveryReport, RecoveryReportStatus
from morrow.core.store import StorageError, StorageErrorCode
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

from .learning.memory_run_projection import (
    build_run_context_projection,
    load_run_context_projection,
)
from .learning.memory_selector import MemorySelector


@dataclass
class DurableTurnState:
    """Mutable in-process projection of the currently durable foreground work."""

    turn_id: str | None = None
    task_run_id: str | None = None
    agent_run_id: str | None = None
    permission_snapshot_id: str | None = None
    last_client_message_id: str | None = None
    open_report: RecoveryReport | None = None
    pending_resume: bool = False

    def reset(self) -> None:
        self.turn_id = None
        self.task_run_id = None
        self.agent_run_id = None
        self.permission_snapshot_id = None
        self.last_client_message_id = None
        self.open_report = None
        self.pending_resume = False


@dataclass(frozen=True)
class TurnSubmitResult:
    kind: Literal["accepted", "closed_replay", "recovery", "conflict"]
    turn_id: str | None
    receipt: TurnSubmitReceipt | None = None
    assistant_text: str | None = None


@dataclass(frozen=True)
class _AcceptedTurn:
    turn_id: str
    task_run_id: str
    agent_run_id: str


@dataclass(frozen=True, slots=True)
class PreferenceRunSources:
    """Pre-transaction YAML sources, including empty layers for degraded reads."""

    global_document: PreferenceDocument
    workspace_document: PreferenceDocument
    workspace_presence: StatePresence = StatePresence.PRESENT
    refresh_status: Literal["ok", "degraded"] = "ok"
    refresh_error: str | None = None


class TurnSubmissionCoordinator:
    """Own atomic Turn admission, submit replay, and terminal Task transitions."""

    def __init__(
        self,
        journal: MemorySelectionAdmissionPort,
        *,
        workspace_id: str,
        id_source: IdSource,
        model: ModelRef,
        run_policy,
        runtime_instance_id: str,
        tasks: TaskService,
        clock: Callable[[], datetime],
        state: DurableTurnState,
        preference_reviews: PreferenceReviewJobEnqueuer | None = None,
        preference_loader: Callable[[], PreferenceRunSources] | None = None,
    ) -> None:
        self.journal = journal
        self.workspace_id = workspace_id
        self.id_source = id_source
        self.model = model
        self.run_policy = run_policy
        self.runtime_instance_id = runtime_instance_id
        self.tasks = tasks
        self.clock = clock
        self.state = state
        self.preference_reviews = preference_reviews
        self.preference_loader = preference_loader
        self.memory_selector = MemorySelector(id_source=id_source, clock=clock)

    def commit(
        self,
        planned: ConversationAppend,
        *,
        session: Session,
        writer: DurableConversationWriter,
    ) -> None:
        terminal = planned.added[-1] if planned.added else None
        if not isinstance(terminal, TurnTerminalRecord):
            writer.commit(planned)
            return

        def work(txn: TurnLifecycleJournalPort) -> bool:
            writer.persist_with_records(planned)
            if self.preference_reviews is not None:
                self.preference_reviews.enqueue_terminal_turn(
                    txn,
                    session=session,
                    turn_id=self.state.turn_id,
                    conversation=planned.snapshot,
                    terminal=terminal,
                )
            clear_task = self._apply_task_terminal_in_txn(txn, session, terminal)
            self._close_open_receipt_in_txn(txn, session)
            return clear_task

        clear_task = self.journal.transact(work)
        session.log.apply_committed(planned)
        session.dirty = session.log.has_active_turn
        if clear_task:
            self.state.task_run_id = None

    def close_open_receipt(self, session: Session) -> None:
        if self.state.last_client_message_id is None:
            return
        receipt = self.journal.get_receipt(
            self.workspace_id, session.session_id, self.state.last_client_message_id
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
        tools: tuple[ToolDefinition, ...] = (),
        writer: DurableConversationWriter,
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
            self._mark_needs_recovery(session)
            return TurnSubmitResult("recovery", existing.turn_id, existing)
        if session.health is SessionHealth.NEEDS_RECOVERY:
            return TurnSubmitResult("recovery", self.state.turn_id, None)
        if session.lifecycle is not SessionLifecycle.ACTIVE:
            raise ApplicationError(
                ApplicationErrorCode.INVALID,
                "only an active Session can start a Turn",
            )
        if session.health is not SessionHealth.OK:
            raise _turn_health_error(session.health)

        preference_sources = (
            self.preference_loader() if self.preference_loader is not None else None
        )
        planned = session.log.plan_begin_turn(UserMessage(content=user_input))
        command_id = self.id_source.new_id(COMMAND_ID_PREFIX)

        def work(txn: MemorySelectionAdmissionPort) -> _AcceptedTurn | TurnSubmitResult:
            row = txn.get_session(self.workspace_id, session.session_id)
            if row is None:
                stamp = self.clock()
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
                    return TurnSubmitResult("recovery", self.state.turn_id, None)
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
                stamp = self.clock()
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
            stamp = self.clock()
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
            selection = self.memory_selector.select(
                txn,
                MemoryQuery(
                    workspace_id=self.workspace_id,
                    task_run_id=task_id,
                    turn_id=turn_id,
                    task_goal=user_input,
                ),
                now=stamp,
            )
            snapshot = build_agent_run_snapshot(
                session,
                model=self.model,
                run_policy=self.run_policy,
                tools=tools,
                runtime_instance_id=self.runtime_instance_id,
                memory_selection=selection,
                preference_sources=preference_sources,
            )
            txn.put_memory_selection(self.workspace_id, selection)
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
            return _AcceptedTurn(turn_id, task_id, stored_agent_run_id)

        accepted = self.journal.transact(work)
        if isinstance(accepted, TurnSubmitResult):
            session.health = SessionHealth.NEEDS_RECOVERY
            return accepted
        try:
            projection = load_run_context_projection(
                self.journal, self.workspace_id, accepted.agent_run_id
            )
        except StorageError as exc:
            session.run_context_projection = None
            if exc.code is StorageErrorCode.NEEDS_REPAIR:
                session.health = SessionHealth.QUARANTINED
            raise
        self.state.turn_id = accepted.turn_id
        self.state.task_run_id = accepted.task_run_id
        self.state.agent_run_id = accepted.agent_run_id
        self.state.permission_snapshot_id = None
        self.state.last_client_message_id = client_message_id
        session.run_context_projection = projection
        if preference_sources is not None:
            session.generic_global_preferences = preference_sources.global_document
            session.generic_workspace_preferences = preference_sources.workspace_document
            session.global_preferences_revision = preference_sources.global_document.revision
            session.preferences_revision = preference_sources.workspace_document.revision
            session.workspace_preferences_presence = preference_sources.workspace_presence
        session.log.apply_committed(planned)
        session.dirty = True
        receipt = self.journal.get_receipt(self.workspace_id, session.session_id, client_message_id)
        return TurnSubmitResult("accepted", accepted.turn_id, receipt)

    def _mark_needs_recovery(self, session: Session) -> None:
        session.health = SessionHealth.NEEDS_RECOVERY
        row = self.journal.get_session(self.workspace_id, session.session_id)
        if row is not None and row.health is not SessionHealth.NEEDS_RECOVERY:
            self.journal.save_session(
                self.workspace_id,
                row.model_copy(update={"health": SessionHealth.NEEDS_RECOVERY}),
            )

    def _apply_task_terminal_in_txn(
        self,
        txn: TurnLifecycleJournalPort,
        session: Session,
        terminal: TurnTerminalRecord,
    ) -> bool:
        if self.state.task_run_id is None:
            return False
        task = txn.get_task_run(self.workspace_id, self.state.task_run_id)
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
            turn_id=self.state.turn_id,
        )
        if trigger is not None:
            outcomes = TaskOutcomeAssembler(
                txn,
                workspace_id=self.workspace_id,
                id_source=self.id_source,
                clock=self.clock,
            )
            txn.put_task_outcome(self.workspace_id, outcomes.build(updated, trigger=trigger))
        return target.is_terminal

    def _close_open_receipt_in_txn(self, txn: TurnLifecycleJournalPort, session: Session) -> None:
        if self.state.last_client_message_id is None:
            return
        receipt = txn.get_receipt(
            self.workspace_id, session.session_id, self.state.last_client_message_id
        )
        if receipt is None or receipt.disposition is TurnSubmitDisposition.ACCEPTED_CLOSED:
            return
        txn.update_receipt(
            self.workspace_id,
            receipt.model_copy(update={"disposition": TurnSubmitDisposition.ACCEPTED_CLOSED}),
        )


class SessionRestoreCoordinator:
    """Rebuild the in-process Session and durable foreground-work projection."""

    def __init__(
        self,
        journal: MemoryRunProjectionJournalPort,
        *,
        workspace_id: str,
        recovery: RecoveryService,
        clock: Callable[[], datetime],
        state: DurableTurnState,
    ) -> None:
        self.journal = journal
        self.workspace_id = workspace_id
        self.recovery = recovery
        self.clock = clock
        self.state = state

    def start_new_session(self, session: Session, session_id: str) -> None:
        stamp = self.clock()
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
        self.state.reset()

    def restore_into(self, session: Session) -> None:
        self.state.reset()
        session.run_context_projection = None
        row = self.journal.get_session(self.workspace_id, session.session_id)
        if row is None:
            stamp = self.clock()
            self.journal.create_session(
                DurableSession(
                    session_id=session.session_id,
                    workspace_id=self.workspace_id,
                    created_at=stamp,
                    updated_at=stamp,
                )
            )
            return
        session.lifecycle = row.lifecycle
        session.health = row.health
        session.context_checkpoint = self._restore_context_checkpoint(row)
        self.state.task_run_id = row.current_task_run_id
        try:
            session.log = restore_conversation_log(
                self.journal, self.workspace_id, session.session_id
            )
        except (ConversationLogError, ValueError):
            session.log = ConversationLog()
            session.health = SessionHealth.QUARANTINED
        except StorageError as exc:
            if exc.code is not StorageErrorCode.NEEDS_REPAIR:
                raise
            session.log = ConversationLog()
            session.health = SessionHealth.QUARANTINED
        else:
            self._restore_active_work(session)
        if session.health in (SessionHealth.NEEDS_RECOVERY, SessionHealth.QUARANTINED):
            self.journal.save_session(
                self.workspace_id,
                row.model_copy(update={"health": session.health}),
            )

    def synchronize_projection(self, session: Session) -> bool:
        """Restore a basic projection after an application transaction rolls back."""

        row = self.journal.get_session(self.workspace_id, session.session_id)
        if row is None:
            return False
        restored = restore_conversation_log(self.journal, self.workspace_id, session.session_id)
        session.log.install_snapshot(restored.snapshot())
        session.health = row.health
        session.lifecycle = row.lifecycle
        session.dirty = session.log.has_active_turn
        self.state.reset()
        self.state.task_run_id = row.current_task_run_id
        self._restore_active_work(session)
        return True

    def synchronize_recovery(
        self,
        session: Session,
        report: RecoveryReport,
        resumed_agent_run_id: str | None,
    ) -> None:
        row = self.journal.get_session(self.workspace_id, report.session_id)
        if row is None:
            return
        session.health = row.health
        self.state.task_run_id = row.current_task_run_id
        if resumed_agent_run_id is not None:
            self.state.agent_run_id = resumed_agent_run_id
            self.state.permission_snapshot_id = None
            session.run_context_projection = load_run_context_projection(
                self.journal, self.workspace_id, resumed_agent_run_id
            )
        elif report.status is RecoveryReportStatus.RESOLVED:
            session.run_context_projection = None
        self.state.open_report = None if report.status is RecoveryReportStatus.RESOLVED else report

    def _restore_context_checkpoint(self, row: DurableSession) -> ContextCheckpoint | None:
        try:
            checkpoints = self.journal.list_context_checkpoints(self.workspace_id, row.session_id)
            checkpoint = checkpoints[-1] if checkpoints else None
            if checkpoint is None and row.parent_checkpoint_id is not None:
                checkpoint = self.journal.get_context_checkpoint(
                    self.workspace_id, row.parent_checkpoint_id
                )
            return checkpoint
        except StorageError:
            return None

    def _restore_active_work(self, session: Session) -> None:
        session.run_context_projection = None
        report = self.recovery.discover(session.session_id, session.log)
        self.state.open_report = report
        if report is not None:
            self.state.turn_id = report.turn_id
            self.state.agent_run_id = report.agent_run_id
            if report.turn_id is not None:
                turn = self.journal.get_turn(self.workspace_id, report.turn_id)
                if turn is not None:
                    self.state.last_client_message_id = turn.client_message_id
            interrupted = (
                self.journal.get_agent_run(self.workspace_id, report.agent_run_id)
                if report.agent_run_id is not None
                else None
            )
            self.state.permission_snapshot_id = (
                interrupted.permission_snapshot_id if interrupted is not None else None
            )
            if interrupted is not None:
                self._install_memory_projection(session, interrupted.snapshot)
        if report is not None and session.health is not SessionHealth.QUARANTINED:
            session.health = SessionHealth.NEEDS_RECOVERY
        elif session.lifecycle is SessionLifecycle.ACTIVE and session.log.has_active_turn:
            turns = self.journal.list_session_turns(self.workspace_id, session.session_id)
            runs = self.journal.list_session_agent_runs(self.workspace_id, session.session_id)
            self.state.turn_id = turns[-1].turn_id if turns else None
            self.state.agent_run_id = runs[-1].agent_run_id if runs else None
            if turns:
                self.state.last_client_message_id = turns[-1].client_message_id
            if runs:
                self._install_memory_projection(session, runs[-1].snapshot)
            self.state.pending_resume = session.health is SessionHealth.OK

    def _install_memory_projection(self, session: Session, snapshot: AgentRunSnapshot) -> None:
        try:
            session.run_context_projection = build_run_context_projection(
                self.journal, self.workspace_id, snapshot
            )
        except StorageError as exc:
            if exc.code is StorageErrorCode.NEEDS_REPAIR:
                session.run_context_projection = None
                session.health = SessionHealth.QUARANTINED
                return
            raise


def request_digest(user_input: str) -> str:
    return sha256_digest(canonical_json_bytes({"content": user_input}))


def build_agent_run_snapshot(
    session: Session,
    *,
    model: ModelRef,
    run_policy,
    tools: tuple[ToolDefinition, ...],
    runtime_instance_id: str,
    memory_selection: MemorySelection | None = None,
    preference_sources: PreferenceRunSources | None = None,
) -> AgentRunSnapshot:
    def source_digest(presence: StatePresence, value) -> str:
        if value is not None and not hasattr(value, "model_dump"):
            value = [item.model_dump(mode="json") for item in value]
        payload = value.model_dump(mode="json") if hasattr(value, "model_dump") else value
        return sha256_digest(
            canonical_json_bytes(
                {
                    "presence": presence.value,
                    "value": payload,
                }
            )
        )

    global_document = preference_sources.global_document if preference_sources is not None else None
    workspace_document = (
        preference_sources.workspace_document if preference_sources is not None else None
    )
    global_entries = (
        global_document.entries
        if global_document is not None
        else session.generic_global_preferences.entries
        if session.generic_global_preferences is not None
        else legacy_entries_from_preferences(
            "global", session.global_preferences.model_dump(mode="python")
        )
    )
    workspace_entries = (
        workspace_document.entries
        if workspace_document is not None
        else session.generic_workspace_preferences.entries
        if session.generic_workspace_preferences is not None
        else legacy_entries_from_preferences(
            "workspace", session.workspace_preferences.model_dump(mode="python")
        )
    )
    session_entries = (
        session.generic_session_preferences
        if session.generic_session_preferences
        else legacy_entries_from_preferences(
            "session", session.preferences.model_dump(mode="python"), allow_session=True
        )
    )
    preference_projection = select_run_preferences(
        global_entries,
        workspace_entries,
        session_entries,
    )
    global_revision = (
        global_document.revision
        if global_document is not None
        else session.global_preferences_revision
    )
    workspace_revision = (
        workspace_document.revision
        if workspace_document is not None
        else session.preferences_revision
    )
    workspace_presence = (
        preference_sources.workspace_presence
        if preference_sources is not None
        else session.workspace_preferences_presence
    )

    revisions = (
        SourceRevisionRef(
            kind="global_config",
            revision=global_revision,
            content_sha256=source_digest(
                StatePresence.PRESENT,
                global_document or session.generic_global_preferences or session.global_preferences,
            ),
        ),
        SourceRevisionRef(
            kind="workspace_profile",
            revision=session.profile_revision,
            content_sha256=source_digest(session.profile_presence, session.profile),
        ),
        SourceRevisionRef(
            kind="workspace_preferences",
            revision=workspace_revision,
            content_sha256=source_digest(
                workspace_presence,
                workspace_document
                or session.generic_workspace_preferences
                or session.workspace_preferences,
            ),
        ),
        SourceRevisionRef(
            kind="session_preferences",
            revision=0,
            content_sha256=source_digest(StatePresence.PRESENT, session_entries),
        ),
    )
    tool_payload = [tool.model_dump(mode="json") for tool in tools]
    return AgentRunSnapshot(
        profile=session.profile,
        model=model,
        provider_id=model.provider_id,
        source_revisions=revisions,
        run_policy_digest=sha256_digest(canonical_json_bytes(run_policy.model_dump(mode="json"))),
        tool_schema_digest=sha256_digest(canonical_json_bytes(tool_payload)),
        permission_profile_digest=sha256_digest(
            canonical_json_bytes(session.permission_profile.model_dump(mode="json"))
        ),
        runtime_instance_id=runtime_instance_id,
        memory_selection_id=memory_selection.selection_id if memory_selection else None,
        memory_selection_digest=memory_selection.selection_digest if memory_selection else None,
        memory_snapshot_revision=(
            memory_selection.source_memory_revision if memory_selection is not None else None
        ),
        frozen_preferences=preference_projection.entries,
        preference_projection_digest=preference_projection.digest,
        preference_omitted_count=preference_projection.omitted_count,
        preference_source_scopes=preference_projection.source_scopes,
        preference_refresh_status=(
            preference_sources.refresh_status if preference_sources is not None else "legacy"
        ),
        preference_refresh_error=(
            preference_sources.refresh_error if preference_sources is not None else None
        ),
    )


def _turn_health_error(health: SessionHealth) -> ApplicationError:
    codes = {
        SessionHealth.QUARANTINED: ApplicationErrorCode.QUARANTINED,
        SessionHealth.READ_ONLY: ApplicationErrorCode.READ_ONLY,
    }
    return ApplicationError(
        codes[health],
        f"Session health {health.value} cannot start a Turn",
    )


def _last_assistant_text(log: ConversationLog) -> str | None:
    for message in reversed(log.messages_view()):
        if message.role == "assistant" and message.content:
            return message.content
    return None
