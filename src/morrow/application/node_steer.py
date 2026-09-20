"""Durable targeted node-steering commands (master plan P6.2–P6.4).

The dedicated control owner for node steer commands: root-session
authorization, exact node binding (running node of the workflow run at the
executing graph revision), idempotent receipts keyed by the caller's command
id, at-most-once consumption at the leaf's request boundaries and completion
expiry. The ordinary chat runtime-control queue is never touched.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from morrow.core.application import ApplicationError, ApplicationErrorCode
from morrow.core.runtime_control import (
    RuntimeControlError,
)
from morrow.core.workflows.runs import WorkflowStatus


class NodeSteerService:
    def __init__(
        self,
        journal,
        *,
        workspace_id: str,
        id_source,
        clock: Callable[[], datetime],
    ) -> None:
        self.journal = journal
        self.workspace_id = workspace_id
        self.id_source = id_source
        self.clock = clock

    def node_steer(
        self,
        session_id: str,
        *,
        workflow_run_id: str,
        node_run_id: str,
        text: str,
        command_id: str,
        expected_revision: str | None = None,
    ) -> dict:
        """Authorize, bind and durably enqueue one node steer command."""
        run = self.journal.workflows.get_run(self.workspace_id, workflow_run_id)
        if run is None:
            raise ApplicationError(ApplicationErrorCode.NOT_FOUND, "workflow run is missing")
        root_task = self.journal.get_task_run(self.workspace_id, run.root_task_run_id)
        if root_task is None or root_task.session_id != session_id:
            raise ApplicationError(
                ApplicationErrorCode.CROSS_WORKSPACE,
                "only the invoking root session may steer a node",
            )
        if expected_revision is not None and run.workflow_revision_id != expected_revision:
            raise ApplicationError(
                ApplicationErrorCode.CONFLICT,
                "node steer targets a stale graph revision",
            )
        node = self.journal.workflows.get_node(self.workspace_id, node_run_id)
        if node is None or node.workflow_run_id != workflow_run_id:
            raise ApplicationError(
                ApplicationErrorCode.NOT_FOUND, "node run is outside this workflow run"
            )
        if node.status is not WorkflowStatus.RUNNING:
            raise ApplicationError(
                ApplicationErrorCode.INVALID, "only a running node can be steered"
            )
        leaf_session_id = node.conversation_session_id
        if not leaf_session_id:
            raise ApplicationError(
                ApplicationErrorCode.INVALID, "node has no deliverable leaf session"
            )
        existing = self.journal.get_node_steer(self.workspace_id, leaf_session_id, command_id)
        if existing is not None:
            return self.replay_or_conflict(leaf_session_id, command_id, text)
        return self.enqueue(
            leaf_session_id=leaf_session_id,
            workflow_run_id=workflow_run_id,
            node_run_id=node_run_id,
            node_id=node.node_id,
            graph_revision_id=run.workflow_revision_id,
            text=text,
            client_message_id=command_id,
        )

    def enqueue(
        self,
        *,
        leaf_session_id: str,
        workflow_run_id: str,
        node_run_id: str,
        node_id: str | None,
        graph_revision_id: str,
        text: str,
        client_message_id: str,
    ) -> dict:
        try:
            entry = self.journal.enqueue_node_steer(
                self.workspace_id,
                session_id=leaf_session_id,
                workflow_run_id=workflow_run_id,
                node_run_id=node_run_id,
                node_id=node_id,
                graph_revision_id=graph_revision_id,
                client_message_id=client_message_id,
                text=text,
                created_at=self.clock(),
            )
        except RuntimeControlError as exc:
            raise ApplicationError(exc.code.value, exc.args[0]) from exc
        return {
            "disposition": "accepted",
            "leaf_session_id": leaf_session_id,
            "node_run_id": node_run_id,
            "workflow_run_id": workflow_run_id,
            "client_message_id": entry["client_message_id"],
            "entry_status": entry["status"],
        }

    def replay_or_conflict(self, leaf_session_id: str, command_id: str, text: str) -> dict:
        existing = self.journal.get_node_steer(self.workspace_id, leaf_session_id, command_id)
        if existing is None:
            raise ApplicationError(ApplicationErrorCode.NOT_FOUND, "node steer entry is missing")
        if existing["text"] != text:
            raise ApplicationError(
                ApplicationErrorCode.CONFLICT,
                "node steer command id conflicts with an existing entry",
            )
        return {
            "disposition": "replay",
            "leaf_session_id": leaf_session_id,
            "node_run_id": existing["node_run_id"],
            "workflow_run_id": existing["workflow_run_id"],
            "client_message_id": existing["client_message_id"],
            "entry_status": existing["status"],
        }

    # --- leaf drive consumption seam (AgentLoop duck-type, P6.3) ---

    def peek_steering(self, session_id: str) -> dict | None:
        return self.journal.peek_pending_node_steer(self.workspace_id, session_id)

    def consume_first_steering(self, session_id: str) -> dict | None:
        entry = self.peek_steering(session_id)
        if entry is None:
            return None
        return self.journal.consume_node_steer(
            self.workspace_id,
            session_id,
            entry["client_message_id"],
            consumed_at=self.clock(),
        )

    def supersede_steering(self, session_id: str) -> int:
        return self.journal.expire_node_steers(self.workspace_id, session_id)
