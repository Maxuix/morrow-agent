"""The sole writer of WorkflowRun/NodeRun state.

Every transition loads the current row, derives the next value and saves it
under OCC through the existing journal guards. Service-level idempotency makes
duplicate commands and recovery replays safe no-ops; the journal remains the
authority for legality, immutability and completion preconditions.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from morrow.core.application import ApplicationError, ApplicationErrorCode
from morrow.core.models import utc_now
from morrow.core.workflows.contracts import ArtifactBinding
from morrow.core.workflows.runs import NodeRun, WorkflowRun, WorkflowStatus


class WorkflowTransitionService:
    def __init__(self, journal, *, workspace_id: str, clock: Callable[[], datetime] = utc_now):
        self.journal = journal
        self.workspace_id = workspace_id
        self.clock = clock

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
        effective_node_generation_request_cap: int,
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
                "row_version": current.row_version + 1,
            }
        )
        return self.journal.workflows.save_node(updated, expected_row_version=current.row_version)

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

    def bind_node_inputs(self, node_run_id: str, bindings: tuple[ArtifactBinding, ...]) -> None:
        """Durably bind one node's declared input Artifacts in one transaction.

        The journal remains the authority: it verifies each declared binding,
        the exact producer output and the immutable-binding rule, so replay of
        an identical set is a no-op and a fault can never leave a partial
        input set behind.
        """

        if not bindings:
            return
        node = self._require_node(node_run_id)

        def work(txn) -> None:
            for binding in bindings:
                txn.workflows.bind_artifact(
                    self.workspace_id,
                    node.workflow_run_id,
                    binding,
                    node_run_id=node_run_id,
                    direction="input",
                )

        self.journal.transact(work)

    def _node_terminal(self, node_run_id: str, target: WorkflowStatus) -> NodeRun:
        return self._node_to(node_run_id, target, terminal=True)

    def _node_to(
        self, node_run_id: str, target: WorkflowStatus, *, terminal: bool = False
    ) -> NodeRun:
        current = self._require_node(node_run_id)
        if current.status is target:
            return current
        update = {"status": target, "row_version": current.row_version + 1}
        if terminal:
            update["completed_at"] = self.clock()
        return self.journal.workflows.save_node(
            current.model_copy(update=update), expected_row_version=current.row_version
        )

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
        return self.journal.workflows.save_run(updated, expected_row_version=current.row_version)

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
        return self.journal.workflows.save_run(updated, expected_row_version=current.row_version)

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
        return self.journal.workflows.save_run(updated, expected_row_version=current.row_version)

    def resume_blocked_run(self, workflow_run_id: str) -> WorkflowRun:
        current = self._require_run(workflow_run_id)
        if current.status is WorkflowStatus.RUNNING:
            return current
        updated = current.model_copy(
            update={"status": WorkflowStatus.RUNNING, "row_version": current.row_version + 1}
        )
        return self.journal.workflows.save_run(updated, expected_row_version=current.row_version)

    # Pause/Drain control -----------------------------------------------------

    def request_pause(self, workflow_run_id: str) -> WorkflowRun:
        """Record the durable pause fact; a running run starts draining.

        The blocked form records only the fact and leaves unknown evidence
        untouched; a pending user-cancel intent owns its blocked run instead.
        """

        current = self._require_run(workflow_run_id)
        if current.pause_requested:
            return current
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
            update["status"] = WorkflowStatus.DRAINING
        elif current.status is WorkflowStatus.QUEUED:
            update["status"] = WorkflowStatus.PAUSED
        elif current.status is not WorkflowStatus.BLOCKED:
            raise ApplicationError(
                ApplicationErrorCode.INVALID,
                f"a {current.status.value} Workflow cannot be paused",
            )
        return self.journal.workflows.save_run(
            current.model_copy(update=update), expected_row_version=current.row_version
        )

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
        return self.journal.workflows.save_run(
            current.model_copy(update=update), expected_row_version=current.row_version
        )

    def complete_drain(self, workflow_run_id: str) -> WorkflowRun:
        """A draining run with no Active nodes becomes paused.

        No admission can commit while the pause fact stands, so the active-node
        read and the status write cannot race a queued→running flip.
        """

        current = self._require_run(workflow_run_id)
        if current.status is not WorkflowStatus.DRAINING:
            return current
        nodes = self.journal.workflows.list_nodes(self.workspace_id, workflow_run_id)
        if any(node.status in (WorkflowStatus.RUNNING, WorkflowStatus.BLOCKED) for node in nodes):
            return current
        updated = current.model_copy(
            update={"status": WorkflowStatus.PAUSED, "row_version": current.row_version + 1}
        )
        return self.journal.workflows.save_run(updated, expected_row_version=current.row_version)

    def resume_blocked_run_to_draining(self, workflow_run_id: str) -> WorkflowRun:
        """Recovery resolve-success on a paused blocked run drains instead of running."""

        current = self._require_run(workflow_run_id)
        if current.status is WorkflowStatus.DRAINING:
            return current
        updated = current.model_copy(
            update={"status": WorkflowStatus.DRAINING, "row_version": current.row_version + 1}
        )
        return self.journal.workflows.save_run(updated, expected_row_version=current.row_version)

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
        return self.journal.workflows.save_run(updated, expected_row_version=current.row_version)

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
        return self.journal.workflows.save_run(updated, expected_row_version=current.row_version)

    def _require_run(self, workflow_run_id: str) -> WorkflowRun:
        current = self.journal.workflows.get_run(self.workspace_id, workflow_run_id)
        if current is None:
            raise ValueError("WorkflowRun is missing")
        return current
