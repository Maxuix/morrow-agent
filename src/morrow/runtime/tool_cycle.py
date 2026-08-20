"""One tool-call execution collaborator with no ConversationLog or public-event ownership."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime

from morrow.core.capabilities import PolicyVerdict, ToolRunContext
from morrow.core.execution import (
    ApprovalDecisionError,
    DurableToolExecution,
    EffectClass,
    ExecutionTransitionError,
    ToolExecutionDisposition,
    ToolExecutionState,
)
from morrow.core.faults import FaultPoint
from morrow.core.models import FunctionToolCall, ToolApprovalRequest, ToolEffect
from morrow.core.permissions import PermissionEvidenceError
from morrow.runtime.policy import RunPolicy
from morrow.runtime.session import DurableRunCoordinator, Session
from morrow.runtime.tools import ToolErrorCode, ToolExecutionOutcome, ToolExecutor


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
        remaining_run_seconds: float,
    ) -> ToolCallExecution:
        durable = durable_execution
        skip_approval = durable is not None
        denied_result = None
        handler_disposition = None

        if durable is not None:
            coordinator = self._coordinator(session)
            if durable.intent.policy_verdict is PolicyVerdict.DENY:
                denied_result = self.tool_executor.error_outcome(
                    call,
                    ToolErrorCode.PERMISSION_DENIED,
                    "当前能力策略拒绝此操作",
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
                    call.name == "run_command"
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
                timeout = min(self.run_policy.tool_timeout_seconds, remaining_run_seconds)
                result = await asyncio.wait_for(
                    self.await_with_cancellation(execution, session, durable),
                    timeout=timeout,
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
        except ToolCancellationRequested:
            result = self.tool_executor.error_outcome(
                call,
                ToolErrorCode.CANCELLED,
                "工具执行已收到撤销请求",
                result_limit=result_limit,
            )
            durable = self.reload_durable(session, durable)
            if durable is not None and durable.state is ToolExecutionState.EXECUTING:
                if (
                    durable.tool_name == "run_command"
                    and durable.intent.effect_class is EffectClass.UNCONFINED_EXTERNAL_EFFECT
                ):
                    # The opaque Host process may already have taken effect.
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

        if durable is not None and durable.state is ToolExecutionState.EXECUTING:
            durable = self._coordinator(session).record_handler_completed(
                durable,
                result,
                now=self.wall_now(session),
                disposition=handler_disposition,
            )
        return ToolCallExecution(result, durable)

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
