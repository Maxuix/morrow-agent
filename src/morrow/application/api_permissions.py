"""Approval and capability-grant commands behind the application facade."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from morrow.adapters.state.journal import SqliteOperationalJournal
from morrow.application.grants import (
    CapabilityGrantError,
    CapabilityGrantService,
    validate_capability_subset,
)
from morrow.core.application import (
    ApplicationCommandResult,
    ApplicationError,
    ApplicationErrorCode,
)
from morrow.core.capabilities import PermissionPreset, PermissionProfile
from morrow.core.domain import canonical_json_bytes, sha256_digest
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
)

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


class PermissionApplicationService:
    """Own approval and grant command transactions for one application facade."""

    def __init__(self, application) -> None:
        self.application = application

    def create_approval(
        self, execution: DurableToolExecution, *, persistence=None, command_id=None
    ):
        api = self.application
        persistence = persistence or api.persistence
        if persistence is None:
            raise ApplicationError(
                ApplicationErrorCode.UNAVAILABLE, "Session persistence is unavailable"
            )
        operation = "approval_create"
        payload = {"tool_execution_id": execution.tool_execution_id}
        command_id, digest, replay = api._prepare(operation, payload, command_id)
        if replay is not None:
            value = api._query(
                lambda: api.journal.get_approval(api.workspace_id, replay.result_id or "")
            )
            if value is None:
                raise ApplicationError(
                    ApplicationErrorCode.NEEDS_RECOVERY, "approval result is missing"
                )
            return ApplicationCommandResult(value, replay)

        def work(txn):
            existing = api._replay_in_txn(txn, command_id, digest)
            if existing is not None:
                value = txn.get_approval(api.workspace_id, existing.result_id or "")
                if value is None:
                    raise ApplicationError(
                        ApplicationErrorCode.NEEDS_RECOVERY, "approval result is missing"
                    )
                return ApplicationCommandResult(value, existing)
            value = persistence.create_pending_approval(execution)
            event = api._event(
                txn,
                event_type="approval.created",
                aggregate_kind="approval",
                aggregate_id=value.approval_id,
                payload={"tool_execution_id": execution.tool_execution_id},
            )
            receipt = api._receipt(
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

        return api._translate(lambda: api.journal.transact(work))

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
        api = self.application
        try:
            capabilities = validate_capability_subset(capabilities)
        except CapabilityGrantError as exc:
            raise api._translate_exception(exc) from exc
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
        command_id, digest, replay = api._prepare(operation, payload, command_id)
        if replay is not None:
            value = api._query(
                lambda: api.journal.get_capability_grant(api.workspace_id, replay.result_id or "")
            )
            if value is None:
                raise ApplicationError(
                    ApplicationErrorCode.NEEDS_RECOVERY, "grant result is missing"
                )
            return ApplicationCommandResult(value, replay)

        def work(txn: SqliteOperationalJournal):
            existing = api._replay_in_txn(txn, command_id, digest)
            if existing is not None:
                value = txn.get_capability_grant(api.workspace_id, existing.result_id or "")
                if value is None:
                    raise ApplicationError(
                        ApplicationErrorCode.NEEDS_RECOVERY, "grant result is missing"
                    )
                return ApplicationCommandResult(value, existing)
            run = txn.get_agent_run(api.workspace_id, agent_run_id)
            task = txn.get_task_run(api.workspace_id, task_run_id)
            if run is None or task is None:
                raise ApplicationError(ApplicationErrorCode.NOT_FOUND, "grant subject is missing")
            turn = txn.get_turn(api.workspace_id, run.turn_id)
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
            created_at = _now(api.clock)
            value = CapabilityGrantService(txn, workspace_id=api.workspace_id).create(
                CapabilityGrant(
                    grant_id=grant_id or api.id_source.new_id(CAPABILITY_GRANT_ID_PREFIX),
                    workspace_id=api.workspace_id,
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
            event = api._event(
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
            receipt = api._receipt(
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

        return api._translate(lambda: api.journal.transact(work))

    def revoke_grant(
        self,
        grant_id: str,
        *,
        reason: str,
        expected_row_version: int | None = None,
        command_id: str | None = None,
    ) -> ApplicationCommandResult[CapabilityGrant]:
        api = self.application
        operation = "grant_revoke"
        payload = {
            "grant_id": grant_id,
            "reason": reason,
            "expected_row_version": expected_row_version,
        }
        command_id, digest, replay = api._prepare(operation, payload, command_id)
        if replay is not None:
            value = api._query(
                lambda: api.journal.get_capability_grant(api.workspace_id, replay.result_id or "")
            )
            if value is None:
                raise ApplicationError(
                    ApplicationErrorCode.NEEDS_RECOVERY, "grant result is missing"
                )
            return ApplicationCommandResult(value, replay)

        def work(txn: SqliteOperationalJournal):
            existing = api._replay_in_txn(txn, command_id, digest)
            if existing is not None:
                value = txn.get_capability_grant(api.workspace_id, existing.result_id or "")
                if value is None:
                    raise ApplicationError(
                        ApplicationErrorCode.NEEDS_RECOVERY, "grant result is missing"
                    )
                return ApplicationCommandResult(value, existing)
            current = txn.get_capability_grant(api.workspace_id, grant_id)
            if current is None:
                raise ApplicationError(
                    ApplicationErrorCode.NOT_FOUND, "capability grant is missing"
                )
            expected = current.row_version if expected_row_version is None else expected_row_version
            was_unrevoked = current.revoked_at is None
            value = CapabilityGrantService(txn, workspace_id=api.workspace_id).revoke(
                current,
                reason=reason,
                now=_now(api.clock),
                expected_row_version=expected,
            )
            invalidated_approvals = 0
            cancellation_requests = 0
            if was_unrevoked:
                revoke_reason = "capability grant revoked"
                for approval in txn.list_approvals_for_grant(api.workspace_id, value.grant_id):
                    if approval.resolution.value == "pending" and approval.consumed_at is None:
                        txn.revoke_approval_in_txn(
                            api.workspace_id,
                            approval.approval_id,
                            now=value.revoked_at or _now(api.clock),
                            reason=revoke_reason,
                        )
                        invalidated_approvals += 1
                for execution in txn.list_executions_for_grant(api.workspace_id, value.grant_id):
                    stamp = value.revoked_at or _now(api.clock)
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
                            api.workspace_id,
                            closed,
                            expected_row_version=execution.row_version,
                        )
                    elif execution.state is ToolExecutionState.EXECUTING:
                        requested = txn.request_execution_cancellation_in_txn(
                            api.workspace_id,
                            execution.tool_execution_id,
                            now=stamp,
                            reason=revoke_reason,
                        )
                        if requested is not None and requested.cancel_requested_at is not None:
                            cancellation_requests += 1
            run = txn.get_agent_run(api.workspace_id, value.agent_run_id)
            if run is None:
                raise ApplicationError(
                    ApplicationErrorCode.NEEDS_RECOVERY, "grant subject is missing"
                )
            event = api._event(
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
            receipt = api._receipt(
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

        return api._translate(lambda: api.journal.transact(work))

    def resolve_approval(
        self,
        execution: DurableToolExecution,
        approval: DurableApproval,
        *,
        approved: bool,
        command_id: str | None = None,
        persistence=None,
    ):
        api = self.application
        persistence = persistence or api.persistence
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
        command_id, digest, replay = api._prepare(operation, payload, command_id)
        if replay is not None:
            saved_approval = api._query(
                lambda: api.journal.get_approval(api.workspace_id, replay.result_id or "")
            )
            saved_execution = api._query(
                lambda: api.journal.get_execution(api.workspace_id, execution.tool_execution_id)
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
            existing = api._replay_in_txn(txn, command_id, digest)
            if existing is not None:
                saved_approval = txn.get_approval(api.workspace_id, existing.result_id or "")
                saved_execution = txn.get_execution(api.workspace_id, execution.tool_execution_id)
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
            event = api._event(
                txn,
                event_type="approval.resolved",
                aggregate_kind="approval",
                aggregate_id=saved_approval.approval_id,
                payload={"resolution": saved_approval.resolution.value, "approved": did_execute},
            )
            receipt = api._receipt(
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

        return api._translate(lambda: api.journal.transact(work))
