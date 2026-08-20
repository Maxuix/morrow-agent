"""Focused persistence collaborators for one durable tool cycle."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta

from morrow.application.artifacts import ArtifactService
from morrow.application.prepared import prepare_cycle_executions
from morrow.application.turn_permissions import RunPermissionCoordinator
from morrow.core.artifacts import ArtifactError
from morrow.core.capabilities import ToolRunContext
from morrow.core.domain import ArtifactReference
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
from morrow.core.faults import FaultInjector, FaultPoint
from morrow.core.journal import DurableToolJournalPort
from morrow.core.models import AssistantMessage
from morrow.core.permissions import IsolationLabel, PermissionEvidenceError
from morrow.core.ports import IdSource
from morrow.core.store import StorageError
from morrow.runtime.conversation import ConversationAppend
from morrow.runtime.durable_log import DurableConversationWriter, durable_call_id
from morrow.runtime.session import Session
from morrow.runtime.tools import ToolExecutionOutcome, ToolExecutor
from morrow.services.files import WorkspaceMutationService

APPROVAL_TTL = timedelta(minutes=5)


class DurableToolExecutionCoordinator:
    """Persist approval and execution state transitions without owning chat history."""

    def __init__(
        self,
        journal: DurableToolJournalPort,
        *,
        workspace_id: str,
        id_source: IdSource,
        permissions: RunPermissionCoordinator,
        faults: FaultInjector,
        clock: Callable[[], datetime],
        artifacts: ArtifactService | None = None,
    ) -> None:
        self.journal = journal
        self.workspace_id = workspace_id
        self.id_source = id_source
        self.permissions = permissions
        self.faults = faults
        self.clock = clock
        self.artifacts = artifacts

    def execution_is_visible(self, tool_execution_id: str) -> bool:
        return self.get_execution(tool_execution_id) is not None

    def get_execution(self, tool_execution_id: str) -> DurableToolExecution | None:
        return self.journal.get_execution(self.workspace_id, tool_execution_id)

    def create_pending_approval(
        self, execution: DurableToolExecution, *, now: datetime | None = None
    ) -> DurableApproval:
        stamp = now or self.clock()
        self.permissions.assert_execution_permission(execution, now=stamp)
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
        stamp = now or self.clock()
        self.permissions.assert_execution_permission(execution, now=stamp)
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

            def deny(
                txn: DurableToolJournalPort,
            ) -> tuple[DurableToolExecution, DurableApproval]:
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

        def work(
            txn: DurableToolJournalPort,
        ) -> tuple[DurableToolExecution, DurableApproval]:
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
        stamp = now or self.clock()
        self.permissions.assert_execution_permission(execution, now=stamp)
        executing = transition_execution(
            execution,
            ToolExecutionState.EXECUTING,
            expected_row_version=execution.row_version,
            now=stamp,
        )
        return self.journal.save_execution(
            self.workspace_id, executing, expected_row_version=execution.row_version
        )

    def _close_before_handler(
        self,
        execution: DurableToolExecution,
        *,
        disposition: ToolExecutionDisposition,
        now: datetime | None = None,
    ) -> DurableToolExecution:
        current = self.get_execution(execution.tool_execution_id)
        if current is None:
            raise PermissionEvidenceError("tool execution is missing")
        if current.state in (ToolExecutionState.CLOSED, ToolExecutionState.HANDLER_COMPLETED):
            return current
        stamp = now or self.clock()
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

        def work(txn: DurableToolJournalPort) -> DurableToolExecution:
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
        return self._close_before_handler(
            execution, disposition=ToolExecutionDisposition.DENIED, now=now
        )

    def cancel_execution_before_handler(
        self, execution: DurableToolExecution, *, now: datetime | None = None
    ) -> DurableToolExecution:
        return self._close_before_handler(
            execution, disposition=ToolExecutionDisposition.CANCELLED, now=now
        )

    def record_handler_completed(
        self,
        execution: DurableToolExecution,
        result: ToolExecutionOutcome,
        *,
        now: datetime | None = None,
        disposition: ToolExecutionDisposition | None = None,
    ) -> DurableToolExecution:
        stamp = now or self.clock()
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


class ToolConversationPersistence:
    """Atomically persist chat records with their durable tool execution state."""

    def __init__(
        self,
        journal: DurableToolJournalPort,
        *,
        workspace_id: str,
        id_source: IdSource,
        mutation: WorkspaceMutationService | None,
        faults: FaultInjector,
        clock: Callable[[], datetime],
    ) -> None:
        self.journal = journal
        self.workspace_id = workspace_id
        self.id_source = id_source
        self.mutation = mutation
        self.faults = faults
        self.clock = clock

    def prepare_and_commit_assistant(
        self,
        planned: ConversationAppend,
        message: AssistantMessage,
        *,
        session: Session,
        writer: DurableConversationWriter,
        run_context: ToolRunContext,
        tool_executor: ToolExecutor | None,
        task_run_id: str,
        turn_id: str,
        agent_run_id: str,
        permission_snapshot_id: str,
        grant_id: str | None,
        isolation_label: IsolationLabel | None,
    ) -> tuple[DurableToolExecution, ...]:
        executions = prepare_cycle_executions(
            message,
            session=session,
            tool_executor=tool_executor,
            run_context=run_context,
            id_source=self.id_source,
            workspace_id=self.workspace_id,
            task_run_id=task_run_id,
            turn_id=turn_id,
            agent_run_id=agent_run_id,
            mutation=self.mutation,
            isolation=session.permission_profile.process_isolation,
            permission_snapshot_id=permission_snapshot_id,
            grant_id=grant_id,
            isolation_label=isolation_label,
        )
        self.faults.check(FaultPoint.CONVERSATION_BEFORE_COMMIT)

        def work(txn: DurableToolJournalPort) -> tuple[DurableToolExecution, ...]:
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
        _apply_committed(session, planned)
        return committed

    def commit_tool_message(
        self,
        planned: ConversationAppend,
        execution: DurableToolExecution,
        *,
        session: Session,
        writer: DurableConversationWriter,
        now: datetime | None = None,
        disposition: ToolExecutionDisposition | None = None,
    ) -> DurableToolExecution:
        stamp = now or self.clock()
        self.faults.check(FaultPoint.CONVERSATION_BEFORE_TOOL_MESSAGE_COMMIT)
        if execution.state is ToolExecutionState.CLOSED:

            def persist_only(txn: DurableToolJournalPort) -> DurableToolExecution:
                del txn
                writer.persist_with_records(planned)
                return execution

            stored = self.journal.transact(persist_only)
            self.faults.check(FaultPoint.CONVERSATION_AFTER_TOOL_MESSAGE_COMMIT)
            _apply_committed(session, planned)
            return stored
        closed = transition_execution(
            execution,
            ToolExecutionState.CLOSED,
            expected_row_version=execution.row_version,
            disposition=disposition,
            now=stamp,
        )

        def work(txn: DurableToolJournalPort) -> DurableToolExecution:
            writer.persist_with_records(planned)
            return txn.save_execution(
                self.workspace_id, closed, expected_row_version=execution.row_version
            )

        stored = self.journal.transact(work)
        self.faults.check(FaultPoint.CONVERSATION_AFTER_TOOL_MESSAGE_COMMIT)
        _apply_committed(session, planned)
        return stored


def _envelope_from_outcome(result: ToolExecutionOutcome) -> HandlerResultEnvelope:
    error_code = result.error_code.value if result.error_code is not None else None
    return HandlerResultEnvelope(
        ok=bool(result.ok),
        truncated=bool(result.truncated),
        summary={"chars": len(result.envelope or "")},
        error_code=error_code,
    )


def _apply_committed(session: Session, planned: ConversationAppend) -> None:
    session.log.apply_committed(planned)
    session.dirty = session.log.has_active_turn
