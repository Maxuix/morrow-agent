"""The single typed Command/Query boundary for completed Stage 4 domains."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from morrow.adapters.state.journal import SqliteOperationalJournal
from morrow.application.artifacts import ArtifactService
from morrow.application.checkpoints import (
    ContextCheckpointError,
    ContextCheckpointService,
    SessionForkService,
)
from morrow.application.cleanup import ArtifactCleanupService
from morrow.application.grants import (
    CapabilityGrantError,
    CapabilityGrantService,
    validate_capability_subset,
)
from morrow.application.recovery import RecoveryService
from morrow.application.tasks import (
    TaskCommandConflict,
    TaskCommandError,
    TaskService,
)
from morrow.application.turns import TurnSubmitResult
from morrow.core.application import (
    ApplicationCommandDisposition,
    ApplicationCommandReceipt,
    ApplicationCommandResult,
    ApplicationError,
    ApplicationErrorCode,
    ApplicationEvent,
    QueryPage,
)
from morrow.core.artifacts import ArtifactError, ArtifactMetadata
from morrow.core.capabilities import PermissionPreset, PermissionProfile
from morrow.core.domain import (
    AGENT_RUN_ID_PREFIX,
    COMMAND_ID_PREFIX,
    WORKSPACE_ID_PREFIX,
    DurableAgentRun,
    DurableSession,
    DurableTaskOutcome,
    DurableTaskRun,
    SessionHealth,
    SessionLifecycle,
    TaskOutcomeTrigger,
    TaskRunStatus,
    TurnSubmitDisposition,
    canonical_json_bytes,
    sha256_digest,
    validate_prefixed_id,
)
from morrow.core.execution import (
    DurableApproval,
    DurableToolExecution,
    ToolExecutionDisposition,
    ToolExecutionState,
    transition_execution,
)
from morrow.core.permissions import (
    CAPABILITY_GRANT_ID_PREFIX,
    PERMISSION_POLICY_VERSION,
    CapabilityGrant,
    CapabilityName,
    PermissionSnapshot,
)
from morrow.core.ports import IdSource
from morrow.core.recovery import (
    RecoveryDecisionError,
    RecoveryReport,
    RecoveryReportStatus,
    RecoveryResolution,
)
from morrow.core.store import StorageError, StorageErrorCode
from morrow.runtime.durable_log import restore_conversation_log
from morrow.runtime.ids import RandomIdSource


def request_digest(operation: str, payload: dict[str, object]) -> str:
    return sha256_digest(canonical_json_bytes({"operation": operation, **payload}))


_FULL_ACCESS_MANUAL_PROFILE_DIGEST = sha256_digest(
    canonical_json_bytes(
        PermissionProfile.from_preset(PermissionPreset.FULL_ACCESS_MANUAL).model_dump(mode="json")
    )
)


def _now(clock: Callable[[], datetime] | None) -> datetime:
    value = clock() if clock is not None else datetime.now(UTC)
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class OperationalApplicationService:
    """Compose existing domain services without introducing a second lifecycle."""

    def __init__(
        self,
        *,
        journal: SqliteOperationalJournal,
        workspace_id: str,
        id_source: IdSource | None = None,
        tasks: TaskService | None = None,
        artifacts: ArtifactService | None = None,
        recovery: RecoveryService | None = None,
        checkpoints: ContextCheckpointService | None = None,
        forks: SessionForkService | None = None,
        persistence=None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.journal = journal
        try:
            self.workspace_id = validate_prefixed_id(workspace_id, WORKSPACE_ID_PREFIX)
        except ValueError as exc:
            raise ApplicationError(ApplicationErrorCode.INVALID, "workspace ID is invalid") from exc
        self.id_source = id_source or RandomIdSource()
        self.clock = clock
        self.tasks = tasks or TaskService(
            journal=journal, workspace_id=workspace_id, id_source=self.id_source
        )
        self.artifacts = artifacts
        self.recovery = recovery
        self.checkpoints = checkpoints
        self.forks = forks
        self.persistence = persistence

    # Queries -----------------------------------------------------------------

    def get_session(self, session_id: str) -> DurableSession | None:
        return self._query(lambda: self.journal.get_session(self.workspace_id, session_id))

    def list_sessions(
        self, *, cursor: str | None = None, limit: int = 50
    ) -> QueryPage[DurableSession]:
        offset = self._offset(cursor, limit)
        items = self._query(lambda: self.journal.list_sessions(self.workspace_id))
        page = items[offset : offset + limit]
        return QueryPage(page, str(offset + len(page)) if offset + len(page) < len(items) else None)

    def get_task(self, task_run_id: str) -> DurableTaskRun | None:
        return self._query(lambda: self.journal.get_task_run(self.workspace_id, task_run_id))

    def list_tasks(
        self, session_id: str, *, cursor: str | None = None, limit: int = 50
    ) -> QueryPage[DurableTaskRun]:
        self._require_session(session_id)
        offset = self._offset(cursor, limit)
        items = self._query(lambda: self.tasks.list(session_id))
        page = items[offset : offset + limit]
        return QueryPage(page, str(offset + len(page)) if offset + len(page) < len(items) else None)

    def list_outcomes(self, task_run_id: str) -> tuple[DurableTaskOutcome, ...]:
        task = self._require_task(task_run_id)
        return self._query(
            lambda: self.journal.list_task_outcomes(self.workspace_id, task.task_run_id)
        )

    def get_artifact(self, artifact_id: str) -> ArtifactMetadata | None:
        if self.artifacts is None:
            return None
        return self._query(lambda: self.artifacts.get(artifact_id))

    def get_grant(self, grant_id: str) -> CapabilityGrant | None:
        return self._query(lambda: self.journal.get_capability_grant(self.workspace_id, grant_id))

    def list_grants(
        self,
        *,
        agent_run_id: str | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> QueryPage[CapabilityGrant]:
        offset = self._offset(cursor, limit)
        grants = self._query(
            lambda: self.journal.list_capability_grants(
                self.workspace_id, agent_run_id=agent_run_id
            )
        )
        page = grants[offset : offset + limit]
        return QueryPage(
            page, str(offset + len(page)) if offset + len(page) < len(grants) else None
        )

    def get_permission_snapshot(self, permission_snapshot_id: str) -> PermissionSnapshot | None:
        return self._query(
            lambda: self.journal.get_permission_snapshot(self.workspace_id, permission_snapshot_id)
        )

    def list_permission_snapshots(
        self,
        *,
        agent_run_id: str | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> QueryPage[PermissionSnapshot]:
        offset = self._offset(cursor, limit)
        snapshots = self._query(
            lambda: self.journal.list_permission_snapshots(
                self.workspace_id, agent_run_id=agent_run_id
            )
        )
        page = snapshots[offset : offset + limit]
        return QueryPage(
            page, str(offset + len(page)) if offset + len(page) < len(snapshots) else None
        )

    def list_artifacts(
        self,
        *,
        session_id: str | None = None,
        task_run_id: str | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> QueryPage[ArtifactMetadata]:
        if session_id is not None:
            self._require_session(session_id)
        if task_run_id is not None:
            task = self._require_task(task_run_id)
            if session_id is not None and task.session_id != session_id:
                raise ApplicationError(
                    ApplicationErrorCode.CROSS_WORKSPACE,
                    "TaskRun does not belong to the requested Session",
                )
        if self.artifacts is None:
            return QueryPage(())
        offset = self._offset(cursor, limit)
        items = self._query(
            lambda: self.journal.list_artifacts(
                self.workspace_id, session_id=session_id, task_run_id=task_run_id
            )
        )
        page = items[offset : offset + limit]
        return QueryPage(page, str(offset + len(page)) if offset + len(page) < len(items) else None)

    def get_recovery(self, report_id: str) -> RecoveryReport | None:
        if self.recovery is None:
            return None
        return self._query(lambda: self.journal.get_report(self.workspace_id, report_id))

    def list_recovery(self, session_id: str) -> tuple[RecoveryReport, ...]:
        self._require_session(session_id)
        return self._query(
            lambda: self.journal.list_recovery_reports(self.workspace_id, session_id)
        )

    def list_checkpoints(self, session_id: str):
        self._require_session(session_id)
        return self._query(
            lambda: self.journal.list_context_checkpoints(self.workspace_id, session_id)
        )

    def list_events(
        self, *, after_cursor: int = 0, limit: int = 100
    ) -> QueryPage[ApplicationEvent]:
        if not isinstance(after_cursor, int) or isinstance(after_cursor, bool) or after_cursor < 0:
            raise ApplicationError(
                ApplicationErrorCode.INVALID, "application event cursor is invalid"
            )
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
            raise ApplicationError(
                ApplicationErrorCode.INVALID, "application event page size is invalid"
            )
        events = self._query(
            lambda: self.journal.list_application_events(
                self.workspace_id, after_cursor=after_cursor, limit=limit
            )
        )
        next_cursor = events[-1].cursor if len(events) == limit else None
        return QueryPage(events, str(next_cursor) if next_cursor is not None else None)

    def cleanup_orphans(self, *, dry_run: bool = True):
        if self.artifacts is None:
            raise ApplicationError(
                ApplicationErrorCode.UNAVAILABLE, "Artifact service is unavailable"
            )
        return ArtifactCleanupService(self.artifacts).run(dry_run=dry_run)

    def create_checkpoint(
        self,
        session_id: str,
        *,
        command_id: str | None = None,
        task_run_id: str | None = None,
        source_agent_run_id: str | None = None,
        retain_recent_turns: int = 1,
        checkpoint_id: str | None = None,
    ):
        if self.checkpoints is None:
            raise ApplicationError(
                ApplicationErrorCode.UNAVAILABLE, "Context checkpoint service is unavailable"
            )
        operation = "checkpoint_create"
        payload = {
            "session_id": session_id,
            "task_run_id": task_run_id,
            "source_agent_run_id": source_agent_run_id,
            "retain_recent_turns": retain_recent_turns,
            "checkpoint_id": checkpoint_id,
        }
        command_id, digest, replay = self._prepare(operation, payload, command_id)
        if replay is not None:
            value = self._query(
                lambda: self.journal.get_context_checkpoint(
                    self.workspace_id, replay.result_id or ""
                )
            )
            if value is None:
                raise ApplicationError(
                    ApplicationErrorCode.NEEDS_RECOVERY, "checkpoint result is missing"
                )
            return ApplicationCommandResult(value, replay)

        def work(txn):
            existing = self._replay_in_txn(txn, command_id, digest)
            if existing is not None:
                value = txn.get_context_checkpoint(self.workspace_id, existing.result_id or "")
                if value is None:
                    raise ApplicationError(
                        ApplicationErrorCode.NEEDS_RECOVERY, "checkpoint result is missing"
                    )
                return ApplicationCommandResult(value, existing)
            value = self.checkpoints.create(
                session_id,
                task_run_id=task_run_id,
                source_agent_run_id=source_agent_run_id,
                retain_recent_turns=retain_recent_turns,
                checkpoint_id=checkpoint_id,
            )
            event = self._event(
                txn,
                event_type="checkpoint.created",
                aggregate_kind="checkpoint",
                aggregate_id=value.checkpoint_id,
                payload={
                    "session_id": value.session_id,
                    "source_end_position": value.source_end_position,
                },
            )
            receipt = self._receipt(
                txn,
                command_id=command_id,
                operation=operation,
                digest=digest,
                session_id=value.session_id,
                result_kind="checkpoint",
                result_id=value.checkpoint_id,
                event_cursor=event.cursor,
            )
            return ApplicationCommandResult(value, receipt)

        return self._translate(lambda: self.journal.transact(work))

    def submit_turn(
        self,
        session,
        *,
        user_input: str,
        client_message_id: str,
        turn_id: str,
        agent_run_id: str = "",
        tools: tuple = (),
        command_id: str | None = None,
        persistence=None,
    ):
        persistence = persistence or self.persistence
        if persistence is None:
            raise ApplicationError(
                ApplicationErrorCode.UNAVAILABLE, "Session persistence is unavailable"
            )
        operation = "turn_submit"
        payload = {
            "client_message_id": client_message_id,
            "content_digest": sha256_digest(user_input),
        }
        command_id, digest, replay = self._prepare(operation, payload, command_id)
        if replay is not None:
            return ApplicationCommandResult(
                TurnSubmitResult("accepted", replay.result_id),
                replay,
            )

        def work(txn):
            existing = self._replay_in_txn(txn, command_id, digest)
            if existing is not None:
                return ApplicationCommandResult(
                    TurnSubmitResult("accepted", existing.result_id),
                    existing,
                )
            result = persistence.submit_user(
                session,
                user_input,
                client_message_id,
                turn_id=turn_id,
                agent_run_id=agent_run_id,
                tools=tools,
            )
            if result.kind == "conflict":
                raise ApplicationError(
                    ApplicationErrorCode.CONFLICT,
                    "client message conflicts with an existing request",
                )
            if result.kind == "recovery":
                raise ApplicationError(
                    ApplicationErrorCode.NEEDS_RECOVERY,
                    "Session requires recovery before this Turn",
                )
            if result.kind != "accepted":
                return ApplicationCommandResult(result, None)
            event = self._event(
                txn,
                event_type="turn.submitted",
                aggregate_kind="turn",
                aggregate_id=result.turn_id or turn_id,
                payload={"session_id": session.session_id, "client_message_id": client_message_id},
            )
            receipt = self._receipt(
                txn,
                command_id=command_id,
                operation=operation,
                digest=digest,
                session_id=session.session_id,
                result_kind="turn",
                result_id=result.turn_id or turn_id,
                event_cursor=event.cursor,
            )
            return ApplicationCommandResult(result, receipt)

        try:
            return self._translate(lambda: self.journal.transact(work))
        except ApplicationError:
            self._restore_session_projection(session, persistence)
            raise

    def create_approval(
        self, execution: DurableToolExecution, *, persistence=None, command_id=None
    ):
        persistence = persistence or self.persistence
        if persistence is None:
            raise ApplicationError(
                ApplicationErrorCode.UNAVAILABLE, "Session persistence is unavailable"
            )
        operation = "approval_create"
        payload = {"tool_execution_id": execution.tool_execution_id}
        command_id, digest, replay = self._prepare(operation, payload, command_id)
        if replay is not None:
            value = self._query(
                lambda: self.journal.get_approval(self.workspace_id, replay.result_id or "")
            )
            if value is None:
                raise ApplicationError(
                    ApplicationErrorCode.NEEDS_RECOVERY, "approval result is missing"
                )
            return ApplicationCommandResult(value, replay)

        def work(txn):
            existing = self._replay_in_txn(txn, command_id, digest)
            if existing is not None:
                value = txn.get_approval(self.workspace_id, existing.result_id or "")
                if value is None:
                    raise ApplicationError(
                        ApplicationErrorCode.NEEDS_RECOVERY, "approval result is missing"
                    )
                return ApplicationCommandResult(value, existing)
            value = persistence.create_pending_approval(execution)
            event = self._event(
                txn,
                event_type="approval.created",
                aggregate_kind="approval",
                aggregate_id=value.approval_id,
                payload={"tool_execution_id": execution.tool_execution_id},
            )
            receipt = self._receipt(
                txn,
                command_id=command_id,
                operation=operation,
                digest=digest,
                session_id=execution.session_id,
                result_kind="approval",
                result_id=value.approval_id,
                event_cursor=event.cursor,
            )
            return ApplicationCommandResult(value, receipt)

        return self._translate(lambda: self.journal.transact(work))

    def create_grant(
        self,
        *,
        task_run_id: str,
        agent_run_id: str,
        capabilities: tuple[CapabilityName, ...],
        reason: str,
        preview_digest: str,
        expires_at: datetime | None = None,
        grant_id: str | None = None,
        command_id: str | None = None,
    ) -> ApplicationCommandResult[CapabilityGrant]:
        """Create a grant from an explicit local-interface application command."""

        try:
            capabilities = validate_capability_subset(capabilities)
        except CapabilityGrantError as exc:
            raise self._translate_exception(exc) from exc
        operation = "grant_create"
        payload = {
            "task_run_id": task_run_id,
            "agent_run_id": agent_run_id,
            "capabilities": tuple(value.value for value in capabilities),
            "reason": reason,
            "preview_digest": preview_digest,
            "expires_at": expires_at.isoformat() if expires_at is not None else None,
            "grant_id": grant_id,
        }
        command_id, digest, replay = self._prepare(operation, payload, command_id)
        if replay is not None:
            value = self._query(
                lambda: self.journal.get_capability_grant(self.workspace_id, replay.result_id or "")
            )
            if value is None:
                raise ApplicationError(
                    ApplicationErrorCode.NEEDS_RECOVERY, "grant result is missing"
                )
            return ApplicationCommandResult(value, replay)

        def work(txn: SqliteOperationalJournal):
            existing = self._replay_in_txn(txn, command_id, digest)
            if existing is not None:
                value = txn.get_capability_grant(self.workspace_id, existing.result_id or "")
                if value is None:
                    raise ApplicationError(
                        ApplicationErrorCode.NEEDS_RECOVERY, "grant result is missing"
                    )
                return ApplicationCommandResult(value, existing)
            run = txn.get_agent_run(self.workspace_id, agent_run_id)
            task = txn.get_task_run(self.workspace_id, task_run_id)
            if run is None or task is None:
                raise ApplicationError(ApplicationErrorCode.NOT_FOUND, "grant subject is missing")
            turn = txn.get_turn(self.workspace_id, run.turn_id)
            if turn is None or turn.task_run_id != task_run_id or run.session_id != task.session_id:
                raise ApplicationError(
                    ApplicationErrorCode.CROSS_WORKSPACE,
                    "grant subject does not match the requested TaskRun",
                )
            if run.snapshot.permission_profile_digest != _FULL_ACCESS_MANUAL_PROFILE_DIGEST:
                raise ApplicationError(
                    ApplicationErrorCode.INVALID,
                    "CapabilityGrant requires a Full Access Manual AgentRun",
                )
            created_at = _now(self.clock)
            value = CapabilityGrantService(txn, workspace_id=self.workspace_id).create(
                CapabilityGrant(
                    grant_id=grant_id or self.id_source.new_id(CAPABILITY_GRANT_ID_PREFIX),
                    workspace_id=self.workspace_id,
                    task_run_id=task_run_id,
                    agent_run_id=agent_run_id,
                    capabilities=capabilities,
                    command_id=command_id,
                    reason=reason,
                    preview_digest=preview_digest,
                    policy_version=PERMISSION_POLICY_VERSION,
                    created_at=created_at,
                    expires_at=expires_at or created_at + timedelta(minutes=15),
                ),
                now=created_at,
            )
            event = self._event(
                txn,
                event_type="grant.created",
                aggregate_kind="grant",
                aggregate_id=value.grant_id,
                payload={
                    "task_run_id": value.task_run_id,
                    "agent_run_id": value.agent_run_id,
                    "capabilities": tuple(item.value for item in value.capabilities),
                    "granted_by": value.granted_by.value,
                    "expires_at": value.expires_at.isoformat(),
                },
            )
            receipt = self._receipt(
                txn,
                command_id=command_id,
                operation=operation,
                digest=digest,
                session_id=run.session_id,
                result_kind="grant",
                result_id=value.grant_id,
                row_version=value.row_version,
                event_cursor=event.cursor,
            )
            return ApplicationCommandResult(value, receipt)

        return self._translate(lambda: self.journal.transact(work))

    def revoke_grant(
        self,
        grant_id: str,
        *,
        reason: str,
        expected_row_version: int | None = None,
        command_id: str | None = None,
    ) -> ApplicationCommandResult[CapabilityGrant]:
        operation = "grant_revoke"
        payload = {
            "grant_id": grant_id,
            "reason": reason,
            "expected_row_version": expected_row_version,
        }
        command_id, digest, replay = self._prepare(operation, payload, command_id)
        if replay is not None:
            value = self._query(
                lambda: self.journal.get_capability_grant(self.workspace_id, replay.result_id or "")
            )
            if value is None:
                raise ApplicationError(
                    ApplicationErrorCode.NEEDS_RECOVERY, "grant result is missing"
                )
            return ApplicationCommandResult(value, replay)

        def work(txn: SqliteOperationalJournal):
            existing = self._replay_in_txn(txn, command_id, digest)
            if existing is not None:
                value = txn.get_capability_grant(self.workspace_id, existing.result_id or "")
                if value is None:
                    raise ApplicationError(
                        ApplicationErrorCode.NEEDS_RECOVERY, "grant result is missing"
                    )
                return ApplicationCommandResult(value, existing)
            current = txn.get_capability_grant(self.workspace_id, grant_id)
            if current is None:
                raise ApplicationError(
                    ApplicationErrorCode.NOT_FOUND, "capability grant is missing"
                )
            expected = current.row_version if expected_row_version is None else expected_row_version
            was_unrevoked = current.revoked_at is None
            value = CapabilityGrantService(txn, workspace_id=self.workspace_id).revoke(
                current,
                reason=reason,
                now=_now(self.clock),
                expected_row_version=expected,
            )
            invalidated_approvals = 0
            cancellation_requests = 0
            if was_unrevoked:
                revoke_reason = "capability grant revoked"
                for approval in txn.list_approvals_for_grant(self.workspace_id, value.grant_id):
                    if approval.resolution.value == "pending" and approval.consumed_at is None:
                        txn.revoke_approval_in_txn(
                            self.workspace_id,
                            approval.approval_id,
                            now=value.revoked_at or _now(self.clock),
                            reason=revoke_reason,
                        )
                        invalidated_approvals += 1
                for execution in txn.list_executions_for_grant(self.workspace_id, value.grant_id):
                    stamp = value.revoked_at or _now(self.clock)
                    if execution.state in {
                        ToolExecutionState.PREPARED,
                        ToolExecutionState.AWAITING_APPROVAL,
                    }:
                        closed = transition_execution(
                            execution,
                            ToolExecutionState.CLOSED,
                            expected_row_version=execution.row_version,
                            disposition=ToolExecutionDisposition.DENIED,
                            now=stamp,
                        )
                        txn.save_execution(
                            self.workspace_id,
                            closed,
                            expected_row_version=execution.row_version,
                        )
                    elif execution.state is ToolExecutionState.EXECUTING:
                        requested = txn.request_execution_cancellation_in_txn(
                            self.workspace_id,
                            execution.tool_execution_id,
                            now=stamp,
                            reason=revoke_reason,
                        )
                        if requested is not None and requested.cancel_requested_at is not None:
                            cancellation_requests += 1
            run = txn.get_agent_run(self.workspace_id, value.agent_run_id)
            if run is None:
                raise ApplicationError(
                    ApplicationErrorCode.NEEDS_RECOVERY, "grant subject is missing"
                )
            event = self._event(
                txn,
                event_type="grant.revoked",
                aggregate_kind="grant",
                aggregate_id=value.grant_id,
                payload={
                    "agent_run_id": value.agent_run_id,
                    "revoked_at": value.revoked_at.isoformat() if value.revoked_at else None,
                    "row_version": value.row_version,
                    "invalidated_approvals": invalidated_approvals,
                    "cancellation_requests": cancellation_requests,
                },
            )
            receipt = self._receipt(
                txn,
                command_id=command_id,
                operation=operation,
                digest=digest,
                session_id=run.session_id,
                result_kind="grant",
                result_id=value.grant_id,
                row_version=value.row_version,
                event_cursor=event.cursor,
            )
            return ApplicationCommandResult(value, receipt)

        return self._translate(lambda: self.journal.transact(work))

    def resolve_approval(
        self,
        execution: DurableToolExecution,
        approval: DurableApproval,
        *,
        approved: bool,
        command_id: str | None = None,
        persistence=None,
    ):
        persistence = persistence or self.persistence
        if persistence is None:
            raise ApplicationError(
                ApplicationErrorCode.UNAVAILABLE, "Session persistence is unavailable"
            )
        operation = "approval_resolve"
        payload = {
            "approval_id": approval.approval_id,
            "tool_execution_id": execution.tool_execution_id,
            "approved": approved,
        }
        command_id, digest, replay = self._prepare(operation, payload, command_id)
        if replay is not None:
            saved_approval = self._query(
                lambda: self.journal.get_approval(self.workspace_id, replay.result_id or "")
            )
            saved_execution = self._query(
                lambda: self.journal.get_execution(self.workspace_id, execution.tool_execution_id)
            )
            if saved_approval is None or saved_execution is None:
                raise ApplicationError(
                    ApplicationErrorCode.NEEDS_RECOVERY, "approval result is missing"
                )
            return ApplicationCommandResult(
                (saved_execution, saved_approval, saved_approval.resolution.value == "approved"),
                replay,
            )

        def work(txn):
            existing = self._replay_in_txn(txn, command_id, digest)
            if existing is not None:
                saved_approval = txn.get_approval(self.workspace_id, existing.result_id or "")
                saved_execution = txn.get_execution(self.workspace_id, execution.tool_execution_id)
                if saved_approval is None or saved_execution is None:
                    raise ApplicationError(
                        ApplicationErrorCode.NEEDS_RECOVERY, "approval result is missing"
                    )
                return ApplicationCommandResult(
                    (
                        saved_execution,
                        saved_approval,
                        saved_approval.resolution.value == "approved",
                    ),
                    existing,
                )
            saved_execution, saved_approval, did_execute = persistence.consume_and_mark_executing(
                execution, approval, approved=approved, command_id=command_id
            )
            event = self._event(
                txn,
                event_type="approval.resolved",
                aggregate_kind="approval",
                aggregate_id=saved_approval.approval_id,
                payload={"resolution": saved_approval.resolution.value, "approved": did_execute},
            )
            receipt = self._receipt(
                txn,
                command_id=command_id,
                operation=operation,
                digest=digest,
                session_id=execution.session_id,
                result_kind="approval",
                result_id=saved_approval.approval_id,
                event_cursor=event.cursor,
            )
            return ApplicationCommandResult((saved_execution, saved_approval, did_execute), receipt)

        return self._translate(lambda: self.journal.transact(work))

    # Commands ----------------------------------------------------------------

    def create_session(
        self, *, session_id: str | None = None, command_id: str | None = None
    ) -> ApplicationCommandResult[DurableSession]:
        operation = "session_create"
        payload = {"session_id": session_id}
        command_id, digest, replay = self._prepare(operation, payload, command_id)
        if replay is not None:
            value = self._query(
                lambda: self.journal.get_session(self.workspace_id, replay.result_id or "")
            )
            if value is None:
                raise ApplicationError(
                    ApplicationErrorCode.NEEDS_RECOVERY, "command result is missing"
                )
            return ApplicationCommandResult(value, replay)

        def work(txn: SqliteOperationalJournal):
            existing = self._replay_in_txn(txn, command_id, digest)
            if existing is not None:
                value = txn.get_session(self.workspace_id, existing.result_id or "")
                if value is None:
                    raise ApplicationError(
                        ApplicationErrorCode.NEEDS_RECOVERY, "command result is missing"
                    )
                return ApplicationCommandResult(value, existing)
            created = DurableSession(
                session_id=session_id or self.id_source.new_id("ses"),
                workspace_id=self.workspace_id,
            )
            value = txn.create_session(created)
            event = self._event(
                txn,
                event_type="session.created",
                aggregate_kind="session",
                aggregate_id=value.session_id,
                payload={"lifecycle": value.lifecycle.value, "health": value.health.value},
            )
            receipt = self._receipt(
                txn,
                command_id=command_id,
                operation=operation,
                digest=digest,
                session_id=value.session_id,
                result_kind="session",
                result_id=value.session_id,
                event_cursor=event.cursor,
            )
            return ApplicationCommandResult(value, receipt)

        return self._translate(lambda: self.journal.transact(work))

    def archive_session(
        self,
        session_id: str,
        *,
        command_id: str | None = None,
        expected_updated_at: datetime | None = None,
    ) -> ApplicationCommandResult[DurableSession]:
        operation = "session_archive"
        payload = {
            "session_id": session_id,
            "expected_updated_at": expected_updated_at.isoformat() if expected_updated_at else None,
        }
        command_id, digest, replay = self._prepare(operation, payload, command_id)
        if replay is not None:
            value = self._require_session(replay.result_id or "")
            return ApplicationCommandResult(value, replay)

        def work(txn: SqliteOperationalJournal):
            existing = self._replay_in_txn(txn, command_id, digest)
            if existing is not None:
                return ApplicationCommandResult(
                    self._require_session(existing.result_id or ""), existing
                )
            current = txn.get_session(self.workspace_id, session_id)
            if current is None:
                raise ApplicationError(ApplicationErrorCode.NOT_FOUND, "Session is missing")
            if expected_updated_at is not None and current.updated_at != expected_updated_at:
                raise ApplicationError(ApplicationErrorCode.STALE, "Session row is stale")
            if current.lifecycle is SessionLifecycle.DELETED:
                raise ApplicationError(
                    ApplicationErrorCode.INVALID, "deleted Session cannot be archived"
                )
            updated = current.model_copy(
                update={"lifecycle": SessionLifecycle.ARCHIVED, "updated_at": _now(self.clock)}
            )
            value = txn.save_session(self.workspace_id, updated)
            event = self._event(
                txn,
                event_type="session.archived",
                aggregate_kind="session",
                aggregate_id=value.session_id,
                payload={"lifecycle": value.lifecycle.value},
            )
            receipt = self._receipt(
                txn,
                command_id=command_id,
                operation=operation,
                digest=digest,
                session_id=value.session_id,
                result_kind="session",
                result_id=value.session_id,
                event_cursor=event.cursor,
            )
            return ApplicationCommandResult(value, receipt)

        return self._translate(lambda: self.journal.transact(work))

    def fork_session(
        self,
        parent_session_id: str,
        *,
        checkpoint_id: str | None = None,
        cut_position: int | None = None,
        reason: str = "context fork",
        child_session_id: str | None = None,
        command_id: str | None = None,
    ) -> ApplicationCommandResult[DurableSession]:
        operation = "session_fork"
        payload = {
            "parent_session_id": parent_session_id,
            "checkpoint_id": checkpoint_id,
            "cut_position": cut_position,
            "reason": reason,
            "child_session_id": child_session_id,
        }
        command_id, digest, replay = self._prepare(operation, payload, command_id)
        if replay is not None:
            return ApplicationCommandResult(self._require_session(replay.result_id or ""), replay)
        forks = self.forks
        if forks is None:
            raise ApplicationError(
                ApplicationErrorCode.UNAVAILABLE, "Session fork service is unavailable"
            )

        def work(txn: SqliteOperationalJournal):
            existing = self._replay_in_txn(txn, command_id, digest)
            if existing is not None:
                return ApplicationCommandResult(
                    self._require_session(existing.result_id or ""), existing
                )
            value = forks.fork(
                parent_session_id,
                checkpoint_id=checkpoint_id,
                cut_position=cut_position,
                reason=reason,
                child_session_id=child_session_id,
            )
            event = self._event(
                txn,
                event_type="session.forked",
                aggregate_kind="session",
                aggregate_id=value.session_id,
                payload={
                    "parent_session_id": parent_session_id,
                    "cut_position": value.parent_cut_position,
                    "checkpoint_id": value.parent_checkpoint_id,
                },
            )
            receipt = self._receipt(
                txn,
                command_id=command_id,
                operation=operation,
                digest=digest,
                session_id=value.session_id,
                result_kind="session",
                result_id=value.session_id,
                event_cursor=event.cursor,
            )
            return ApplicationCommandResult(value, receipt)

        return self._translate(lambda: self.journal.transact(work))

    def task_new(
        self,
        session_id: str,
        *,
        command_id: str | None = None,
        expected_row_version: int | None = None,
    ) -> ApplicationCommandResult[DurableTaskRun]:
        return self._task_command(
            "task_new",
            {"session_id": session_id, "expected_row_version": expected_row_version},
            command_id,
            lambda cid: self.tasks.new_task(
                session_id, command_id=cid, expected_row_version=expected_row_version
            ),
            event_type="task.created",
        )

    def task_accept(
        self,
        task_run_id: str,
        *,
        command_id: str | None = None,
        expected_row_version: int | None = None,
    ) -> ApplicationCommandResult[DurableTaskRun]:
        return self._task_command(
            "task_accept",
            {"task_run_id": task_run_id, "expected_row_version": expected_row_version},
            command_id,
            lambda cid: self.tasks.accept(
                task_run_id, command_id=cid, expected_row_version=expected_row_version
            ),
            event_type="task.accepted",
        )

    def task_cancel(
        self,
        task_run_id: str,
        *,
        command_id: str | None = None,
        expected_row_version: int | None = None,
    ) -> ApplicationCommandResult[DurableTaskRun]:
        return self._task_command(
            "task_cancel",
            {"task_run_id": task_run_id, "expected_row_version": expected_row_version},
            command_id,
            lambda cid: self.tasks.cancel(
                task_run_id, command_id=cid, expected_row_version=expected_row_version
            ),
            event_type="task.cancelled",
        )

    def task_resume(
        self,
        task_run_id: str,
        *,
        command_id: str | None = None,
        expected_row_version: int | None = None,
    ) -> ApplicationCommandResult[DurableTaskRun]:
        return self._task_command(
            "task_resume",
            {"task_run_id": task_run_id, "expected_row_version": expected_row_version},
            command_id,
            lambda cid: self.tasks.resume(
                task_run_id, command_id=cid, expected_row_version=expected_row_version
            ),
            event_type="task.resumed",
        )

    def pin_artifact(
        self,
        artifact_id: str,
        *,
        command_id: str | None = None,
        expected_row_version: int | None = None,
    ) -> ApplicationCommandResult[ArtifactMetadata]:
        return self._artifact_retention(
            artifact_id,
            retention="pinned",
            command_id=command_id,
            expected_row_version=expected_row_version,
        )

    def release_artifact(
        self,
        artifact_id: str,
        *,
        command_id: str | None = None,
        expected_row_version: int | None = None,
    ) -> ApplicationCommandResult[ArtifactMetadata]:
        return self._artifact_retention(
            artifact_id,
            retention="standard",
            command_id=command_id,
            expected_row_version=expected_row_version,
        )

    def resolve_recovery(
        self,
        report: RecoveryReport,
        *,
        command_id: str,
        resolution: RecoveryResolution,
        item_id: str | None = None,
        log=None,
        writer=None,
        close_all: bool = False,
    ) -> ApplicationCommandResult[RecoveryReport]:
        if self.recovery is None or log is None:
            raise ApplicationError(
                ApplicationErrorCode.UNAVAILABLE, "recovery service is unavailable"
            )
        if report.workspace_id != self.workspace_id:
            raise ApplicationError(
                ApplicationErrorCode.CROSS_WORKSPACE, "recovery report is outside the workspace"
            )
        payload = {
            "report_id": report.report_id,
            "resolution": resolution.value,
            "item_id": item_id,
        }
        command_id, digest, replay = self._prepare("recovery_resolve", payload, command_id)
        if replay is not None:
            value = self._query(
                lambda: self.journal.get_report(self.workspace_id, replay.result_id or "")
            )
            if value is None:
                raise ApplicationError(
                    ApplicationErrorCode.NEEDS_RECOVERY, "recovery result is missing"
                )
            return ApplicationCommandResult(value, replay)
        try:
            resumed_agent_run_id: list[str] = []
            updated, recovery_receipt, planned = self.recovery.decide(
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
                value = self._query(
                    lambda: self.journal.get_report(self.workspace_id, recovery_receipt.report_id)
                )
                if value is None:
                    raise ApplicationError(
                        ApplicationErrorCode.NEEDS_RECOVERY, "recovery result is missing"
                    )
                receipt = ApplicationCommandReceipt(
                    command_id=command_id,
                    workspace_id=self.workspace_id,
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
                existing = self._replay_in_txn(txn, command_id, digest)
                if existing is not None:
                    value = txn.get_report(self.workspace_id, existing.result_id or "")
                    if value is None:
                        raise ApplicationError(
                            ApplicationErrorCode.NEEDS_RECOVERY, "recovery result is missing"
                        )
                    return ApplicationCommandResult(value, existing)
                value = self.recovery.commit_decision(
                    updated,
                    recovery_receipt,
                    planned=planned,
                    log=log,
                    writer=writer,
                    close_all=close_all,
                    apply_log_projection=False,
                    finalize=lambda finalize_txn, saved: self._apply_recovery_lifecycle_in_txn(
                        finalize_txn,
                        report=report,
                        saved=saved,
                        resolution=resolution,
                        resumed_agent_run_id=resumed_agent_run_id,
                    ),
                )
                event = self._event(
                    txn,
                    event_type="recovery.resolved",
                    aggregate_kind="recovery",
                    aggregate_id=value.report_id,
                    payload={"status": value.status.value, "resolution": resolution.value},
                )
                receipt = self._receipt(
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
                result = self._translate(lambda: self.journal.transact(work))
                if planned is not None:
                    log.apply_committed(planned)
                self._sync_recovery_persistence(result.value, resumed_agent_run_id)
                return result
            except ApplicationError:
                self._restore_log_projection(log, report.session_id)
                raise
        except ApplicationError:
            raise
        except Exception as exc:
            self._restore_log_projection(log, report.session_id)
            raise self._translate_exception(exc) from exc

    def _apply_recovery_lifecycle_in_txn(
        self,
        txn: SqliteOperationalJournal,
        *,
        report: RecoveryReport,
        saved: RecoveryReport,
        resolution: RecoveryResolution,
        resumed_agent_run_id: list[str],
    ) -> None:
        """Keep Session health, turn receipts, tasks, and resume runs atomic with recovery."""

        session = txn.get_session(self.workspace_id, report.session_id)
        if session is None:
            raise StorageError(StorageErrorCode.NOT_FOUND, "operational session is missing")

        health = SessionHealth.NEEDS_RECOVERY
        current_task_run_id = session.current_task_run_id
        if saved.status is RecoveryReportStatus.QUARANTINED:
            health = SessionHealth.QUARANTINED
        elif saved.status is RecoveryReportStatus.RESOLVED:
            health = SessionHealth.OK
            if resolution is RecoveryResolution.RESUME and saved.agent_run_id is not None:
                previous = txn.get_agent_run(self.workspace_id, saved.agent_run_id)
                if previous is None:
                    raise StorageError(StorageErrorCode.NOT_FOUND, "recovery AgentRun is missing")
                new_id = self.id_source.new_id(AGENT_RUN_ID_PREFIX)
                txn.create_agent_run(
                    self.workspace_id,
                    DurableAgentRun(
                        agent_run_id=new_id,
                        turn_id=previous.turn_id,
                        session_id=previous.session_id,
                        resume_of_agent_run_id=previous.agent_run_id,
                        snapshot=previous.snapshot,
                    ),
                )
                resumed_agent_run_id.append(new_id)
            elif resolution is RecoveryResolution.ABORT:
                current_task_run_id = self._abort_recovery_task_in_txn(
                    txn, session, turn_id=report.turn_id
                )
                self._close_recovery_receipt_in_txn(txn, report)

        txn.save_session(
            self.workspace_id,
            session.model_copy(
                update={"health": health, "current_task_run_id": current_task_run_id}
            ),
        )

    def _sync_recovery_persistence(
        self, report: RecoveryReport, resumed_agent_run_id: list[str]
    ) -> None:
        persistence = self.persistence
        session = getattr(persistence, "_session", None) if persistence is not None else None
        if persistence is None or session is None or session.session_id != report.session_id:
            return
        row = self.journal.get_session(self.workspace_id, report.session_id)
        if row is None:
            return
        session.health = row.health
        persistence.current_task_run_id = row.current_task_run_id
        if resumed_agent_run_id:
            persistence.current_agent_run_id = resumed_agent_run_id[0]
            persistence.current_permission_snapshot_id = None
        persistence.open_report = None if report.status is RecoveryReportStatus.RESOLVED else report

    def _abort_recovery_task_in_txn(
        self, txn: SqliteOperationalJournal, session: DurableSession, *, turn_id: str | None
    ) -> str | None:
        task_id = session.current_task_run_id
        if task_id is None:
            return None
        task = txn.get_task_run(self.workspace_id, task_id)
        if task is None:
            raise StorageError(StorageErrorCode.NOT_FOUND, "recovery TaskRun is missing")
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

    # Internal composition helpers -------------------------------------------

    def _task_command(self, operation, payload, command_id, call, *, event_type):
        command_id, digest, replay = self._prepare(operation, payload, command_id)
        if replay is not None:
            value = self._require_task(replay.result_id or "")
            return ApplicationCommandResult(value, replay)

        def work(txn: SqliteOperationalJournal):
            existing = self._replay_in_txn(txn, command_id, digest)
            if existing is not None:
                return ApplicationCommandResult(
                    self._require_task(existing.result_id or ""), existing
                )
            result = call(command_id)
            if result.kind == "replay" and result.task is not None:
                return ApplicationCommandResult(result.task, result.receipt)
            if result.task is None:
                raise ApplicationError(
                    ApplicationErrorCode.NEEDS_RECOVERY, "Task command result is missing"
                )
            event = self._event(
                txn,
                event_type=event_type,
                aggregate_kind="task",
                aggregate_id=result.task.task_run_id,
                payload={
                    "status": result.task.status.value,
                    "row_version": result.task.row_version,
                },
            )
            receipt = self._receipt(
                txn,
                command_id=command_id,
                operation=operation,
                digest=digest,
                session_id=result.task.session_id,
                result_kind="task",
                result_id=result.task.task_run_id,
                row_version=result.task.row_version,
                event_cursor=event.cursor,
            )
            return ApplicationCommandResult(result.task, receipt)

        return self._translate(lambda: self.journal.transact(work))

    def _artifact_retention(self, artifact_id, *, retention, command_id, expected_row_version):
        if self.artifacts is None:
            raise ApplicationError(
                ApplicationErrorCode.UNAVAILABLE, "Artifact service is unavailable"
            )
        operation = "artifact_pin" if retention == "pinned" else "artifact_release"
        payload = {
            "artifact_id": artifact_id,
            "expected_row_version": expected_row_version,
        }
        command_id, digest, replay = self._prepare(operation, payload, command_id)
        if replay is not None:
            value = self.artifacts.get(replay.result_id or "")
            if value is None:
                raise ApplicationError(
                    ApplicationErrorCode.NEEDS_RECOVERY, "Artifact result is missing"
                )
            return ApplicationCommandResult(value, replay)

        def work(txn: SqliteOperationalJournal):
            existing = self._replay_in_txn(txn, command_id, digest)
            if existing is not None:
                value = self.artifacts.get(existing.result_id or "")
                if value is None:
                    raise ApplicationError(
                        ApplicationErrorCode.NEEDS_RECOVERY, "Artifact result is missing"
                    )
                return ApplicationCommandResult(value, existing)
            current = txn.get_artifact(self.workspace_id, artifact_id)
            if current is None:
                raise ApplicationError(ApplicationErrorCode.NOT_FOUND, "Artifact is missing")
            if expected_row_version is not None and current.row_version != expected_row_version:
                raise ApplicationError(ApplicationErrorCode.STALE, "Artifact row is stale")
            updated = (
                self.artifacts.pin(artifact_id)
                if retention == "pinned"
                else self.artifacts.unpin(artifact_id)
            )
            event = self._event(
                txn,
                event_type="artifact.pinned" if retention == "pinned" else "artifact.released",
                aggregate_kind="artifact",
                aggregate_id=updated.artifact_id,
                payload={"retention": updated.retention.value, "row_version": updated.row_version},
            )
            receipt = self._receipt(
                txn,
                command_id=command_id,
                operation=operation,
                digest=digest,
                session_id=updated.session_id,
                result_kind="artifact",
                result_id=updated.artifact_id,
                row_version=updated.row_version,
                event_cursor=event.cursor,
            )
            return ApplicationCommandResult(updated, receipt)

        return self._translate(lambda: self.journal.transact(work))

    def _prepare(self, operation, payload, command_id):
        command_id = command_id or self.id_source.new_id(COMMAND_ID_PREFIX)
        digest = request_digest(operation, payload)
        try:
            existing = self.journal.get_application_command_receipt(self.workspace_id, command_id)
        except Exception as exc:
            raise self._translate_exception(exc) from exc
        if existing is not None:
            if existing.request_digest != digest:
                raise ApplicationError(
                    ApplicationErrorCode.CONFLICT,
                    "command ID was reused with a different request",
                )
            return (
                command_id,
                digest,
                existing.model_copy(update={"disposition": ApplicationCommandDisposition.REPLAY}),
            )
        return command_id, digest, None

    def _replay_in_txn(self, txn, command_id, digest):
        existing = txn.get_application_command_receipt(self.workspace_id, command_id)
        if existing is None:
            return None
        if existing.request_digest != digest:
            raise ApplicationError(
                ApplicationErrorCode.CONFLICT,
                "command ID was reused with a different request",
            )
        return existing.model_copy(update={"disposition": ApplicationCommandDisposition.REPLAY})

    def _receipt(
        self,
        txn,
        *,
        command_id,
        operation,
        digest,
        session_id,
        result_kind,
        result_id,
        event_cursor,
        row_version=None,
    ):
        return txn.put_application_command_receipt_in_txn(
            self.workspace_id,
            ApplicationCommandReceipt(
                command_id=command_id,
                workspace_id=self.workspace_id,
                session_id=session_id,
                operation=operation,
                request_digest=digest,
                result_kind=result_kind,
                result_id=result_id,
                event_cursor=event_cursor,
                row_version=row_version,
            ),
        )

    def _event(self, txn, *, event_type, aggregate_kind, aggregate_id, payload):
        return txn.put_application_event_in_txn(
            self.workspace_id,
            ApplicationEvent(
                event_id=self.id_source.new_id("evt"),
                workspace_id=self.workspace_id,
                event_type=event_type,
                aggregate_kind=aggregate_kind,
                aggregate_id=aggregate_id,
                payload=payload,
                created_at=_now(self.clock),
            ),
        )

    def _require_session(self, session_id: str) -> DurableSession:
        value = self._query(lambda: self.journal.get_session(self.workspace_id, session_id))
        if value is None:
            raise ApplicationError(ApplicationErrorCode.NOT_FOUND, "Session is missing")
        if value.workspace_id != self.workspace_id:
            raise ApplicationError(
                ApplicationErrorCode.CROSS_WORKSPACE, "Session is outside the workspace"
            )
        return value

    def _require_task(self, task_run_id: str) -> DurableTaskRun:
        value = self._query(lambda: self.journal.get_task_run(self.workspace_id, task_run_id))
        if value is None:
            raise ApplicationError(ApplicationErrorCode.NOT_FOUND, "TaskRun is missing")
        return value

    @staticmethod
    def _offset(cursor: str | None, limit: int) -> int:
        if limit < 1 or limit > 100:
            raise ApplicationError(ApplicationErrorCode.INVALID, "query page size is invalid")
        if cursor is None:
            return 0
        try:
            value = int(cursor)
        except ValueError as exc:
            raise ApplicationError(ApplicationErrorCode.INVALID, "query cursor is invalid") from exc
        if value < 0:
            raise ApplicationError(ApplicationErrorCode.INVALID, "query cursor is invalid")
        return value

    def _translate(self, call):
        try:
            return call()
        except ApplicationError:
            raise
        except Exception as exc:
            raise self._translate_exception(exc) from exc

    def _query(self, call):
        try:
            return call()
        except ApplicationError:
            raise
        except Exception as exc:
            raise self._translate_exception(exc) from exc

    def _restore_session_projection(self, session, persistence) -> None:
        try:
            row = self.journal.get_session(self.workspace_id, session.session_id)
            if row is None:
                return
            session.log.install_snapshot(
                restore_conversation_log(
                    self.journal, self.workspace_id, session.session_id
                ).snapshot()
            )
            session.health = row.health
            session.lifecycle = row.lifecycle
            session.dirty = session.log.has_active_turn
            persistence.current_turn_id = None
            persistence.current_task_run_id = row.current_task_run_id
            persistence.current_agent_run_id = None
            persistence._last_client_message_id = None
            persistence.attach(session)
        except Exception:
            return

    def _restore_log_projection(self, log, session_id: str) -> None:
        try:
            log.install_snapshot(
                restore_conversation_log(self.journal, self.workspace_id, session_id).snapshot()
            )
        except Exception:
            return

    @staticmethod
    def _translate_exception(exc: Exception) -> ApplicationError:
        if isinstance(exc, StorageError):
            if "outside the workspace" in str(exc):
                return ApplicationError(ApplicationErrorCode.CROSS_WORKSPACE, str(exc))
            mapping = {
                StorageErrorCode.NOT_FOUND: ApplicationErrorCode.NOT_FOUND,
                StorageErrorCode.BUSY: ApplicationErrorCode.BUSY,
                StorageErrorCode.NEEDS_REPAIR: ApplicationErrorCode.NEEDS_RECOVERY,
                StorageErrorCode.UNAVAILABLE: ApplicationErrorCode.UNAVAILABLE,
                StorageErrorCode.FUTURE_SCHEMA: ApplicationErrorCode.UNAVAILABLE,
                StorageErrorCode.IDENTITY_MISMATCH: ApplicationErrorCode.NEEDS_RECOVERY,
            }
            return ApplicationError(mapping[exc.code], str(exc))
        if isinstance(exc, TaskCommandConflict):
            return ApplicationError(ApplicationErrorCode.CONFLICT, str(exc))
        if isinstance(exc, (TaskCommandError, RecoveryDecisionError, ContextCheckpointError)):
            text = str(exc)
            code = (
                ApplicationErrorCode.STALE
                if "stale" in text.casefold()
                else ApplicationErrorCode.INVALID
            )
            return ApplicationError(code, text)
        if isinstance(exc, CapabilityGrantError):
            return ApplicationError(exc.code, str(exc))
        if isinstance(exc, ArtifactError):
            code = (
                ApplicationErrorCode.NOT_FOUND
                if exc.code.value.endswith("missing")
                else ApplicationErrorCode.CONFLICT
                if exc.code.value.endswith("conflict")
                else ApplicationErrorCode.INVALID
            )
            return ApplicationError(code, exc.message)
        if isinstance(exc, ValueError):
            return ApplicationError(ApplicationErrorCode.INVALID, "application input is invalid")
        return ApplicationError(ApplicationErrorCode.UNAVAILABLE, "application command failed")
