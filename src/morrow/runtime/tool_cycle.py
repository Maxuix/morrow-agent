"""One tool-call execution collaborator with no ConversationLog or public-event ownership."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from datetime import datetime

from morrow.core.capabilities import PolicyVerdict, ToolRunContext
from morrow.core.execution import (
    ApprovalDecisionError,
    DurableToolExecution,
    ExecutionTransitionError,
    MissingCompletionPolicy,
    ToolExecutionDisposition,
    ToolExecutionState,
)
from morrow.core.faults import FaultPoint
from morrow.core.models import FunctionToolCall, ToolApprovalRequest, ToolEffect
from morrow.core.permissions import PermissionEvidenceError
from morrow.runtime.policy import RunPolicy
from morrow.runtime.session import DurableRunCoordinator, Session
from morrow.runtime.tools import (
    ToolErrorCode,
    ToolExecutionOutcome,
    ToolExecutor,
    policy_denial_message,
)


class ToolCancellationRequested(Exception):
    """The durable cancellation flag was observed before the handler completed."""


@dataclass(frozen=True)
class ToolCallExecution:
    outcome: ToolExecutionOutcome
    durable_execution: DurableToolExecution | None


class ToolCycleExecutor:
    """Execute and durably classify one call without writing chat history or public events."""

    def __init__(
        self,
        tool_executor: ToolExecutor,
        run_policy: RunPolicy,
        *,
        wall_now: Callable[[Session], datetime],
    ) -> None:
        self.tool_executor = tool_executor
        self.run_policy = run_policy
        self.wall_now = wall_now

    async def execute_call(
        self,
        session: Session,
        call: FunctionToolCall,
        *,
        durable_execution: DurableToolExecution | None,
        run_context: ToolRunContext,
        ordinal: int,
        total: int,
        result_limit: int,
        preflight_error: tuple[ToolErrorCode, str] | None = None,
    ) -> ToolCallExecution:
        durable = durable_execution
        if preflight_error is not None:
            code, message = preflight_error
            result = self.tool_executor.error_outcome(
                call, code, message, result_limit=result_limit
            )
            if durable is not None:
                durable = self._coordinator(session).deny_execution_before_handler(
                    durable, now=self.wall_now(session)
                )
            self.tool_executor.cleanup_call(
                call,
                run_context=run_context,
                ordinal=ordinal,
                total=total,
                result_limit=result_limit,
            )
            return ToolCallExecution(result, durable)
        skip_approval = durable is not None
        denied_result = None
        handler_disposition = None

        if durable is not None:
            coordinator = self._coordinator(session)
            if durable.intent.policy_verdict is PolicyVerdict.DENY:
                denied_result = self.tool_executor.error_outcome(
                    call,
                    ToolErrorCode.PERMISSION_DENIED,
                    policy_denial_message(call.name, durable.intent.policy_reason_codes),
                    result_limit=result_limit,
                )
                durable = coordinator.deny_execution_before_handler(
                    durable, now=self.wall_now(session)
                )
            else:
                try:
                    durable, denied_result = await self._gate_durable(
                        session,
                        durable,
                        call,
                        now=self.wall_now(session),
                        result_limit=result_limit,
                    )
                except asyncio.CancelledError:
                    self.tool_executor.cleanup_call(
                        call,
                        run_context=run_context,
                        ordinal=ordinal,
                        total=total,
                        result_limit=result_limit,
                    )
                    raise
                except (
                    ApprovalDecisionError,
                    ExecutionTransitionError,
                    PermissionEvidenceError,
                ):
                    denied_result = self._permission_denied(call, result_limit)
                    durable = self.reload_durable(session, durable)
                    durable = coordinator.deny_execution_before_handler(
                        durable, now=self.wall_now(session)
                    )

        try:
            if denied_result is not None:
                result = denied_result
            else:
                if durable is not None:
                    coordinator = self._coordinator(session)
                    coordinator.check_fault(FaultPoint.HANDLER_BEFORE_ENTER)
                    durable = coordinator.assert_handler_may_enter(
                        durable, now=self.wall_now(session)
                    )
                allow_unconfined_host = (
                    call.name in {"run_command", "bash"}
                    and durable is not None
                    and self._has_active_unconfined_grant(session, durable)
                )
                execution = self.tool_executor.execute_with_context(
                    call,
                    result_limit=result_limit,
                    run_context=run_context,
                    ordinal=ordinal,
                    total=total,
                    skip_approval=skip_approval,
                    allow_unconfined_host=allow_unconfined_host,
                )
                result = await asyncio.wait_for(
                    self.await_with_cancellation(execution, session, durable),
                    timeout=self.run_policy.tool_timeout_seconds,
                )
                if durable is not None:
                    self._coordinator(session).check_fault(FaultPoint.HANDLER_AFTER_RETURN)
        except TimeoutError:
            result = self.tool_executor.error_outcome(
                call,
                ToolErrorCode.TIMEOUT,
                "工具执行超时",
                result_limit=result_limit,
            )
            if durable is not None and self._unknown_after_handler_entry(call):
                handler_disposition = ToolExecutionDisposition.UNKNOWN
        except ToolCancellationRequested:
            result = self.tool_executor.error_outcome(
                call,
                ToolErrorCode.CANCELLED,
                "工具执行已收到撤销请求",
                result_limit=result_limit,
            )
            durable = self.reload_durable(session, durable)
            if durable is not None and durable.state is ToolExecutionState.EXECUTING:
                if self._unknown_after_handler_entry(call):
                    handler_disposition = ToolExecutionDisposition.UNKNOWN
            elif durable is not None and durable.state in {
                ToolExecutionState.PREPARED,
                ToolExecutionState.AWAITING_APPROVAL,
            }:
                durable = self._coordinator(session).cancel_execution_before_handler(
                    durable, now=self.wall_now(session)
                )
        except (
            ApprovalDecisionError,
            ExecutionTransitionError,
            PermissionEvidenceError,
        ):
            result = self._permission_denied(call, result_limit)
            durable = self.reload_durable(session, durable)
            if durable is not None:
                durable = self._coordinator(session).deny_execution_before_handler(
                    durable, now=self.wall_now(session)
                )

        if result.disposition is not None:
            handler_disposition = result.disposition
        if durable is not None and durable.state is ToolExecutionState.EXECUTING:
            durable = self._coordinator(session).record_handler_completed(
                durable,
                result,
                now=self.wall_now(session),
                disposition=handler_disposition,
            )
        result = self._attach_artifact_references(
            result,
            (
                *result.artifact_refs,
                *result.mcp_result_artifact_refs,
                *(durable.artifact_refs if durable is not None else ()),
            ),
            result_limit=result_limit,
        )
        self.tool_executor.cleanup_call(
            call,
            run_context=run_context,
            ordinal=ordinal,
            total=total,
            result_limit=result_limit,
        )
        return ToolCallExecution(result, durable)

    @staticmethod
    def _attach_artifact_references(
        result: ToolExecutionOutcome,
        references,
        *,
        result_limit: int,
    ) -> ToolExecutionOutcome:
        """Expose only opaque durable links in the v2 model projection.

        The durable coordinator owns publication and retention.  This small projection makes a
        published result fetchable by the v2 ``read_artifact`` tool without copying any artifact
        bytes into a second event or history record.
        """

        unique = []
        for reference in references:
            if reference not in unique:
                unique.append(reference)
        if not unique:
            return result
        try:
            payload = json.loads(result.envelope)
        except (TypeError, ValueError, json.JSONDecodeError):
            return result
        if not isinstance(payload, dict):
            return result
        serialized_refs = [reference.model_dump(mode="json") for reference in unique]
        body = payload.get("result")
        if isinstance(body, dict):
            updated = dict(body)
            existing = updated.get("artifact_refs")
            if isinstance(existing, list):
                serialized_refs = existing + [
                    item for item in serialized_refs if item not in existing
                ]
            updated["artifact_refs"] = serialized_refs
            candidate_payload = {**payload, "result": updated}
        elif "error" in payload:
            # Preserve a failed tool outcome as failed.  The reference is metadata for a
            # separately readable artifact, not permission to turn an error into success.
            candidate_payload = {**payload, "artifact_refs": serialized_refs}
        else:
            candidate_payload = {
                **payload,
                "result": {"artifact_refs": serialized_refs},
            }
        candidate = json.dumps(
            candidate_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        if len(candidate) <= result_limit:
            return replace(result, envelope=candidate, artifact_refs=tuple(unique))
        # Keep the reference usable even when the original result consumed the entire per-call
        # budget.  The omitted bytes remain in the managed Artifact, not in this fallback envelope.
        compact_payload = {
            "ok": bool(result.ok),
            "result": {
                "truncated": True,
                "original_chars": len(result.envelope),
                "content": "",
                "artifact_refs": serialized_refs,
            },
        }
        if not result.ok and isinstance(payload.get("error"), dict):
            compact_payload["error"] = payload["error"]
        compact = json.dumps(
            compact_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        if len(compact) > result_limit:
            return result
        return replace(
            result,
            envelope=compact,
            truncated=True,
            original_chars=len(result.envelope),
            artifact_refs=tuple(unique),
        )

    def _unknown_after_handler_entry(self, call: FunctionToolCall) -> bool:
        declaration = self.tool_executor.recovery_declaration(call.name)
        return declaration.missing_handler_completed in {
            MissingCompletionPolicy.OUTCOME_UNKNOWN,
            MissingCompletionPolicy.REQUIRES_RECONCILIATION,
        }

    async def await_with_cancellation(
        self,
        execution: Awaitable[ToolExecutionOutcome],
        session: Session,
        durable_execution: DurableToolExecution | None,
    ) -> ToolExecutionOutcome:
        task = asyncio.ensure_future(execution)
        try:
            while True:
                done, _pending = await asyncio.wait((task,), timeout=0.05)
                if done:
                    return await task
                current = self.reload_durable(session, durable_execution)
                if current is not None and current.cancel_requested_at is not None:
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
                    raise ToolCancellationRequested
        except asyncio.CancelledError:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            raise

    @staticmethod
    def reload_durable(
        session: Session, execution: DurableToolExecution | None
    ) -> DurableToolExecution | None:
        if execution is None or session.durable_runtime is None:
            return execution
        return session.durable_runtime.get_execution(execution.tool_execution_id) or execution

    async def _gate_durable(
        self,
        session: Session,
        execution: DurableToolExecution,
        call: FunctionToolCall,
        *,
        now: datetime,
        result_limit: int,
    ) -> tuple[DurableToolExecution, ToolExecutionOutcome | None]:
        coordinator = self._coordinator(session)
        if execution.intent.requires_approval:
            approval = coordinator.create_pending_approval(execution, now=now)
            registered = self.tool_executor.tool_set.tools.get(call.name)
            request = ToolApprovalRequest(
                call_id=call.id,
                effect=(
                    registered.execution_policy.effect
                    if registered is not None
                    else ToolEffect.NONE
                ),
                preview=execution.intent.preview,
                reason_codes=execution.intent.policy_reason_codes,
                approval_id=approval.approval_id,
            )
            decision = await self.tool_executor.request_approval(request)
            approved = bool(decision is not None and decision.approved)
            execution, _approval, run_handler = coordinator.consume_and_mark_executing(
                execution,
                approval,
                approved=approved,
                now=self.wall_now(session),
            )
            if run_handler:
                return execution, None
            denied = self.tool_executor.error_outcome(
                call,
                ToolErrorCode.APPROVAL_REJECTED,
                "工具操作未获批准",
                result_limit=result_limit,
            )
            return execution, denied
        return coordinator.mark_executing(execution, now=now), None

    def _has_active_unconfined_grant(
        self, session: Session, execution: DurableToolExecution
    ) -> bool:
        return self._coordinator(session).has_active_unconfined_grant(
            execution, now=self.wall_now(session)
        )

    def _permission_denied(self, call: FunctionToolCall, result_limit: int) -> ToolExecutionOutcome:
        return self.tool_executor.error_outcome(
            call,
            ToolErrorCode.PERMISSION_DENIED,
            "权限证据已撤销或不可证明",
            result_limit=result_limit,
        )

    @staticmethod
    def _coordinator(session: Session) -> DurableRunCoordinator:
        coordinator = session.durable_runtime
        if coordinator is None:
            raise RuntimeError("durable execution requires a durable runtime coordinator")
        return coordinator
