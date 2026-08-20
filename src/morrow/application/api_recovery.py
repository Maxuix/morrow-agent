"""Recovery commands behind the operational application facade."""

from __future__ import annotations

from morrow.adapters.state.journal import SqliteOperationalJournal
from morrow.core.application import (
    ApplicationCommandDisposition,
    ApplicationCommandReceipt,
    ApplicationCommandResult,
    ApplicationError,
    ApplicationErrorCode,
)
from morrow.core.domain import (
    AGENT_RUN_ID_PREFIX,
    DurableAgentRun,
    DurableSession,
    SessionHealth,
    TaskOutcomeTrigger,
    TaskRunStatus,
    TurnSubmitDisposition,
)
from morrow.core.recovery import RecoveryReport, RecoveryReportStatus, RecoveryResolution
from morrow.core.store import StorageError, StorageErrorCode


class RecoveryApplicationService:
    """Own Recovery command transactions without expanding the public facade."""

    def __init__(self, application) -> None:
        self.application = application

    def resolve(
        self,
        report: RecoveryReport,
        *,
        command_id: str | None = None,
        resolution: RecoveryResolution,
        item_id: str | None = None,
        log=None,
        writer=None,
        close_all: bool = False,
    ) -> ApplicationCommandResult[RecoveryReport]:
        api = self.application
        if api.recovery is None or log is None:
            raise ApplicationError(
                ApplicationErrorCode.UNAVAILABLE, "recovery service is unavailable"
            )
        if report.workspace_id != api.workspace_id:
            raise ApplicationError(
                ApplicationErrorCode.CROSS_WORKSPACE, "recovery report is outside the workspace"
            )
        payload = {
            "report_id": report.report_id,
            "resolution": resolution.value,
            "item_id": item_id,
        }
        command_id, digest, replay = api._prepare("recovery_resolve", payload, command_id)
        if replay is not None:
            value = api._query(
                lambda: api.journal.get_report(api.workspace_id, replay.result_id or "")
            )
            if value is None:
                raise ApplicationError(
                    ApplicationErrorCode.NEEDS_RECOVERY, "recovery result is missing"
                )
            return ApplicationCommandResult(value, replay)
        try:
            resumed_agent_run_id: list[str] = []
            updated, recovery_receipt, planned = api.recovery.decide(
                report,
                command_id=command_id,
                resolution=resolution,
                item_id=item_id,
                log=log,
            )
            if recovery_receipt.kind == "conflict":
                raise ApplicationError(
                    ApplicationErrorCode.CONFLICT,
                    "recovery command ID was reused with a different request",
                )
            if recovery_receipt.kind == "replay":
                value = api._query(
                    lambda: api.journal.get_report(api.workspace_id, recovery_receipt.report_id)
                )
                if value is None:
                    raise ApplicationError(
                        ApplicationErrorCode.NEEDS_RECOVERY, "recovery result is missing"
                    )
                receipt = ApplicationCommandReceipt(
                    command_id=command_id,
                    workspace_id=api.workspace_id,
                    session_id=value.session_id,
                    operation="recovery_resolve",
                    request_digest=digest,
                    disposition=ApplicationCommandDisposition.REPLAY,
                    result_kind="recovery",
                    result_id=value.report_id,
                )
                return ApplicationCommandResult(value, receipt)

            close_all = close_all or (resolution is RecoveryResolution.ABORT and item_id is None)

            def work(txn: SqliteOperationalJournal):
                existing = api._replay_in_txn(txn, command_id, digest)
                if existing is not None:
                    value = txn.get_report(api.workspace_id, existing.result_id or "")
                    if value is None:
                        raise ApplicationError(
                            ApplicationErrorCode.NEEDS_RECOVERY,
                            "recovery result is missing",
                        )
                    return ApplicationCommandResult(value, existing)
                value = api.recovery.commit_decision(
                    updated,
                    recovery_receipt,
                    planned=planned,
                    log=log,
                    writer=writer,
                    close_all=close_all,
                    apply_log_projection=False,
                    finalize=lambda finalize_txn, saved: self._apply_lifecycle_in_txn(
                        finalize_txn,
                        report=report,
                        saved=saved,
                        resolution=resolution,
                        resumed_agent_run_id=resumed_agent_run_id,
                    ),
                )
                event = api._event(
                    txn,
                    event_type="recovery.resolved",
                    aggregate_kind="recovery",
                    aggregate_id=value.report_id,
                    payload={"status": value.status.value, "resolution": resolution.value},
                )
                receipt = api._receipt(
                    txn,
                    command_id=command_id,
                    operation="recovery_resolve",
                    digest=digest,
                    session_id=value.session_id,
                    result_kind="recovery",
                    result_id=value.report_id,
                    event_cursor=event.cursor,
                )
                return ApplicationCommandResult(value, receipt)

            try:
                result = api._translate(lambda: api.journal.transact(work))
                if planned is not None:
                    log.apply_committed(planned)
                self._sync_persistence(result.value, resumed_agent_run_id)
                return result
            except ApplicationError:
                api._restore_log_projection(log, report.session_id)
                raise
        except ApplicationError:
            raise
        except Exception as exc:
            api._restore_log_projection(log, report.session_id)
            raise api._translate_exception(exc) from exc

    def _apply_lifecycle_in_txn(
        self,
        txn: SqliteOperationalJournal,
        *,
        report: RecoveryReport,
        saved: RecoveryReport,
        resolution: RecoveryResolution,
        resumed_agent_run_id: list[str],
    ) -> None:
        """Keep Session health, turn receipts, tasks, and resume runs atomic."""

        api = self.application
        session = txn.get_session(api.workspace_id, report.session_id)
        if session is None:
            raise StorageError(StorageErrorCode.NOT_FOUND, "operational session is missing")

        health = SessionHealth.NEEDS_RECOVERY
        current_task_run_id = session.current_task_run_id
        if saved.status is RecoveryReportStatus.QUARANTINED:
            health = SessionHealth.QUARANTINED
        elif saved.status is RecoveryReportStatus.RESOLVED:
            health = SessionHealth.OK
            if resolution is RecoveryResolution.RESUME and saved.agent_run_id is not None:
                previous = txn.get_agent_run(api.workspace_id, saved.agent_run_id)
                if previous is None:
                    raise StorageError(StorageErrorCode.NOT_FOUND, "recovery AgentRun is missing")
                new_id = api.id_source.new_id(AGENT_RUN_ID_PREFIX)
                runtime_instance_id = getattr(api.persistence, "runtime_instance_id", None)
                snapshot = previous.snapshot
                if runtime_instance_id is not None:
                    snapshot = snapshot.model_copy(
                        update={"runtime_instance_id": runtime_instance_id}
                    )
                txn.create_agent_run(
                    api.workspace_id,
                    DurableAgentRun(
                        agent_run_id=new_id,
                        turn_id=previous.turn_id,
                        session_id=previous.session_id,
                        resume_of_agent_run_id=previous.agent_run_id,
                        snapshot=snapshot,
                    ),
                )
                resumed_agent_run_id.append(new_id)
            elif resolution is RecoveryResolution.ABORT:
                current_task_run_id = self._abort_task_in_txn(txn, session, turn_id=report.turn_id)
                self._close_receipt_in_txn(txn, report)

        txn.save_session(
            api.workspace_id,
            session.model_copy(
                update={"health": health, "current_task_run_id": current_task_run_id}
            ),
        )

    def _sync_persistence(self, report: RecoveryReport, resumed_agent_run_id: list[str]) -> None:
        api = self.application
        persistence = api.persistence
        session = getattr(persistence, "_session", None) if persistence is not None else None
        if persistence is None or session is None or session.session_id != report.session_id:
            return
        row = api.journal.get_session(api.workspace_id, report.session_id)
        if row is None:
            return
        session.health = row.health
        persistence.current_task_run_id = row.current_task_run_id
        if resumed_agent_run_id:
            persistence.current_agent_run_id = resumed_agent_run_id[0]
            persistence.current_permission_snapshot_id = None
        persistence.open_report = None if report.status is RecoveryReportStatus.RESOLVED else report

    def _abort_task_in_txn(
        self, txn: SqliteOperationalJournal, session: DurableSession, *, turn_id: str | None
    ) -> str | None:
        api = self.application
        task_id = session.current_task_run_id
        if task_id is None:
            return None
        task = txn.get_task_run(api.workspace_id, task_id)
        if task is None:
            raise StorageError(StorageErrorCode.NOT_FOUND, "recovery TaskRun is missing")
        if task.status in {TaskRunStatus.OPEN, TaskRunStatus.READY_FOR_ACCEPTANCE}:
            task = api.tasks._transition_in_txn(
                txn,
                task,
                TaskRunStatus.CANCELLED,
                reason="recovery_abort",
                turn_id=turn_id,
            )
            api.tasks._outcome_in_txn(
                txn,
                task,
                trigger=TaskOutcomeTrigger.TERMINAL_CLOSE,
                summary="TaskRun cancelled during recovery abort.",
            )
            return None
        return task_id if not task.status.is_terminal else None

    def _close_receipt_in_txn(self, txn: SqliteOperationalJournal, report: RecoveryReport) -> None:
        api = self.application
        if report.turn_id is None:
            return
        turn = txn.get_turn(api.workspace_id, report.turn_id)
        if turn is None:
            return
        receipt = txn.get_receipt(api.workspace_id, report.session_id, turn.client_message_id)
        if receipt is None or receipt.disposition is TurnSubmitDisposition.ACCEPTED_CLOSED:
            return
        txn.update_receipt(
            api.workspace_id,
            receipt.model_copy(update={"disposition": TurnSubmitDisposition.ACCEPTED_CLOSED}),
        )
