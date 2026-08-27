"""Turn-submit coordination. ConversationLog remains the only chat writer."""

from __future__ import annotations

from datetime import datetime

from morrow.adapters.state.journal import SqliteOperationalJournal
from morrow.adapters.state.operational import OperationalStoreSession
from morrow.application.compaction_persistence import checkpoint_for_compaction
from morrow.application.preferences.jobs import PreferenceReviewJobEnqueuer
from morrow.application.recovery import RecoveryService
from morrow.application.tasks import TaskService
from morrow.application.tool_persistence import (
    DurableToolExecutionCoordinator,
    ToolConversationPersistence,
)
from morrow.application.turn_lifecycle import (
    DurableTurnState,
    SessionRestoreCoordinator,
    TurnSubmissionCoordinator,
    TurnSubmitResult,
)
from morrow.application.turn_permissions import RunPermissionCoordinator
from morrow.core.compaction import CompactionEntry
from morrow.core.domain import AgentRunSnapshot
from morrow.core.execution import (
    DurableApproval,
    DurableToolExecution,
    ToolExecutionDisposition,
)
from morrow.core.faults import FaultInjector, FaultPoint, NoOpFaultInjector
from morrow.core.mcp import McpReviewEvidence
from morrow.core.models import AssistantMessage, ModelRef, utc_now
from morrow.core.observability import MODEL_REQUEST_ID_PREFIX
from morrow.core.permissions import PermissionSnapshot
from morrow.core.ports import Clock, IdSource
from morrow.core.recovery import RecoveryReport
from morrow.runtime.conversation import ConversationAppend
from morrow.runtime.durable_log import DurableConversationWriter
from morrow.runtime.session import Session


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
        preference_loader=None,
        skill_selection=None,
        prompt_assembler=None,
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
        self.prompt_assembler = prompt_assembler
        workspace_root = mutation.files.resolver.root if mutation is not None else None
        self.recovery = recovery or RecoveryService(
            journal,
            workspace_id=workspace_id,
            id_source=id_source,
            workspace_root=workspace_root,
        )
        self.faults = faults or NoOpFaultInjector()
        self.clock = clock or store_session
        self.turn_state = DurableTurnState()
        self.tasks = TaskService(
            journal=journal,
            workspace_id=workspace_id,
            id_source=id_source,
            clock=self._now,
        )
        self.preference_reviews = PreferenceReviewJobEnqueuer(
            workspace_id=workspace_id,
            id_source=id_source,
            clock=self._now,
        )
        self.turn_submission = TurnSubmissionCoordinator(
            journal,
            workspace_id=workspace_id,
            id_source=id_source,
            model=model,
            run_policy=run_policy,
            runtime_instance_id=runtime_instance_id,
            tasks=self.tasks,
            clock=self._now,
            state=self.turn_state,
            preference_reviews=self.preference_reviews,
            preference_loader=preference_loader,
            skill_selection=skill_selection,
            prompt_assembler=prompt_assembler,
        )
        self.session_restore = SessionRestoreCoordinator(
            journal,
            workspace_id=workspace_id,
            recovery=self.recovery,
            clock=self._now,
            state=self.turn_state,
            prompt_assembler=prompt_assembler,
        )
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
        self.writer: DurableConversationWriter | None = None
        self._session: Session | None = None

    @property
    def current_turn_id(self) -> str | None:
        return self.turn_state.turn_id

    @property
    def current_task_run_id(self) -> str | None:
        return self.turn_state.task_run_id

    @property
    def current_agent_run_id(self) -> str | None:
        return self.turn_state.agent_run_id

    @property
    def current_permission_snapshot_id(self) -> str | None:
        return self.turn_state.permission_snapshot_id

    @property
    def open_report(self) -> RecoveryReport | None:
        return self.turn_state.open_report

    @property
    def pending_resume(self) -> bool:
        return self.turn_state.pending_resume

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
        mcp_review_evidence: tuple[McpReviewEvidence, ...] = (),
        now: datetime | None = None,
    ) -> PermissionSnapshot:
        """Freeze base permissions and any already-explicit run-bound grant once."""

        del tools
        snapshot = self.permissions.freeze(
            session,
            agent_run_id=self.current_agent_run_id,
            task_run_id=self.current_task_run_id,
            turn_id=self.current_turn_id,
            mcp_review_evidence=mcp_review_evidence,
            now=now,
        )
        self.turn_state.permission_snapshot_id = snapshot.permission_snapshot_id
        return snapshot

    def commit(self, planned: ConversationAppend) -> None:
        if self.writer is None or self._session is None:
            raise RuntimeError("session persistence is not attached")
        self.turn_submission.commit(planned, session=self._session, writer=self.writer)

    def close_open_receipt(self) -> None:
        if self._session is not None:
            self.turn_submission.close_open_receipt(self._session)

    def probe(
        self,
        session: Session,
        user_input: str,
        client_message_id: str,
    ) -> TurnSubmitResult:
        """Read-only submission classification; no IDs, YAML reads or writes."""
        return self.turn_submission.probe(session, user_input, client_message_id)

    def submit_user(
        self,
        session: Session,
        user_input: str,
        client_message_id: str,
        *,
        turn_id: str,
        agent_run_id: str,
        tools: tuple = (),
        prepared_spec=None,
        prepared_mcp_run=None,
        prompt_projection=None,
    ) -> TurnSubmitResult:
        if self.writer is None:
            raise RuntimeError("session persistence is not attached")
        return self.turn_submission.submit_user(
            session,
            user_input,
            client_message_id,
            turn_id=turn_id,
            agent_run_id=agent_run_id,
            tools=tools,
            prepared_spec=prepared_spec,
            prepared_mcp_run=prepared_mcp_run,
            prompt_projection=prompt_projection,
            writer=self.writer,
        )

    def get_open_run_snapshot(self) -> AgentRunSnapshot | None:
        """Return the durable foreground AgentRun's frozen snapshot, if any."""
        if self.current_agent_run_id is None:
            return None
        run = self.journal.get_agent_run(self.workspace_id, self.current_agent_run_id)
        return run.snapshot if run is not None else None

    def admit_model_request(self, **kwargs):
        """Record bounded Provider admission without exposing the SQLite journal to AgentLoop."""

        kwargs.setdefault("agent_run_id", self.current_agent_run_id)
        if kwargs["agent_run_id"] is None:
            raise RuntimeError("model request admission requires an open AgentRun")
        kwargs.setdefault("model_request_id", self.id_source.new_id(MODEL_REQUEST_ID_PREFIX))
        return self.journal.admit_model_request(self.workspace_id, **kwargs)

    def settle_model_request(self, model_request_id: str, **kwargs):
        """Settle one admitted Provider request exactly once."""

        return self.journal.settle_model_request(self.workspace_id, model_request_id, **kwargs)

    def finalize_agent_run(self, **kwargs):
        """Persist the bounded terminal aggregate for the current AgentRun."""

        kwargs.setdefault("agent_run_id", self.current_agent_run_id)
        if kwargs["agent_run_id"] is None:
            raise RuntimeError("AgentRun finalization requires an open AgentRun")
        return self.journal.finalize_agent_run(self.workspace_id, **kwargs)

    def get_agent_run_observation(self, agent_run_id: str | None = None):
        selected = agent_run_id or self.current_agent_run_id
        if selected is None:
            return None
        return self.journal.get_agent_run_observation(self.workspace_id, selected)

    def start_new_session(self, session: Session, session_id: str) -> None:
        self.session_restore.start_new_session(session, session_id)
        self.attach(session)

    def restore_into(self, session: Session) -> None:
        self.session_restore.restore_into(session)
        self.attach(session)

    def synchronize_projection(self, session: Session) -> None:
        """Refresh the attached projection after an application transaction rolls back."""

        if self.session_restore.synchronize_projection(session):
            self.attach(session)

    def synchronize_task_projection(self, task_run_id: str | None) -> None:
        """Synchronize the foreground Task after a committed Task command."""

        self.turn_state.task_run_id = task_run_id

    def persist_compaction_entry(self, entry: CompactionEntry) -> None:
        """Persist one model-context projection without appending a chat record."""

        if self._session is not None and entry.session_id != self._session.session_id:
            raise ValueError("compaction entry belongs to another Session")
        records = self.journal.load_effective_records(self.workspace_id, entry.session_id)
        checkpoint = checkpoint_for_compaction(self.workspace_id, entry, records)
        self.journal.put_context_checkpoint(self.workspace_id, checkpoint)

    def synchronize_recovery_projection(
        self, report: RecoveryReport, *, resumed_agent_run_id: str | None = None
    ) -> None:
        """Apply committed Recovery state without exposing private persistence fields."""

        session = self._session
        if session is None or session.session_id != report.session_id:
            return
        self.session_restore.synchronize_recovery(session, report, resumed_agent_run_id)

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
