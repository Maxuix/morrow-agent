"""The single typed Command/Query boundary for completed Stage 4 domains."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from morrow.adapters.state.journal import SqliteOperationalJournal
from morrow.application.api_context import ApplicationCommandContext
from morrow.application.api_context import request_digest as _request_digest
from morrow.application.api_permissions import PermissionApplicationService
from morrow.application.api_recovery import RecoveryApplicationService
from morrow.application.artifacts import ArtifactService
from morrow.application.checkpoints import ContextCheckpointService, SessionForkService
from morrow.application.cleanup import ArtifactCleanupService
from morrow.application.recovery import RecoveryService
from morrow.application.tasks import TaskService
from morrow.application.turns import TurnSubmitResult
from morrow.core.application import (
    ApplicationCommandResult,
    ApplicationError,
    ApplicationErrorCode,
    ApplicationEvent,
    QueryPage,
)
from morrow.core.artifacts import ArtifactMetadata
from morrow.core.domain import (
    WORKSPACE_ID_PREFIX,
    DurableSession,
    DurableTaskOutcome,
    DurableTaskRun,
    SessionLifecycle,
    sha256_digest,
    validate_prefixed_id,
)
from morrow.core.execution import DurableApproval, DurableToolExecution
from morrow.core.permissions import (
    CapabilityGrant,
    CapabilityName,
    PermissionSnapshot,
)
from morrow.core.ports import IdSource
from morrow.core.recovery import RecoveryReport, RecoveryResolution
from morrow.runtime.ids import RandomIdSource


def _now(clock: Callable[[], datetime] | None) -> datetime:
    value = clock() if clock is not None else datetime.now(UTC)
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def request_digest(operation: str, payload: dict[str, object]) -> str:
    return _request_digest(operation, payload)


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
        self.clock = clock or journal.now
        self.tasks = tasks or TaskService(
            journal=journal,
            workspace_id=workspace_id,
            id_source=self.id_source,
            clock=self.clock,
        )
        self.artifacts = artifacts
        self.recovery = recovery
        self.checkpoints = checkpoints
        self.forks = forks
        self.persistence = persistence
        self.command_context = ApplicationCommandContext(
            journal=self.journal,
            workspace_id=self.workspace_id,
            id_source=self.id_source,
            clock=self.clock,
            tasks=self.tasks,
            recovery=self.recovery,
            persistence=self.persistence,
        )
        self._recovery_commands = RecoveryApplicationService(self.command_context)
        self._permission_commands = PermissionApplicationService(self.command_context)

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
        return self._permission_commands.create_approval(
            execution, persistence=persistence, command_id=command_id
        )

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
        return self._permission_commands.create_grant(
            task_run_id=task_run_id,
            agent_run_id=agent_run_id,
            capabilities=capabilities,
            reason=reason,
            preview_digest=preview_digest,
            expires_at=expires_at,
            grant_id=grant_id,
            command_id=command_id,
        )

    def revoke_grant(
        self,
        grant_id: str,
        *,
        reason: str,
        expected_row_version: int | None = None,
        command_id: str | None = None,
    ) -> ApplicationCommandResult[CapabilityGrant]:
        return self._permission_commands.revoke_grant(
            grant_id,
            reason=reason,
            expected_row_version=expected_row_version,
            command_id=command_id,
        )

    def resolve_approval(
        self,
        execution: DurableToolExecution,
        approval: DurableApproval,
        *,
        approved: bool,
        command_id: str | None = None,
        persistence=None,
    ):
        return self._permission_commands.resolve_approval(
            execution,
            approval,
            approved=approved,
            command_id=command_id,
            persistence=persistence,
        )

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
            stamp = _now(self.clock)
            created = DurableSession(
                session_id=session_id or self.id_source.new_id("ses"),
                workspace_id=self.workspace_id,
                created_at=stamp,
                updated_at=stamp,
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
            if current.current_task_run_id is not None:
                raise ApplicationError(
                    ApplicationErrorCode.INVALID,
                    "Session must close its current TaskRun before archive",
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
        command_id: str | None = None,
        resolution: RecoveryResolution,
        item_id: str | None = None,
        log=None,
        writer=None,
        close_all: bool = False,
    ) -> ApplicationCommandResult[RecoveryReport]:
        return self._recovery_commands.resolve(
            report,
            command_id=command_id,
            resolution=resolution,
            item_id=item_id,
            log=log,
            writer=writer,
            close_all=close_all,
        )

    def resolve_recovery_by_id(
        self,
        report_id: str,
        *,
        command_id: str | None = None,
        resolution: RecoveryResolution,
        item_id: str | None = None,
    ) -> ApplicationCommandResult[RecoveryReport]:
        return self._recovery_commands.resolve_by_id(
            report_id,
            command_id=command_id,
            resolution=resolution,
            item_id=item_id,
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
        return self.command_context._prepare(operation, payload, command_id)

    def _replay_in_txn(self, txn, command_id, digest):
        return self.command_context._replay_in_txn(txn, command_id, digest)

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
        return self.command_context._receipt(
            txn,
            command_id=command_id,
            operation=operation,
            digest=digest,
            session_id=session_id,
            result_kind=result_kind,
            result_id=result_id,
            event_cursor=event_cursor,
            row_version=row_version,
        )

    def _event(self, txn, *, event_type, aggregate_kind, aggregate_id, payload):
        return self.command_context._event(
            txn,
            event_type=event_type,
            aggregate_kind=aggregate_kind,
            aggregate_id=aggregate_id,
            payload=payload,
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
        return self.command_context._translate(call)

    def _query(self, call):
        return self.command_context._query(call)

    def _restore_session_projection(self, session, persistence) -> None:
        try:
            persistence.synchronize_projection(session)
        except Exception:
            return

    def _restore_log_projection(self, log, session_id: str) -> None:
        self.command_context._restore_log_projection(log, session_id)

    @staticmethod
    def _translate_exception(exc: Exception) -> ApplicationError:
        return ApplicationCommandContext._translate_exception(exc)
