"""Durable interruption safe points, committed alongside the owning Turn."""

from morrow.core.contracts import PauseIntentFact
from morrow.core.execution_pause import PauseSafetyPoint, WorkflowPausePoint
from morrow.core.models import AgentStopCode


def save_chat_interruption(txn, *, workspace_id, session_id, state, terminal, now):
    repo = txn.workflows.execution_pause
    point = repo.latest_pause_point(workspace_id, owner="chat_turn", owner_id=session_id)
    if point is None or point.fact.lifecycle == "resumed" or point.cancelled_at is not None:
        generation = repo.next_control_generation(
            workspace_id, owner="chat_turn", owner_id=session_id
        )
        reason = (
            "user_interrupt"
            if terminal.stop_code is AgentStopCode.USER_PAUSE
            else "process_interrupt"
            if terminal.stop_code is AgentStopCode.PROCESS_INTERRUPTED
            else "provider_failure"
        )
        point = repo.insert_pause_point(
            WorkflowPausePoint(
                pause_point_id=f"pause_{state.turn_id}",
                workspace_id=workspace_id,
                fact=PauseIntentFact(
                    control_generation=generation,
                    command_id=f"interrupt_{state.turn_id}",
                    owner="chat_turn",
                    owner_id=session_id,
                    reason=reason,
                    requested_at=now,
                ),
                created_at=now,
                updated_at=now,
            )
        )
    requests = (
        txn.list_model_requests(workspace_id, state.agent_run_id) if state.agent_run_id else ()
    )
    safety = PauseSafetyPoint(
        interrupted_request_id=requests[-1].model_request_id if requests else None,
        task_run_id=state.task_run_id,
        committed_position=terminal.sequence,
        interrupted_turn_id=state.turn_id,
        interrupted_agent_run_id=state.agent_run_id,
        stop_code=terminal.stop_code,
        closed_tool_call_ids=terminal.interrupted_call_ids[:64],
    )
    repo.save_pause_point(
        point.model_copy(
            update={
                "fact": point.fact.model_copy(
                    update={"lifecycle": "suspended", "suspended_at": now}
                ),
                "safety": safety,
                "updated_at": now,
                "row_version": point.row_version + 1,
            }
        ),
        expected_row_version=point.row_version,
    )
    txn.interactions.pause(session_id)


def chat_continuation_point(journal, workspace_id, session_id):
    point = journal.workflows.execution_pause.latest_pause_point(
        workspace_id, owner="chat_turn", owner_id=session_id
    )
    if (
        point is None
        or point.cancelled_at is not None
        or point.fact.lifecycle != "suspended"
        or point.safety is None
    ):
        return None
    task = journal.get_task_run(workspace_id, point.safety.task_run_id or "")
    if task is None or task.status.value != "open":
        return None
    return point
