"""Unified session execution projection for the Chat surface.

One projection answers "is this Session executing, for whom, and what can the
user do about it" for ordinary chat, task-plan generation and Workflow runs
alike. Durable facts own the lifecycle; the in-process supervisor only
distinguishes a live driver from a durable record that needs reconciliation
after a restart — a stale ``running`` row is reported as ``needs_recovery``,
never as still executing. Terminal runs leave the projection: the timeline,
plan panel and run views already own completed outcomes.

The projection is advisory surface state. Every control action is still
validated by its own endpoint against durable facts.
"""

from __future__ import annotations

from morrow.core.execution import ApprovalResolution, ToolExecutionState
from morrow.core.workflows.runs import WorkflowStatus

PROTOCOL_VERSION = 1

# Lifecycle states reported per execution and on the top-level projection.
STATE_IDLE = "idle"
STATE_QUEUED = "queued"
STATE_RUNNING = "running"
STATE_STOPPING = "stopping"
STATE_PAUSING = "pausing"
STATE_PAUSED = "paused"
STATE_WAITING_APPROVAL = "waiting_approval"
STATE_NEEDS_RECOVERY = "needs_recovery"


class ExecutionProjection:
    """Builds the per-Session execution view appended to the Chat snapshot."""

    def __init__(
        self,
        *,
        manager,
        journal,
        workspace_id,
        supervisor=None,
        planning=None,
        chat_pause_enabled: bool = False,
    ):
        self.manager = manager
        self.journal = journal
        self.workspace_id = workspace_id
        self.supervisor = supervisor
        self.planning = planning
        # Chat-turn pause advertisement (P03/P04, lane D coordination): the
        # composition enables this when the chat pause endpoint and the chat
        # AgentLoop pause control are wired. Default off keeps the C0 chat
        # surface byte-identical.
        self.chat_pause_enabled = chat_pause_enabled

    # Public entry -------------------------------------------------------------

    def build(self, session_id):
        executions = []
        revisions = []
        runs = {}
        for run in self.journal.workflows.active_runs_for_root_session(
            self.workspace_id, session_id
        ):
            runs[run.workflow_run_id] = run
        for run in self.journal.workflows.active_runs_for_leaf_session(
            self.workspace_id, session_id
        ):
            runs.setdefault(run.workflow_run_id, run)
        for run in sorted(runs.values(), key=lambda item: item.workflow_run_id):
            executions.append(self._workflow_execution(run, session_id))
            revisions.append(run.row_version)
        planning = self._planning_execution(session_id)
        if planning is not None:
            executions.append(planning)
            revisions.append(planning.pop("_row_version", 0))
        chat = self._chat_execution(session_id)
        if chat is not None:
            executions.append(chat)
        updated_at = self.journal.now().isoformat()
        if not executions:
            return {
                "protocol_version": PROTOCOL_VERSION,
                "owner": None,
                "state": STATE_IDLE,
                "phase": None,
                "agent_run_id": None,
                "workflow_run_id": None,
                "root_session_id": None,
                "leaf": False,
                "node_run_ids": [],
                "planning_operation_id": None,
                "allowed_actions": [],
                "accepts_chat_input": True,
                "executions": [],
                "revision": self._queue_revision(session_id),
                "updated_at": updated_at,
            }
        return {
            **executions[0],
            "executions": executions,
            "revision": self._queue_revision(session_id) + sum(revisions),
            "updated_at": updated_at,
        }

    # Workflow ownership ---------------------------------------------------------

    def _workflow_execution(self, run, session_id):
        nodes = self.journal.workflows.list_nodes(self.workspace_id, run.workflow_run_id)
        node_run_ids = tuple(
            node.node_run_id
            for node in nodes
            if node.status in (WorkflowStatus.RUNNING, WorkflowStatus.BLOCKED)
        )
        root_session_id = None
        root = self.journal.get_task_run(self.workspace_id, run.root_task_run_id)
        if root is not None:
            root_session_id = root.session_id
        # A Direct (invoking_session) node drives on the root Session itself;
        # only a different isolated node conversation is execution detail.
        leaf = root_session_id != session_id and any(
            node.conversation_session_id == session_id for node in nodes
        )
        state = self._workflow_state(run)
        phase = "node_execution" if node_run_ids else "admission"
        actions = self._workflow_actions(run, state)
        return {
            "protocol_version": PROTOCOL_VERSION,
            "owner": "workflow",
            "state": state,
            "phase": phase,
            "agent_run_id": None,
            "workflow_run_id": run.workflow_run_id,
            "root_task_run_id": run.root_task_run_id,
            "root_session_id": root_session_id,
            "leaf": leaf,
            "node_run_ids": node_run_ids,
            "planning_operation_id": None,
            "allowed_actions": actions,
            # Isolated node conversations are execution detail; the root Session
            # keeps accepting ordinary input that queues behind the run.
            "accepts_chat_input": not leaf,
        }

    def _workflow_state(self, run):
        if run.pending_terminal_intent == "user_cancel":
            return STATE_STOPPING
        if run.status is WorkflowStatus.BLOCKED:
            return STATE_NEEDS_RECOVERY
        if run.status is WorkflowStatus.PAUSED:
            return STATE_PAUSED
        if run.status is WorkflowStatus.QUEUED:
            return STATE_QUEUED
        if run.pause_requested or run.status is WorkflowStatus.DRAINING:
            return STATE_PAUSING
        if self.supervisor is None or not self.supervisor.is_driving(run.workflow_run_id):
            # Durable running evidence without a confirmable live driver: after
            # a restart this row must invite reconciliation, never claim
            # progress on its own.
            return STATE_NEEDS_RECOVERY
        return STATE_RUNNING

    @staticmethod
    def _workflow_actions(run, state):
        if state in (STATE_STOPPING, STATE_NEEDS_RECOVERY):
            return []
        if state is STATE_PAUSED:
            return ["resume", "change"]
        actions = ["stop"]
        if not run.pause_requested:
            actions += ["pause", "change"]
        else:
            actions += ["change"]
        return actions

    # Planning ownership -----------------------------------------------------------

    def _planning_execution(self, session_id):
        if self.planning is None:
            return None
        operations = self.planning.repo.operations(self.workspace_id, session_id)
        open_operations = [
            operation for operation in operations if operation.status in ("queued", "running")
        ]
        if open_operations:
            operation = open_operations[-1]
            return self._planning_view(operation, STATE_RUNNING, ["stop"])
        # A cancel intent is durable while the in-flight Provider job still
        # has to settle; show stopping until the job leaves the registry.
        settling = [
            operation
            for operation in operations
            if operation.status == "cancelled" and self._planning_job_live(operation)
        ]
        if settling:
            return self._planning_view(settling[-1], STATE_STOPPING, [])
        return None

    def _planning_view(self, operation, state, actions):
        return {
            "protocol_version": PROTOCOL_VERSION,
            "owner": "planning",
            "state": state,
            "phase": "planning_model",
            "agent_run_id": None,
            "workflow_run_id": None,
            "root_task_run_id": None,
            "root_session_id": None,
            "leaf": False,
            "node_run_ids": [],
            "planning_operation_id": operation.planning_operation_id,
            "allowed_actions": actions,
            "accepts_chat_input": True,
            "_row_version": operation.row_version,
        }

    def _planning_job_live(self, operation):
        jobs = getattr(self.planning, "jobs", None)
        job = jobs.get(operation.planning_operation_id) if jobs else None
        return job is not None and not job.done()

    # Ordinary chat ownership --------------------------------------------------------

    def _chat_execution(self, session_id):
        interactions = getattr(self.manager, "interactions", None)
        if interactions is None:
            return None
        agent_run_id = interactions.active_run(session_id)
        paused = self._queue_paused(session_id)
        pending = self.journal.interactions.pending(self.workspace_id, session_id)
        pause_open = self._chat_pause_open(session_id)
        if agent_run_id:
            if session_id in interactions.user_stops:
                state = STATE_STOPPING
                actions = ["stop"]
            elif pause_open:
                # The pause intent is durably accepted and the turn is being
                # interrupted; the client follows the projection (D01/P09).
                state = STATE_PAUSING
                actions = []
            else:
                state = (
                    STATE_WAITING_APPROVAL if self._awaiting_approval(session_id) else STATE_RUNNING
                )
                actions = ["pause", "stop"] if self.chat_pause_enabled else ["stop"]
            return self._chat_view(agent_run_id, state, actions)
        if pause_open:
            # The interrupted turn has settled; the task waits for the user's
            # continue/correction, which the resume path accepts once (D07).
            return self._chat_view(None, STATE_PAUSED, ["resume"])
        if paused and pending:
            return self._chat_view(None, STATE_PAUSED, ["resume"])
        return None

    def _chat_pause_open(self, session_id):
        """True while a chat-turn pause cycle is accepted and not resumed."""
        if not self.chat_pause_enabled:
            return False
        pause_journal = self.journal.workflows.execution_pause
        point = pause_journal.latest_pause_point(
            self.workspace_id, owner="chat_turn", owner_id=session_id
        )
        return point is not None and point.fact.lifecycle in (
            "requested",
            "quiescing",
            "suspended",
        )

    @staticmethod
    def _chat_view(agent_run_id, state, actions):
        return {
            "protocol_version": PROTOCOL_VERSION,
            "owner": "chat",
            "state": state,
            "phase": None,
            "agent_run_id": agent_run_id,
            "workflow_run_id": None,
            "root_task_run_id": None,
            "root_session_id": None,
            "leaf": False,
            "node_run_ids": [],
            "planning_operation_id": None,
            "allowed_actions": actions,
            "accepts_chat_input": True,
        }

    def _awaiting_approval(self, session_id):
        for execution in self.journal.list_session_executions(self.workspace_id, session_id):
            if execution.state is not ToolExecutionState.AWAITING_APPROVAL:
                continue
            approval = self.journal.get_approval_for_execution(
                self.workspace_id, execution.tool_execution_id
            )
            if approval is not None and approval.resolution is ApprovalResolution.PENDING:
                return True
        return False

    def _queue_paused(self, session_id):
        control = self.journal.interactions.control(session_id)
        return bool(control["paused"])

    def _queue_revision(self, session_id):
        return int(self.journal.interactions.control(session_id)["revision"])
