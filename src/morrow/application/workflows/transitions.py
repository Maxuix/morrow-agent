"""The sole writer of WorkflowRun/NodeRun state.

Every transition loads the current row, derives the next value and saves it
under OCC through the existing journal guards. Service-level idempotency makes
duplicate commands and recovery replays safe no-ops; the journal remains the
authority for legality, immutability and completion preconditions.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from morrow.core.application import (
    WORKFLOW_NODE_STATUS_EVENT,
    WORKFLOW_RUN_STATUS_EVENT,
    ApplicationError,
    ApplicationErrorCode,
)
from morrow.core.models import utc_now
from morrow.core.workflows.runs import NodeRun, WorkflowRun, WorkflowStatus

EventSink = Callable[[str, str, str, dict], None]


class WorkflowTransitionService:
    def __init__(
        self,
        journal,
        *,
        workspace_id: str,
        clock: Callable[[], datetime] = utc_now,
        event_sink: EventSink | None = None,
    ) -> None:
        self.journal = journal
        self.workspace_id = workspace_id
        self.clock = clock
        # Optional projection seam with bounded id/status facts. If a caller is
        # already in a larger transaction, the event row joins that transaction
        # and its subscriber hint is deferred until the outer commit.
        self.event_sink = event_sink
        # Optional in-process pause wake (P04): the scheduler injects its
        # NodePauseControl so every pause acceptance path — plan-change, run
        # commands, replan review — wakes live leaf drivers immediately. The
        # durable pause fact stays the authority; the hint is best-effort.
        self.pause_control = None

    def _emit_run(self, run: WorkflowRun) -> None:
        if self.event_sink is None:
            return
        self.event_sink(
            WORKFLOW_RUN_STATUS_EVENT,
            "workflow_run",
            run.workflow_run_id,
            {
                "status": run.status.value,
                "pause_requested": run.pause_requested,
                "result_status": run.result_status,
                "pending_terminal_intent": run.pending_terminal_intent,
                "row_version": run.row_version,
            },
        )

    def _emit_node(self, node: NodeRun) -> None:
        if self.event_sink is None:
            return
        self.event_sink(
            WORKFLOW_NODE_STATUS_EVENT,
            "workflow_node",
            node.node_run_id,
            {
                "workflow_run_id": node.workflow_run_id,
                "node_id": node.node_id,
                "status": node.status.value,
                "row_version": node.row_version,
            },
        )

    # NodeRun transitions ------------------------------------------------------

    def get_node(self, node_run_id: str) -> NodeRun | None:
        return self.journal.workflows.get_node(self.workspace_id, node_run_id)

    def admit_node(
        self,
        node_run_id: str,
        *,
        conversation_session_id: str,
        leaf_task_run_id: str,
        agent_run_id: str,
        effective_node_generation_request_cap: int | None,
        parallel_read_digest: str | None = None,
    ) -> NodeRun:
        """Bind the pre-created queued NodeRun's leaf references and start it."""

        current = self._require_node(node_run_id)
        if current.status is not WorkflowStatus.QUEUED:
            if (
                current.conversation_session_id == conversation_session_id
                and current.leaf_task_run_id == leaf_task_run_id
                and current.agent_run_id == agent_run_id
                and current.effective_node_generation_request_cap
                == effective_node_generation_request_cap
                and current.parallel_read_digest == parallel_read_digest
            ):
                return current
            raise ValueError("admitted NodeRun evidence conflicts with the queued row")
        updated = current.model_copy(
            update={
                "status": WorkflowStatus.RUNNING,
                "started_at": self.clock(),
                "conversation_session_id": conversation_session_id,
                "leaf_task_run_id": leaf_task_run_id,
                "agent_run_id": agent_run_id,
                "effective_node_generation_request_cap": effective_node_generation_request_cap,
                "parallel_read_digest": parallel_read_digest,
                "row_version": current.row_version + 1,
            }
        )
        updated = self.journal.workflows.save_node(
            updated, expected_row_version=current.row_version
        )
        self._emit_node(updated)
        return updated

    def complete_node(self, node_run_id: str) -> NodeRun:
        return self._node_terminal(node_run_id, WorkflowStatus.COMPLETED)

    def fail_node(self, node_run_id: str) -> NodeRun:
        return self._node_terminal(node_run_id, WorkflowStatus.FAILED)

    def cancel_node(self, node_run_id: str) -> NodeRun:
        return self._node_terminal(node_run_id, WorkflowStatus.CANCELLED)

    def block_node(self, node_run_id: str) -> NodeRun:
        return self._node_to(node_run_id, WorkflowStatus.BLOCKED)

    def resume_blocked_node(self, node_run_id: str) -> NodeRun:
        return self._node_to(node_run_id, WorkflowStatus.RUNNING)

    def _node_terminal(self, node_run_id: str, target: WorkflowStatus) -> NodeRun:
        if target is WorkflowStatus.COMPLETED:
            return self._complete_node_settling_segment(node_run_id)
        return self._node_to(node_run_id, target, terminal=True)

    def _complete_node_settling_segment(self, node_run_id: str) -> NodeRun:
        """Complete the node and settle its active segment in one transaction.

        No other code path closes a segment as ``completed``: without this
        settle, the ending execution segment of every completed node lingers
        as ``active`` forever, so run views cannot show a terminal segment and
        a completed node still looks continuable to the pause/continuation
        admission path.
        """

        def work(txn) -> tuple[NodeRun, bool]:
            current = txn.workflows.get_node(self.workspace_id, node_run_id)
            if current is None:
                raise ValueError("Workflow NodeRun is missing")
            if current.status is WorkflowStatus.COMPLETED:
                return current, False
            updated = txn.workflows.save_node(
                current.model_copy(
                    update={
                        "status": WorkflowStatus.COMPLETED,
                        "row_version": current.row_version + 1,
                        "completed_at": self.clock(),
                    }
                ),
                expected_row_version=current.row_version,
            )
            segment = txn.workflows.current_segment(node_run_id)
            if segment is not None and segment.status == "active":
                txn.workflows.close_segment(
                    self.workspace_id,
                    segment.segment_id,
                    status="completed",
                    expected_row_version=segment.row_version,
                )
            return updated, True

        updated, changed = self.journal.transact(work)
        if changed:
            # Mirrors _node_to: an idempotent re-complete must not emit a
            # second completion event.
            self._emit_node(updated)
        return updated

    def _node_to(
        self, node_run_id: str, target: WorkflowStatus, *, terminal: bool = False
    ) -> NodeRun:
        current = self._require_node(node_run_id)
        if current.status is target:
            return current
        update = {"status": target, "row_version": current.row_version + 1}
        if terminal:
            update["completed_at"] = self.clock()
        updated = self.journal.workflows.save_node(
            current.model_copy(update=update), expected_row_version=current.row_version
        )
        self._emit_node(updated)
        return updated

    def _require_node(self, node_run_id: str) -> NodeRun:
        current = self.journal.workflows.get_node(self.workspace_id, node_run_id)
        if current is None:
            raise ValueError("Workflow NodeRun is missing")
        return current

    # WorkflowRun transitions --------------------------------------------------

    def get_run(self, workflow_run_id: str) -> WorkflowRun | None:
        return self.journal.workflows.get_run(self.workspace_id, workflow_run_id)

    def mark_run_running(self, workflow_run_id: str) -> WorkflowRun:
        current = self._require_run(workflow_run_id)
        if current.status is WorkflowStatus.RUNNING:
            return current
        updated = current.model_copy(
            update={
                "status": WorkflowStatus.RUNNING,
                "row_version": current.row_version + 1,
            }
        )
        updated = self.journal.workflows.save_run(updated, expected_row_version=current.row_version)
        self._emit_run(updated)
        return updated

    def complete_run(self, workflow_run_id: str, *, result_status: str) -> WorkflowRun:
        current = self._require_run(workflow_run_id)
        if current.status is WorkflowStatus.COMPLETED:
            if current.result_status != result_status:
                raise ValueError("completed Workflow result cannot change")
            return current
        updated = current.model_copy(
            update={
                "status": WorkflowStatus.COMPLETED,
                "result_status": result_status,
                "completed_at": self.clock(),
                "row_version": current.row_version + 1,
            }
        )
        updated = self.journal.workflows.save_run(updated, expected_row_version=current.row_version)
        self._emit_run(updated)
        return updated

    def fail_run(self, workflow_run_id: str) -> WorkflowRun:
        return self._run_terminal(workflow_run_id, WorkflowStatus.FAILED)

    def cancel_run(self, workflow_run_id: str) -> WorkflowRun:
        return self._run_terminal(workflow_run_id, WorkflowStatus.CANCELLED)

    def block_run(self, workflow_run_id: str) -> WorkflowRun:
        current = self._require_run(workflow_run_id)
        if current.status is WorkflowStatus.BLOCKED:
            return current
        updated = current.model_copy(
            update={"status": WorkflowStatus.BLOCKED, "row_version": current.row_version + 1}
        )
        updated = self.journal.workflows.save_run(updated, expected_row_version=current.row_version)
        self._emit_run(updated)
        return updated

    def resume_blocked_run(self, workflow_run_id: str) -> WorkflowRun:
        current = self._require_run(workflow_run_id)
        if current.status is WorkflowStatus.RUNNING:
            return current
        updated = current.model_copy(
            update={"status": WorkflowStatus.RUNNING, "row_version": current.row_version + 1}
        )
        updated = self.journal.workflows.save_run(updated, expected_row_version=current.row_version)
        self._emit_run(updated)
        return updated

    # Pause/Drain control -----------------------------------------------------

    def request_pause(
        self,
        workflow_run_id: str,
        *,
        command_id: str | None = None,
        reason: str = "user_interrupt",
    ) -> WorkflowRun:
        """Record the durable pause fact; a running run starts draining.

        A user pause also durably accepts one ``PauseIntentFact`` cycle in the
        same transaction, keyed by ``command_id`` when the caller has one.
        Internal boundaries pass ``reason="node_boundary"`` and never wake
        drivers. The blocked form records only the fact and leaves unknown
        evidence untouched; a pending user-cancel intent owns its blocked run
        instead.
        """

        def work(txn) -> WorkflowRun:
            current = txn.workflows.get_run(self.workspace_id, workflow_run_id)
            if current is None:
                raise ValueError("WorkflowRun is missing")
            if current.pause_requested:
                if current.status is not WorkflowStatus.DRAINING:
                    return current
                nodes = txn.workflows.list_nodes(self.workspace_id, workflow_run_id)
                if any(
                    node.status in (WorkflowStatus.RUNNING, WorkflowStatus.BLOCKED)
                    for node in nodes
                ):
                    return current
                update = {
                    "status": WorkflowStatus.PAUSED,
                    "row_version": current.row_version + 1,
                }
                return txn.workflows.save_run(
                    current.model_copy(update=update), expected_row_version=current.row_version
                )
            if current.status.terminal:
                raise ApplicationError(
                    ApplicationErrorCode.INVALID, "a terminal Workflow cannot be paused"
                )
            if (
                current.status is WorkflowStatus.BLOCKED
                and current.pending_terminal_intent == "user_cancel"
            ):
                raise ApplicationError(
                    ApplicationErrorCode.INVALID,
                    "a Workflow with a pending user cancellation cannot be paused",
                )
            update: dict = {"pause_requested": True, "row_version": current.row_version + 1}
            if current.status is WorkflowStatus.RUNNING:
                nodes = txn.workflows.list_nodes(self.workspace_id, workflow_run_id)
                active = any(
                    node.status in (WorkflowStatus.RUNNING, WorkflowStatus.BLOCKED)
                    for node in nodes
                )
                update["status"] = WorkflowStatus.DRAINING if active else WorkflowStatus.PAUSED
            elif current.status is WorkflowStatus.QUEUED:
                update["status"] = WorkflowStatus.PAUSED
            elif current.status is not WorkflowStatus.BLOCKED:
                raise ApplicationError(
                    ApplicationErrorCode.INVALID,
                    f"a {current.status.value} Workflow cannot be paused",
                )
            updated = txn.workflows.save_run(
                current.model_copy(update=update), expected_row_version=current.row_version
            )
            if command_id is not None:
                from morrow.core.contracts import PauseIntentFact
                from morrow.core.execution_pause import PauseIntentRequest, WorkflowPausePoint

                request = PauseIntentRequest(
                    command_id=command_id,
                    owner="workflow_run",
                    owner_id=workflow_run_id,
                    reason=reason,
                )
                existing = txn.workflows.execution_pause.find_pause_point_by_command(
                    self.workspace_id, request
                )
                if existing is None:
                    generation = txn.workflows.execution_pause.next_control_generation(
                        self.workspace_id, owner="workflow_run", owner_id=workflow_run_id
                    )
                    now = self.clock()
                    txn.workflows.execution_pause.insert_pause_point(
                        WorkflowPausePoint(
                            pause_point_id=f"pause_{workflow_run_id}_{generation}",
                            workspace_id=self.workspace_id,
                            fact=PauseIntentFact(
                                control_generation=generation,
                                command_id=command_id,
                                owner="workflow_run",
                                owner_id=workflow_run_id,
                                reason=reason,
                                lifecycle="requested",
                                requested_at=now,
                            ),
                            node_run_id=None,
                            created_at=now,
                            updated_at=now,
                        )
                    )
            return updated

        before = self._require_run(workflow_run_id)
        updated = self.journal.transact(work)
        if updated.row_version != before.row_version:
            self._emit_run(updated)
            if reason in {"user_interrupt", "provider_failure", "process_interrupt"}:
                self._wake_drivers(workflow_run_id)
        return updated

    def _wake_drivers(self, workflow_run_id: str) -> None:
        """Best-effort in-process wake after the durable pause fact commits."""
        control = self.pause_control
        if control is None:
            return
        try:
            control.wake_run(workflow_run_id)
        except Exception:
            # A missed hint only delays the wake; the durable pause fact and
            # the driver's next control check stay authoritative.
            return

    def resume_run(self, workflow_run_id: str) -> WorkflowRun:
        """Atomically clear the pause fact; a paused/draining run runs again.

        A blocked run stays blocked — recovery still owns its resolution — but
        loses the pause fact, so a later resolve-success returns to running.
        """

        current = self._require_run(workflow_run_id)
        if not current.pause_requested:
            return current
        if current.status.terminal:
            raise ApplicationError(
                ApplicationErrorCode.INVALID, "a terminal Workflow cannot resume"
            )
        update: dict = {"pause_requested": False, "row_version": current.row_version + 1}
        if current.status in (WorkflowStatus.PAUSED, WorkflowStatus.DRAINING):
            update["status"] = WorkflowStatus.RUNNING
        elif current.status is not WorkflowStatus.BLOCKED:
            raise ApplicationError(
                ApplicationErrorCode.INVALID,
                f"a {current.status.value} Workflow cannot resume",
            )
        updated = self.journal.workflows.save_run(
            current.model_copy(update=update), expected_row_version=current.row_version
        )
        self._emit_run(updated)
        return updated

    def complete_drain(self, workflow_run_id: str) -> WorkflowRun:
        """A draining run with no Active nodes becomes paused.

        With execution segments (P02/P04), a user-paused RUNNING node whose
        current segment is already ``interrupted`` has parked at its safe
        point: it no longer blocks the drain. A RUNNING node with an active
        segment still does.
        """

        current = self._require_run(workflow_run_id)
        if current.status is not WorkflowStatus.DRAINING:
            return current
        nodes = self.journal.workflows.list_nodes(self.workspace_id, workflow_run_id)
        for node in nodes:
            if node.status not in (WorkflowStatus.RUNNING, WorkflowStatus.BLOCKED):
                continue
            if node.status is WorkflowStatus.BLOCKED:
                return current
            segments = self.journal.workflows.segments_for_node(self.workspace_id, node.node_run_id)
            latest = segments[-1] if segments else None
            if latest is None or latest.status == "active":
                return current
        updated = current.model_copy(
            update={"status": WorkflowStatus.PAUSED, "row_version": current.row_version + 1}
        )
        updated = self.journal.workflows.save_run(updated, expected_row_version=current.row_version)
        self._emit_run(updated)
        return updated

    def resume_blocked_run_to_draining(self, workflow_run_id: str) -> WorkflowRun:
        """Recovery resolve-success on a paused blocked run drains instead of running."""

        current = self._require_run(workflow_run_id)
        if current.status is WorkflowStatus.DRAINING:
            return current
        updated = current.model_copy(
            update={"status": WorkflowStatus.DRAINING, "row_version": current.row_version + 1}
        )
        updated = self.journal.workflows.save_run(updated, expected_row_version=current.row_version)
        self._emit_run(updated)
        return updated

    def set_pending_user_cancel(self, workflow_run_id: str) -> WorkflowRun:
        """Record the one narrow pending terminal intent; it can never be cleared."""

        current = self._require_run(workflow_run_id)
        if current.pending_terminal_intent == "user_cancel":
            return current
        updated = current.model_copy(
            update={
                "pending_terminal_intent": "user_cancel",
                "row_version": current.row_version + 1,
            }
        )
        updated = self.journal.workflows.save_run(updated, expected_row_version=current.row_version)
        self._emit_run(updated)
        return updated

    def _run_terminal(self, workflow_run_id: str, target: WorkflowStatus) -> WorkflowRun:
        current = self._require_run(workflow_run_id)
        if current.status is target:
            return current
        updated = current.model_copy(
            update={
                "status": target,
                "completed_at": self.clock(),
                "row_version": current.row_version + 1,
            }
        )
        updated = self.journal.workflows.save_run(updated, expected_row_version=current.row_version)
        self._emit_run(updated)
        return updated

    def _require_run(self, workflow_run_id: str) -> WorkflowRun:
        current = self.journal.workflows.get_run(self.workspace_id, workflow_run_id)
        if current is None:
            raise ValueError("WorkflowRun is missing")
        return current
