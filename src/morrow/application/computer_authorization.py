"""Read-only desktop admission through the existing execution and permission evidence."""

from __future__ import annotations

from datetime import datetime

from morrow.core.computer_use import (
    ComputerUseContractError,
    SelectedWindowScope,
    computer_use_intent,
)
from morrow.core.execution import EffectClass, ToolExecutionState, assert_handler_may_enter
from morrow.core.permissions import CapabilityName, assert_grant_snapshot_matches


def authorize_computer_execution(
    journal,
    *,
    workspace_id: str,
    execution_id: str,
    scope: SelectedWindowScope,
    tool_name: str,
    now: datetime,
):
    execution = journal.get_execution(workspace_id, execution_id)
    specification = computer_use_intent(tool_name)
    if (
        execution is None
        or execution.state is not ToolExecutionState.EXECUTING
        or execution.tool_name != tool_name
        or execution.workspace_id != workspace_id
        or (execution.task_run_id, execution.agent_run_id)
        != (scope.task_run_id, scope.agent_run_id)
        or scope.workspace_id != workspace_id
        or not execution.permission_snapshot_id
        or not execution.grant_id
        or execution.intent.effect_class is not EffectClass(specification.effect_class)
        or execution.intent.requires_approval != specification.requires_approval
    ):
        raise ComputerUseContractError("execution_not_authorized")
    snapshot = journal.get_permission_snapshot(workspace_id, execution.permission_snapshot_id)
    grant = journal.get_capability_grant(workspace_id, execution.grant_id)
    run = journal.get_agent_run(workspace_id, scope.agent_run_id)
    if (
        snapshot is None
        or grant is None
        or run is None
        or run.permission_snapshot_id != execution.permission_snapshot_id
        or snapshot.computer_use_scope != scope
        or snapshot.session_id != execution.session_id
        or snapshot.turn_id != execution.turn_id
        or snapshot.workspace_read_only
        or CapabilityName.COMPUTER_USE_HOST not in snapshot.granted_capabilities
        or execution.isolation != snapshot.isolation_for_tool(tool_name)
    ):
        raise ComputerUseContractError("execution_not_authorized")
    try:
        assert_grant_snapshot_matches(
            snapshot,
            grant,
            now=now,
            workspace_id=workspace_id,
            task_run_id=scope.task_run_id,
            agent_run_id=scope.agent_run_id,
        )
    except ValueError:
        raise ComputerUseContractError("grant_inactive") from None
    approval = (
        journal.get_approval_for_execution(workspace_id, execution_id)
        if execution.intent.requires_approval
        else None
    )
    try:
        if (
            approval
            and (approval.granted_scope or "").startswith("session:")
            and not journal.session_scopes.active(workspace_id, execution.session_id, approval)
        ):
            raise ValueError("inactive_session_scope")
        assert_handler_may_enter(
            execution, approval, now=now, permission_snapshot=snapshot, grant=grant
        )
    except ValueError:
        code = (
            "execution_cancelled" if execution.cancel_requested_at else "execution_not_authorized"
        )
        raise ComputerUseContractError(code) from None
    return execution
