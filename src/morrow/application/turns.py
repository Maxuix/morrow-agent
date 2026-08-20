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
from morrow.core.artifacts import ArtifactError
from morrow.core.domain import (
    AGENT_RUN_ID_PREFIX,
    COMMAND_ID_PREFIX,
    TASK_RUN_ID_PREFIX,
    AgentRunSnapshot,
    ArtifactReference,
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
    EffectClass,
    HandlerResultEnvelope,
    ToolExecutionDisposition,
    ToolExecutionState,
    approval_preview_digest,
    assert_handler_may_enter,
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
from morrow.core.permissions import (
    PERMISSION_POLICY_VERSION,
    CapabilityGrant,
    CapabilityIsolation,
    IsolationLabel,
    PermissionEvidenceError,
    PermissionSnapshot,
    assert_grant_snapshot_matches,
    capability_grant_digest,
    workspace_root_digest,
)
from morrow.core.ports import Clock, IdSource
from morrow.core.recovery import RecoveryReport, RecoveryReportStatus, RecoveryResolution
from morrow.core.store import StorageError
from morrow.runtime.conversation import (
    ConversationAppend,
    ConversationLog,
    ConversationLogError,
    TurnTerminalRecord,
)
from morrow.runtime.durable_log import (
    DurableConversationWriter,
    durable_call_id,
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


def build_permission_snapshot(
    session: Session,
    *,
    workspace_id: str,
    base_snapshot: AgentRunSnapshot,
    permission_snapshot_id: str,
    task_run_id: str,
    turn_id: str,
    agent_run_id: str,
    grant: CapabilityGrant | None = None,
    created_at: datetime | None = None,
) -> PermissionSnapshot:
    stamp = created_at or utc_now()
    capability = session.workspace_capability
    root_digest = (
        workspace_root_digest(capability.root)
        if capability is not None
        else sha256_digest(f"workspace:{workspace_id}".encode())
    )
    if grant is not None:
        if not grant.is_active(stamp):
            raise RuntimeError("capability grant is expired or revoked")
        if (
            grant.workspace_id != workspace_id
            or grant.task_run_id != task_run_id
            or grant.agent_run_id != agent_run_id
        ):
            raise RuntimeError("capability grant does not match the AgentRun subjects")
        grant_digest = capability_grant_digest(grant)
        capabilities = grant.capabilities
        isolations = tuple(
            CapabilityIsolation(
                capability=item,
                isolation=IsolationLabel.UNCONFINED_HOST,
            )
            for item in capabilities
        )
    else:
        grant_digest = None
        capabilities = ()
        isolations = ()
    return PermissionSnapshot(
        permission_snapshot_id=permission_snapshot_id,
        workspace_id=workspace_id,
        session_id=session.session_id,
        task_run_id=task_run_id,
        turn_id=turn_id,
        agent_run_id=agent_run_id,
        access_scope=session.permission_profile.access_scope,
        approval_mode=session.permission_profile.approval_mode,
        process_isolation=session.permission_profile.process_isolation,
        workspace_root_digest=root_digest,
        workspace_read_only=session.read_only or bool(capability and capability.read_only),
        tool_schema_digest=base_snapshot.tool_schema_digest,
        run_policy_digest=base_snapshot.run_policy_digest,
        permission_profile_digest=base_snapshot.permission_profile_digest,
        policy_version=PERMISSION_POLICY_VERSION,
        source_revisions=base_snapshot.source_revisions,
        grant_id=grant.grant_id if grant is not None else None,
        grant_digest=grant_digest,
        granted_capabilities=capabilities,
        capability_isolations=isolations,
        created_at=stamp,
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
        self.clock = clock
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

    def _now(self) -> datetime:
        return self.clock.now() if self.clock is not None else utc_now()

    def freeze_permission_snapshot(
        self,
        session: Session,
        *,
        tools: tuple = (),
        now: datetime | None = None,
    ) -> PermissionSnapshot:
        """Freeze base permissions and any already-explicit run-bound grant once."""

        if self.current_agent_run_id is None:
            raise RuntimeError("permission snapshot requires an open AgentRun")
        run = self.journal.get_agent_run(self.workspace_id, self.current_agent_run_id)
        if run is None:
            raise RuntimeError("durable AgentRun is missing")
        if run.permission_snapshot_id is not None:
            snapshot = self.journal.get_permission_snapshot(
                self.workspace_id, run.permission_snapshot_id
            )
            if snapshot is None:
                raise RuntimeError("durable PermissionSnapshot is missing")
            self.current_permission_snapshot_id = snapshot.permission_snapshot_id
            return snapshot
        if self.current_turn_id is None or self.current_task_run_id is None:
            raise RuntimeError("permission snapshot subjects are incomplete")
        # The durable AgentRun snapshot is the authority for the later freeze.
        # In particular, a crash-resumed run may intentionally start with no
        # live tool definitions; rebuilding from that transient input would
        # make its permission evidence impossible to persist deterministically.
        base = run.snapshot
        stamp = now or self._now()
        candidates = tuple(
            grant
            for grant in self.journal.list_capability_grants(
                self.workspace_id, agent_run_id=self.current_agent_run_id
            )
            if grant.is_active(stamp)
        )
        if len(candidates) > 1:
            raise RuntimeError("AgentRun has conflicting active capability grants")
        grant = candidates[0] if candidates else None
        snapshot = build_permission_snapshot(
            session,
            workspace_id=self.workspace_id,
            base_snapshot=base,
            permission_snapshot_id=self.id_source.new_id("psnap"),
            task_run_id=self.current_task_run_id,
            turn_id=self.current_turn_id,
            agent_run_id=self.current_agent_run_id,
            grant=grant,
            created_at=stamp,
        )
        self.journal.freeze_agent_run_permission_snapshot(
            self.workspace_id, self.current_agent_run_id, snapshot
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
            self.current_permission_snapshot_id = None
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
        self.current_permission_snapshot_id = None
        self.open_report = None
        self.pending_resume = False
        self.context_checkpoint = None
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
            elif session.log.has_active_turn and session.health is SessionHealth.OK:
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
        grant_id = snapshot.grant_id
        isolation_label = snapshot.isolation_label if grant_id is not None else None
        if grant_id is not None:
            grant = self.journal.get_capability_grant(self.workspace_id, grant_id)
            if grant is None or not grant.is_active(self._now()):
                grant_id = None
                isolation_label = None
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
            permission_snapshot_id=snapshot.permission_snapshot_id,
            grant_id=grant_id,
            isolation_label=isolation_label,
        )
        writer = self.writer
        self.faults.check(FaultPoint.CONVERSATION_BEFORE_COMMIT)

        def work(txn: SqliteOperationalJournal) -> tuple[DurableToolExecution, ...]:
            durables, _snapshot = writer.persist_with_records(planned)
            assistant_id = durables[0].record_id if durables else None
            stored: list[DurableToolExecution] = []
            for execution in executions:
                durable_id = durable_call_id(execution.call_id)
                stored.append(
                    txn.put_execution(
                        self.workspace_id,
                        execution.model_copy(
                            update={
                                "assistant_record_id": assistant_id,
                                "call_id": durable_id,
                                "intent": execution.intent.model_copy(
                                    update={"call_id": durable_id}
                                ),
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
        report = self.open_report
        updated, receipt, planned = self.recovery.decide(
            report,
            command_id=command_id,
            resolution=resolution,
            item_id=item_id,
            log=session.log,
        )
        if receipt.kind == "conflict":
            raise RuntimeError("recovery command conflicts with a previous request")
        if receipt.kind == "replay":
            return self.open_report
        resumed_agent_run_id: list[str] = []
        saved = self.recovery.commit_decision(
            updated,
            receipt,
            planned=planned,
            log=session.log,
            writer=self.writer,
            close_all=resolution is RecoveryResolution.ABORT and item_id is None,
            finalize=lambda txn, saved_report: self._finalize_recovery_in_txn(
                txn,
                session,
                report=report,
                saved=saved_report,
                resolution=resolution,
                resumed_agent_run_id=resumed_agent_run_id,
            ),
        )
        self.open_report = saved if saved.status is not RecoveryReportStatus.RESOLVED else None
        if saved.status is RecoveryReportStatus.QUARANTINED:
            session.health = SessionHealth.QUARANTINED
        elif saved.status is RecoveryReportStatus.RESOLVED:
            session.health = SessionHealth.OK
            if resumed_agent_run_id:
                self.current_agent_run_id = resumed_agent_run_id[0]
                self.current_permission_snapshot_id = None
        else:
            session.health = SessionHealth.NEEDS_RECOVERY
        return saved

    def _finalize_recovery_in_txn(
        self,
        txn: SqliteOperationalJournal,
        session,
        *,
        report: RecoveryReport,
        saved: RecoveryReport,
        resolution: RecoveryResolution,
        resumed_agent_run_id: list[str],
    ) -> None:
        row = txn.get_session(self.workspace_id, session.session_id)
        if row is None:
            raise RuntimeError("operational Session is missing during recovery")
        health = SessionHealth.NEEDS_RECOVERY
        task_id = row.current_task_run_id
        if saved.status is RecoveryReportStatus.QUARANTINED:
            health = SessionHealth.QUARANTINED
        elif saved.status is RecoveryReportStatus.RESOLVED:
            health = SessionHealth.OK
            if resolution is RecoveryResolution.RESUME and saved.agent_run_id is not None:
                previous = txn.get_agent_run(self.workspace_id, saved.agent_run_id)
                if previous is None:
                    raise RuntimeError("recovery AgentRun is missing")
                new_id = self.id_source.new_id(AGENT_RUN_ID_PREFIX)
                txn.create_agent_run(
                    self.workspace_id,
                    DurableAgentRun(
                        agent_run_id=new_id,
                        turn_id=previous.turn_id,
                        session_id=previous.session_id,
                        resume_of_agent_run_id=previous.agent_run_id,
                        snapshot=previous.snapshot.model_copy(
                            update={"runtime_instance_id": self.runtime_instance_id}
                        ),
                    ),
                )
                resumed_agent_run_id.append(new_id)
            elif resolution is RecoveryResolution.ABORT:
                task_id = self._abort_recovery_task_in_txn(txn, row, turn_id=report.turn_id)
                self._close_recovery_receipt_in_txn(txn, report)
        txn.save_session(
            self.workspace_id,
            row.model_copy(update={"health": health, "current_task_run_id": task_id}),
        )

    def _abort_recovery_task_in_txn(
        self, txn: SqliteOperationalJournal, session, *, turn_id: str | None
    ) -> str | None:
        task_id = session.current_task_run_id
        if task_id is None:
            return None
        task = txn.get_task_run(self.workspace_id, task_id)
        if task is None:
            raise RuntimeError("recovery TaskRun is missing")
        if task.status in {TaskRunStatus.OPEN, TaskRunStatus.READY_FOR_ACCEPTANCE}:
            task = self.tasks._transition_in_txn(
                txn,
                task,
                TaskRunStatus.CANCELLED,
                reason="recovery_abort",
                turn_id=turn_id,
            )
            self.tasks._outcome_in_txn(
                txn,
                task,
                trigger=TaskOutcomeTrigger.TERMINAL_CLOSE,
                summary="TaskRun cancelled during recovery abort.",
            )
            return None
        return task_id if not task.status.is_terminal else None

    def _close_recovery_receipt_in_txn(
        self, txn: SqliteOperationalJournal, report: RecoveryReport
    ) -> None:
        if report.turn_id is None:
            return
        turn = txn.get_turn(self.workspace_id, report.turn_id)
        if turn is None:
            return
        receipt = txn.get_receipt(self.workspace_id, report.session_id, turn.client_message_id)
        if receipt is None or receipt.disposition is TurnSubmitDisposition.ACCEPTED_CLOSED:
            return
        txn.update_receipt(
            self.workspace_id,
            receipt.model_copy(update={"disposition": TurnSubmitDisposition.ACCEPTED_CLOSED}),
        )

    def execution_is_visible(self, tool_execution_id: str) -> bool:
        return self.journal.get_execution(self.workspace_id, tool_execution_id) is not None

    def create_pending_approval(
        self, execution: DurableToolExecution, *, now: datetime | None = None
    ) -> DurableApproval:
        stamp = now or self._now()
        self._assert_execution_permission(execution, stamp)
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
            permission_snapshot_id=execution.permission_snapshot_id,
            grant_id=execution.grant_id,
            isolation=execution.isolation,
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
        stamp = now or self._now()
        self._assert_execution_permission(execution, stamp)
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
        stamp = now or self._now()
        self._assert_execution_permission(execution, stamp)
        executing = transition_execution(
            execution,
            ToolExecutionState.EXECUTING,
            expected_row_version=execution.row_version,
            now=stamp,
        )
        return self.journal.save_execution(
            self.workspace_id, executing, expected_row_version=execution.row_version
        )

    def _assert_execution_permission(self, execution: DurableToolExecution, now: datetime) -> None:
        if execution.permission_snapshot_id is None:
            if execution.grant_id is not None or execution.isolation is not None:
                raise PermissionEvidenceError("execution permission evidence is incomplete")
            return
        snapshot = self.journal.get_permission_snapshot(
            self.workspace_id, execution.permission_snapshot_id
        )
        if snapshot is None:
            raise PermissionEvidenceError("permission snapshot is missing")
        if (
            snapshot.session_id != execution.session_id
            or snapshot.turn_id != execution.turn_id
            or snapshot.task_run_id != execution.task_run_id
            or snapshot.agent_run_id != execution.agent_run_id
        ):
            raise PermissionEvidenceError("execution permission snapshot subjects are mismatched")
        if snapshot.grant_id is None:
            if execution.grant_id is not None or execution.isolation is not None:
                raise PermissionEvidenceError("execution cannot add elevated evidence")
            return
        if execution.grant_id is None:
            if execution.isolation is not None:
                raise PermissionEvidenceError("execution added an incomplete elevated label")
            if (
                execution.tool_name == "run_command"
                and execution.intent.effect_class is EffectClass.UNCONFINED_EXTERNAL_EFFECT
                and execution.intent.requires_approval
            ):
                raise PermissionEvidenceError("elevated Host execution dropped grant evidence")
            return
        if execution.isolation is not snapshot.isolation_label:
            raise PermissionEvidenceError("execution elevated evidence is mismatched")
        grant = self.journal.get_capability_grant(self.workspace_id, execution.grant_id)
        if grant is None:
            raise PermissionEvidenceError("capability grant is missing")
        assert_grant_snapshot_matches(
            snapshot,
            grant,
            now=now,
            workspace_id=self.workspace_id,
            task_run_id=execution.task_run_id,
            agent_run_id=execution.agent_run_id,
        )

    def get_execution(self, tool_execution_id: str) -> DurableToolExecution | None:
        return self.journal.get_execution(self.workspace_id, tool_execution_id)

    def _close_execution_before_handler(
        self,
        execution: DurableToolExecution,
        *,
        disposition: ToolExecutionDisposition,
        now: datetime | None = None,
    ) -> DurableToolExecution:
        """Close an execution whose handler has not been allowed to enter."""

        current = self.get_execution(execution.tool_execution_id)
        if current is None:
            raise PermissionEvidenceError("tool execution is missing")
        if current.state is ToolExecutionState.CLOSED:
            return current
        if current.state is ToolExecutionState.HANDLER_COMPLETED:
            return current
        stamp = now or self._now()
        approval = self.journal.get_approval_for_execution(
            self.workspace_id, current.tool_execution_id
        )
        resolved_approval = None
        if (
            approval is not None
            and approval.resolution is ApprovalResolution.PENDING
            and approval.consumed_at is None
        ):
            resolved_approval = resolve_approval(
                approval,
                approved=False,
                expected_row_version=approval.row_version,
                now=stamp,
            )
        closed = transition_execution(
            current,
            ToolExecutionState.CLOSED,
            expected_row_version=current.row_version,
            disposition=disposition,
            now=stamp,
        )
        if resolved_approval is None:
            return self.journal.save_execution(
                self.workspace_id, closed, expected_row_version=current.row_version
            )

        def work(txn: SqliteOperationalJournal) -> DurableToolExecution:
            txn.save_approval(
                self.workspace_id,
                resolved_approval,
                expected_row_version=approval.row_version,
            )
            return txn.save_execution(
                self.workspace_id, closed, expected_row_version=current.row_version
            )

        return self.journal.transact(work)

    def deny_execution_before_handler(
        self, execution: DurableToolExecution, *, now: datetime | None = None
    ) -> DurableToolExecution:
        return self._close_execution_before_handler(
            execution, disposition=ToolExecutionDisposition.DENIED, now=now
        )

    def cancel_execution_before_handler(
        self, execution: DurableToolExecution, *, now: datetime | None = None
    ) -> DurableToolExecution:
        return self._close_execution_before_handler(
            execution, disposition=ToolExecutionDisposition.CANCELLED, now=now
        )

    def assert_handler_may_enter(
        self, execution: DurableToolExecution, *, now: datetime | None = None
    ) -> DurableToolExecution:
        """Re-read immutable evidence immediately before a side-effecting handler."""

        stamp = now or self._now()
        current = self.get_execution(execution.tool_execution_id)
        if current is None:
            raise PermissionEvidenceError("tool execution is missing")
        approval = (
            self.journal.get_approval_for_execution(self.workspace_id, current.tool_execution_id)
            if current.intent.requires_approval
            else None
        )
        snapshot = (
            self.journal.get_permission_snapshot(self.workspace_id, current.permission_snapshot_id)
            if current.permission_snapshot_id is not None
            else None
        )
        grant = (
            self.journal.get_capability_grant(self.workspace_id, current.grant_id)
            if current.grant_id is not None
            else None
        )
        assert_handler_may_enter(
            current,
            approval,
            now=stamp,
            permission_snapshot=snapshot,
            grant=grant,
        )
        return current

    def record_handler_completed(
        self,
        execution: DurableToolExecution,
        result,
        *,
        now: datetime | None = None,
        disposition: ToolExecutionDisposition | None = None,
    ) -> DurableToolExecution:
        stamp = now or self._now()
        final_disposition = disposition or (
            ToolExecutionDisposition.SUCCEEDED if result.ok else ToolExecutionDisposition.FAILED
        )
        artifact_refs: tuple[ArtifactReference, ...] = ()
        if self.artifacts is not None and execution.tool_name == "run_command":
            try:
                artifact = self.artifacts.publish_command_output(
                    result.envelope,
                    session_id=execution.session_id,
                    task_run_id=execution.task_run_id,
                    tool_execution_id=execution.tool_execution_id,
                )
                artifact_refs = (
                    ArtifactReference(artifact_id=artifact.artifact_id, role="tool_output"),
                )
            except (ArtifactError, StorageError):
                # The bounded inline execution envelope remains the truthful fallback;
                # publication failures leave explicit Artifact metadata for diagnosis.
                artifact_refs = ()
        completed = transition_execution(
            execution,
            ToolExecutionState.HANDLER_COMPLETED,
            expected_row_version=execution.row_version,
            disposition=final_disposition,
            now=stamp,
            result_envelope=_envelope_from_outcome(result),
            error_code=result.error_code.value if result.error_code is not None else None,
        )
        if artifact_refs:
            completed = completed.model_copy(update={"artifact_refs": artifact_refs})
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
        stamp = now or self._now()
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
