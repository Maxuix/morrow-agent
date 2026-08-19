"""Discover interrupted work, classify it, and apply user recovery decisions."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from morrow.adapters.state.journal import SqliteOperationalJournal
from morrow.core.capabilities import ProcessIsolation
from morrow.core.execution import (
    EffectClass,
    MissingCompletionPolicy,
    ToolExecutionDisposition,
    ToolExecutionState,
    ToolRecoveryDeclaration,
    UnknownToolDeclarationError,
    tool_declaration,
    transition_execution,
)
from morrow.core.models import FinishReason, utc_now
from morrow.core.ports import IdSource
from morrow.core.recovery import (
    RECOVERY_ITEM_ID_PREFIX,
    RECOVERY_REPORT_ID_PREFIX,
    FileObservation,
    RecoveryDecisionError,
    RecoveryEvidence,
    RecoveryItem,
    RecoveryReceipt,
    RecoveryReport,
    RecoveryReportStatus,
    RecoveryResolution,
    allowed_resolutions,
    apply_item_resolution,
    apply_report_resume,
    classify_execution,
    decision_digest,
    observe_config,
    observe_file,
)
from morrow.runtime.conversation import ConversationAppend, ConversationLog
from morrow.runtime.durable_log import DurableConversationWriter, durable_call_id
from morrow.runtime.tools import ToolErrorCode, tool_error_envelope

RECOVERY_LOST_RESULT = "恢复关闭：原始工具结果未能提交"


def recovery_tool_envelope(message: str = RECOVERY_LOST_RESULT) -> str:
    return tool_error_envelope(ToolErrorCode.INTERNAL, message)


def recovery_envelopes_for(
    log: ConversationLog, call_ids: tuple[str, ...] | None = None
) -> tuple[tuple[str, str], ...]:
    content = recovery_tool_envelope()
    unresolved = log.unresolved_call_ids
    durable_call_ids = {durable_call_id(call_id) for call_id in call_ids or ()}
    selected = (
        unresolved
        if call_ids is None
        else tuple(
            call_id for call_id in unresolved if durable_call_id(call_id) in durable_call_ids
        )
    )
    return tuple((call_id, content) for call_id in selected)


def _isolation_for(effect: EffectClass) -> ProcessIsolation | None:
    if effect is EffectClass.UNCONFINED_EXTERNAL_EFFECT:
        return ProcessIsolation.HOST
    if effect is EffectClass.PROCESS_EFFECT_NON_DURABLE:
        return ProcessIsolation.NATIVE_SANDBOX
    return None


def declaration_for_execution(execution) -> ToolRecoveryDeclaration:
    isolation = _isolation_for(execution.intent.effect_class)
    try:
        return tool_declaration(execution.tool_name, process_isolation=isolation)
    except UnknownToolDeclarationError:
        return ToolRecoveryDeclaration(
            tool_name=execution.tool_name,
            effect_class=EffectClass.UNCONFINED_EXTERNAL_EFFECT,
            missing_handler_completed=MissingCompletionPolicy.OUTCOME_UNKNOWN,
        )


def observations_for(execution, *, workspace_root: Path | None) -> tuple[FileObservation, ...]:
    declaration = declaration_for_execution(execution)
    if declaration.missing_handler_completed is not MissingCompletionPolicy.REQUIRES_RECONCILIATION:
        return ()
    observed: list[FileObservation] = []
    if workspace_root is not None:
        for evidence in execution.intent.file_evidence:
            observed.append(observe_file(evidence, root=workspace_root))
    config = execution.intent.config_evidence
    if config is not None:
        observed.append(
            observe_config(
                source_revision=config.source_revision,
                expected_revision=config.expected_revision,
                actual_revision=config.actual_revision,
            )
        )
    return tuple(observed)


def _force_item_close(item: RecoveryItem, preferred: RecoveryResolution) -> RecoveryItem:
    if item.resolution is not None:
        return item
    choice = preferred if preferred in item.allowed_resolutions else RecoveryResolution.ACKNOWLEDGE
    if choice not in item.allowed_resolutions:
        choice = item.allowed_resolutions[0]
    return apply_item_resolution(item, choice)


def item_from_execution(execution, *, report_id: str, item_id: str, workspace_root: Path | None):
    declaration = declaration_for_execution(execution)
    observed = observations_for(execution, workspace_root=workspace_root)
    classification = classify_execution(
        state=execution.state, declaration=declaration, observations=observed
    )
    observation = observed[0] if len(observed) == 1 else None
    summary = (
        f"{execution.tool_name} 处于 {execution.state.value}",
        f"恢复分类：{classification.value}",
    )
    if observation is not None:
        summary = (*summary, f"观察：{observation.value}")
    return RecoveryItem(
        item_id=item_id,
        report_id=report_id,
        tool_execution_id=execution.tool_execution_id,
        tool_name=execution.tool_name,
        classification=classification,
        allowed_resolutions=allowed_resolutions(classification, declaration),
        evidence=RecoveryEvidence(
            execution_state=execution.state,
            effect_class=execution.intent.effect_class,
            observation=observation,
            relative_paths=tuple(item.relative_path for item in execution.intent.file_evidence),
            summary=summary,
        ),
        blocking=execution.state is not ToolExecutionState.CLOSED,
    )


class RecoveryService:
    def __init__(
        self,
        journal: SqliteOperationalJournal,
        *,
        workspace_id: str,
        id_source: IdSource,
        workspace_root: Path | None = None,
    ) -> None:
        self.journal = journal
        self.workspace_id = workspace_id
        self.id_source = id_source
        self.workspace_root = workspace_root

    def discover(self, session_id: str, log: ConversationLog) -> RecoveryReport | None:
        existing = self.journal.get_open_report(self.workspace_id, session_id)
        if existing is not None:
            return existing
        executions = [
            item
            for item in self.journal.list_session_executions(self.workspace_id, session_id)
            if item.state is not ToolExecutionState.CLOSED
        ]
        if not executions:
            return None
        report_id = self.id_source.new_id(RECOVERY_REPORT_ID_PREFIX)
        items = tuple(
            item_from_execution(
                execution,
                report_id=report_id,
                item_id=self.id_source.new_id(RECOVERY_ITEM_ID_PREFIX),
                workspace_root=self.workspace_root,
            )
            for execution in executions
        )
        report = RecoveryReport(
            report_id=report_id,
            workspace_id=self.workspace_id,
            session_id=session_id,
            turn_id=executions[0].turn_id,
            agent_run_id=executions[0].agent_run_id,
            items=items,
        )
        return self.journal.put_report(self.workspace_id, report)

    def decide(
        self,
        report: RecoveryReport,
        *,
        command_id: str,
        resolution: RecoveryResolution,
        item_id: str | None,
        log: ConversationLog,
        now: datetime | None = None,
    ) -> tuple[RecoveryReport, RecoveryReceipt, ConversationAppend | None]:
        digest = decision_digest(report_id=report.report_id, item_id=item_id, resolution=resolution)
        existing = self.journal.get_recovery_receipt(
            self.workspace_id, report.session_id, command_id
        )
        if existing is not None:
            kind = "replay" if existing.request_digest == digest else "conflict"
            return report, existing.model_copy(update={"kind": kind}), None
        stamp = now or utc_now()
        planned = None
        if resolution is RecoveryResolution.RESUME:
            updated = apply_report_resume(report, now=stamp)
        elif item_id is None and resolution is RecoveryResolution.ABORT:
            items = tuple(
                _force_item_close(item, RecoveryResolution.ABORT) for item in report.items
            )
            updated = report.model_copy(
                update={
                    "items": items,
                    "status": RecoveryReportStatus.RESOLVED,
                    "resolved_at": stamp,
                }
            )
            planned = log.plan_recovery_close(recovery_envelopes_for(log), FinishReason.CANCELLED)
        elif item_id is None and resolution is RecoveryResolution.QUARANTINE:
            updated = report.model_copy(update={"status": RecoveryReportStatus.QUARANTINED})
        else:
            if item_id is None:
                raise RecoveryDecisionError("item resolution requires an item_id")
            target = next((item for item in report.items if item.item_id == item_id), None)
            if target is None:
                raise RecoveryDecisionError("recovery item is missing")
            replaced = apply_item_resolution(target, resolution)
            items = tuple(replaced if item.item_id == item_id else item for item in report.items)
            if resolution is RecoveryResolution.QUARANTINE:
                status = RecoveryReportStatus.QUARANTINED
            elif resolution is RecoveryResolution.ABORT:
                status = (
                    RecoveryReportStatus.RESOLVED
                    if not any(item.blocking and item.resolution is None for item in items)
                    else report.status
                )
            else:
                status = report.status
            updates = {"items": items, "status": status}
            if status is RecoveryReportStatus.RESOLVED:
                updates["resolved_at"] = stamp
            updated = report.model_copy(update=updates)
            if resolution in {RecoveryResolution.ACKNOWLEDGE, RecoveryResolution.ABORT}:
                execution = self.journal.get_execution(self.workspace_id, target.tool_execution_id)
                call_ids = (execution.call_id,) if execution is not None else ()
                envelopes = recovery_envelopes_for(log, call_ids)
                reason = (
                    FinishReason.CANCELLED
                    if resolution is RecoveryResolution.ABORT
                    and status is RecoveryReportStatus.RESOLVED
                    else None
                )
                if envelopes or reason is not None:
                    planned = log.plan_recovery_close(envelopes, reason)
        receipt = RecoveryReceipt(
            session_id=report.session_id,
            command_id=command_id,
            request_digest=digest,
            report_id=report.report_id,
            item_id=item_id,
            resolution=resolution,
        )
        return updated, receipt, planned

    def commit_decision(
        self,
        report: RecoveryReport,
        receipt: RecoveryReceipt,
        *,
        planned: ConversationAppend | None,
        log: ConversationLog,
        writer: DurableConversationWriter | None,
        close_all: bool,
        apply_log_projection: bool = True,
        finalize: Callable[[SqliteOperationalJournal, RecoveryReport], None] | None = None,
    ) -> RecoveryReport:
        def work(txn: SqliteOperationalJournal) -> RecoveryReport:
            saved = txn.save_report(self.workspace_id, report)
            txn.put_recovery_receipt(self.workspace_id, receipt)
            if receipt.resolution in {
                RecoveryResolution.ACKNOWLEDGE,
                RecoveryResolution.ABORT,
            }:
                disposition = (
                    ToolExecutionDisposition.CANCELLED
                    if receipt.resolution is RecoveryResolution.ABORT
                    else ToolExecutionDisposition.INTERRUPTED
                )
                for item in report.items:
                    if not close_all and item.item_id != receipt.item_id:
                        continue
                    execution = txn.get_execution(self.workspace_id, item.tool_execution_id)
                    if execution is None or execution.state is ToolExecutionState.CLOSED:
                        continue
                    _persist_closed_execution(txn, self.workspace_id, execution, disposition)
            if planned is not None:
                if writer is None:
                    raise RecoveryDecisionError("recovery conversation writer is missing")
                writer.persist_with_records(planned)
            if finalize is not None:
                finalize(txn, saved)
            return saved

        saved = self.journal.transact(work)
        if planned is not None and apply_log_projection:
            log.apply_committed(planned)
        return saved


def _persist_closed_execution(
    journal: SqliteOperationalJournal,
    workspace_id: str,
    execution,
    disposition: ToolExecutionDisposition,
) -> None:
    current = execution
    if current.state is ToolExecutionState.EXECUTING:
        mid = transition_execution(
            current,
            ToolExecutionState.HANDLER_COMPLETED,
            expected_row_version=current.row_version,
            disposition=disposition,
        )
        current = journal.save_execution(
            workspace_id, mid, expected_row_version=execution.row_version
        )
    if current.state is not ToolExecutionState.CLOSED:
        closed = transition_execution(
            current,
            ToolExecutionState.CLOSED,
            expected_row_version=current.row_version,
            disposition=disposition,
        )
        journal.save_execution(workspace_id, closed, expected_row_version=current.row_version)
