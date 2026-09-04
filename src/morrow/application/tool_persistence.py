"""Focused persistence collaborators for one durable tool cycle."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime, timedelta

from morrow.application.artifacts import ArtifactService
from morrow.application.prepared import prepare_cycle_executions
from morrow.application.turn_permissions import RunPermissionCoordinator
from morrow.core.artifacts import ArtifactError
from morrow.core.capabilities import ChangeToolFact, ToolRunContext
from morrow.core.domain import ArtifactReference
from morrow.core.execution import (
    APPROVAL_ID_PREFIX,
    ApprovalDecisionError,
    ApprovalResolution,
    DurableApproval,
    DurableToolExecution,
    DurableToolFacts,
    HandlerResultEnvelope,
    ToolExecutionDisposition,
    ToolExecutionState,
    ValidationDiagnostic,
    approval_preview_digest,
    consume_approval,
    intent_hash,
    resolve_approval,
    session_granted_scope,
    session_scope_allowed,
    transition_execution,
)
from morrow.core.faults import FaultInjector, FaultPoint
from morrow.core.journal import DurableToolJournalPort
from morrow.core.mcp import McpResultArtifactLink
from morrow.core.models import AssistantMessage
from morrow.core.permissions import IsolationLabel, PermissionEvidenceError
from morrow.core.ports import IdSource
from morrow.core.store import StorageError
from morrow.runtime.conversation import ConversationAppend
from morrow.runtime.durable_log import DurableConversationWriter, durable_call_id
from morrow.runtime.session import Session
from morrow.runtime.tools import ToolErrorCode, ToolExecutionOutcome, ToolExecutor
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
        change_capture=None,
    ) -> None:
        self.journal = journal
        self.workspace_id = workspace_id
        self.id_source = id_source
        self.permissions = permissions
        self.faults = faults
        self.clock = clock
        self.artifacts = artifacts
        self.change_capture = change_capture

    def execution_is_visible(self, tool_execution_id: str) -> bool:
        return self.get_execution(tool_execution_id) is not None

    def get_execution(self, tool_execution_id: str) -> DurableToolExecution | None:
        return self.journal.get_execution(self.workspace_id, tool_execution_id)

    def get_approval(self, approval_id: str) -> DurableApproval | None:
        return self.journal.get_approval(self.workspace_id, approval_id)

    def create_pending_approval(
        self, execution: DurableToolExecution, *, now: datetime | None = None
    ) -> DurableApproval:
        stamp = now or self.clock()
        self.permissions.assert_execution_permission(execution, now=stamp)
        preview = execution.intent.preview
        requested_scope = f"{execution.intent.effect_class.value}:{execution.tool_name}"
        approval = DurableApproval(
            approval_id=self.id_source.new_id(APPROVAL_ID_PREFIX),
            tool_execution_id=execution.tool_execution_id,
            intent_hash=intent_hash(execution.intent),
            tool_schema_digest=execution.intent.schema_digest,
            permission_context_digest=execution.intent.permission_context_digest,
            requested_scope=requested_scope,
            preview=preview,
            preview_digest=approval_preview_digest(preview),
            permission_snapshot_id=execution.permission_snapshot_id,
            grant_id=execution.grant_id,
            isolation=execution.isolation,
            created_at=stamp,
            expires_at=stamp + APPROVAL_TTL,
        )
        # A session-scoped grant recorded by an earlier user decision resolves
        # the new approval durably at creation; every execution keeps its own
        # auditable approval row. High-risk operations never carry a session
        # scope, so they cannot be auto-approved here.
        if session_scope_allowed(execution.intent.effect_class, execution.isolation):
            precedent = self.journal.find_session_scope_approval(
                self.workspace_id,
                session_id=execution.session_id,
                granted_scope=session_granted_scope(requested_scope),
            )
            if precedent is not None:
                approval = approval.model_copy(
                    update={
                        "resolution": ApprovalResolution.APPROVED,
                        "granted_scope": precedent.granted_scope,
                        "resolved_at": stamp,
                    }
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
        granted_scope: str | None = None,
    ) -> tuple[DurableToolExecution, DurableApproval, bool]:
        stamp = now or self.clock()
        self.permissions.assert_execution_permission(execution, now=stamp)
        if approval.resolution is ApprovalResolution.PENDING:
            resolved = resolve_approval(
                approval,
                approved=approved,
                expected_row_version=approval.row_version,
                now=stamp,
                command_id=command_id,
                granted_scope=granted_scope,
            )
        else:
            # Pre-resolved at creation by a session-scope precedent: the durable
            # decision must agree with the caller; only consumption remains.
            target = ApprovalResolution.APPROVED if approved else ApprovalResolution.DENIED
            if approval.resolution is not target:
                raise ApprovalDecisionError("approval decision conflicts with durable state")
            if approval.resolution is ApprovalResolution.APPROVED and stamp >= approval.expires_at:
                raise ApprovalDecisionError("approval expired")
            resolved = approval
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
                if resolved is approval:
                    saved_approval = approval
                else:
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
            if resolved is approval:
                saved_resolved = approval
            else:
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
        artifact_refs: list[ArtifactReference] = []
        for reference in (*result.artifact_refs, *result.mcp_result_artifact_refs):
            if reference not in artifact_refs:
                artifact_refs.append(reference)
        if self.artifacts is not None and execution.tool_name in {"run_command", "bash"}:
            try:
                artifact = self.artifacts.publish_command_output(
                    result.artifact_content
                    if result.artifact_content is not None
                    else result.envelope,
                    session_id=execution.session_id,
                    task_run_id=execution.task_run_id,
                    tool_execution_id=execution.tool_execution_id,
                )
                reference = ArtifactReference(artifact_id=artifact.artifact_id, role="tool_output")
                if reference not in artifact_refs:
                    artifact_refs.append(reference)
            except (ArtifactError, StorageError):
                pass
        if self.change_capture is not None:
            for reference in self.change_capture.capture(execution, result, artifact_refs):
                if reference not in artifact_refs:
                    artifact_refs.append(reference)
        durable_facts = None
        if execution.intent.file_evidence and any(
            isinstance(fact, ChangeToolFact) for fact in result.facts
        ):
            durable_facts = DurableToolFacts(files=execution.intent.file_evidence)
        completed = transition_execution(
            execution,
            ToolExecutionState.HANDLER_COMPLETED,
            expected_row_version=execution.row_version,
            disposition=final_disposition,
            now=stamp,
            result_envelope=_envelope_from_outcome(result),
            error_code=result.error_code.value if result.error_code is not None else None,
            facts=durable_facts,
        )
        if artifact_refs:
            completed = completed.model_copy(update={"artifact_refs": tuple(artifact_refs)})
        links = tuple(
            McpResultArtifactLink(
                link_id=self.id_source.new_id("mcp_link"),
                workspace_id=self.workspace_id,
                tool_execution_id=execution.tool_execution_id,
                artifact_id=reference.artifact_id,
                role=reference.role,
            )
            for reference in result.mcp_result_artifact_refs
        )

        def work(txn: DurableToolJournalPort) -> DurableToolExecution:
            stored_execution = txn.save_execution(
                self.workspace_id, completed, expected_row_version=execution.row_version
            )
            for link in links:
                txn.put_mcp_result_artifact_link(link)
            return stored_execution

        stored = self.journal.transact(work)
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
    diagnostics: list[ValidationDiagnostic] = []
    if result.error_code is ToolErrorCode.INVALID_ARGUMENTS:
        try:
            payload = json.loads(result.envelope)
            error = payload.get("error") if isinstance(payload, dict) else None
            details = (
                error.get("details", [])
                if payload.get("ok") is False
                and isinstance(error, dict)
                and error.get("code") == ToolErrorCode.INVALID_ARGUMENTS.value
                else []
            )
        except (TypeError, ValueError, json.JSONDecodeError, AttributeError):
            details = []
        if isinstance(details, list):
            for item in details:
                if not isinstance(item, dict):
                    continue
                try:
                    diagnostic = ValidationDiagnostic.model_validate(
                        {"path": item.get("path"), "type": item.get("type")}, strict=True
                    )
                except ValueError:
                    continue
                if diagnostic not in diagnostics:
                    diagnostics.append(diagnostic)
                if len(diagnostics) >= 8:
                    break
    return HandlerResultEnvelope(
        ok=bool(result.ok),
        truncated=bool(result.truncated),
        summary={"chars": len(result.envelope or "")},
        error_code=error_code,
        validation_diagnostics=tuple(diagnostics),
    )


def _apply_committed(session: Session, planned: ConversationAppend) -> None:
    session.log.apply_committed(planned)
    session.dirty = session.log.has_active_turn
