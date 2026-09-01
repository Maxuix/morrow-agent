"""The single serial WorkflowScheduler every graph shape reuses.

Stage 7 admits one node at a time in stable Revision order. Each node binds its
pre-created queued NodeRun through AgentFactory and the existing AgentLoop; the
scheduler owns no chat history, no Tool execution and no second state machine —
terminal truth is always re-derived from durable leaf facts.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from morrow.application.agent_definitions.factory import AgentFactory
from morrow.application.recovery import RecoveryService
from morrow.application.turns import SessionPersistence
from morrow.application.workflows.finalizer import WorkflowOutcomeFinalizer
from morrow.application.workflows.leaf import WorkflowLeafContext, WorkflowLeafHooks
from morrow.application.workflows.tasks import WorkflowTaskLifecycle
from morrow.application.workflows.transitions import WorkflowTransitionService
from morrow.core.application import ApplicationError, ApplicationErrorCode
from morrow.core.domain import DurableSession, DurableTaskRun, TaskRunPurpose, TaskRunStatus
from morrow.core.faults import InjectedFault
from morrow.core.models import FinishReason, utc_now
from morrow.core.workflows.definitions import AgentNode, WorkflowRevision
from morrow.core.workflows.runs import NodeRun, WorkflowRun, WorkflowStatus
from morrow.runtime.agent import AgentLoop
from morrow.runtime.durable_log import restore_conversation_log
from morrow.runtime.session import Session

_REASON_PREFIXES = {
    "budget_exhausted",
    "deadline_exceeded",
    "output_contract_unsatisfied",
    "policy_revoked",
}
_MAX_RESUME_ATTEMPTS = 4


class _ClockAdapter:
    """SessionPersistence consumes a Clock; Workflow services take callables."""

    def __init__(self, value: Callable[[], datetime]) -> None:
        self._value = value

    def now(self) -> datetime:
        return self._value()


@dataclass(frozen=True)
class WorkflowNodeDrive:
    """Observable outcome of one scheduler drive, derived from durable facts."""

    run: WorkflowRun
    reason: str | None = None


def classify_terminal_reason(message: str | None) -> str | None:
    if not message:
        return None
    head = message.split(":", 1)[0].strip()
    return head if head in _REASON_PREFIXES else None


class WorkflowScheduler:
    def __init__(
        self,
        journal,
        *,
        workspace_id: str,
        id_source,
        store_session,
        runtime_instance_id: str,
        artifacts,
        transitions: WorkflowTransitionService,
        finalizer: WorkflowOutcomeFinalizer,
        agent_publication,
        preparation,
        clock: Callable[[], datetime] = utc_now,
        skill_selection=None,
        retry_sleep=None,
        faults=None,
    ) -> None:
        self.journal = journal
        self.workspace_id = workspace_id
        self.id_source = id_source
        self.store_session = store_session
        self.runtime_instance_id = runtime_instance_id
        self.artifacts = artifacts
        self.transitions = transitions
        self.finalizer = finalizer
        self.agent_publication = agent_publication
        self.preparation = preparation
        self.clock = clock
        self.skill_selection = skill_selection
        self.retry_sleep = retry_sleep
        self.faults = faults
        self.lifecycle = WorkflowTaskLifecycle(journal, workspace_id=workspace_id)
        self.recovery = RecoveryService(journal, workspace_id=workspace_id, id_source=id_source)
        self._live_node_run_id: str | None = None

    # Driving ------------------------------------------------------------------

    async def run(self, workflow_run_id: str) -> WorkflowRun:
        """Drive every not-yet-completed node in stable order; never reruns completed work."""

        run = self._require_run(workflow_run_id)
        if run.status.terminal:
            return run
        if run.status is WorkflowStatus.BLOCKED:
            raise ApplicationError(
                ApplicationErrorCode.NEEDS_RECOVERY,
                "Workflow is blocked; resolve its recovery items first",
            )
        revision = self.journal.workflows.get_revision(self.workspace_id, run.workflow_revision_id)
        for node_def in revision.nodes:
            run = self._require_run(workflow_run_id)
            if run.status.terminal:
                break
            node = self._node_for(run, node_def)
            if node.status is WorkflowStatus.COMPLETED:
                continue
            if node.status in (WorkflowStatus.FAILED, WorkflowStatus.CANCELLED):
                self.finalizer.finalize_failure(workflow_run_id, reason="node_failed")
                break
            gate = self._pre_admission_gate(run, revision, node_def, node)
            if gate is not None:
                break
            remaining = (
                run.budget_snapshot.max_agent_generation_requests
                - self.journal.count_workflow_agent_requests(self.workspace_id, run.workflow_run_id)
            )
            if remaining <= 0:
                self._cancel_unstarted(node)
                self.finalizer.finalize_failure(workflow_run_id, reason="budget_exhausted")
                break
            if self.clock() > run.admission_deadline_at:
                self._cancel_unstarted(node)
                self.finalizer.finalize_failure(workflow_run_id, reason="deadline_exceeded")
                break
            cap = min(node_def.declared_node_max_agent_generation_requests, remaining)
            try:
                await self._drive_node(run, revision, node_def, node, cap)
            except ApplicationError as exc:
                reason = classify_terminal_reason(exc.message)
                if reason == "policy_revoked":
                    self._cancel_unstarted(node)
                    self.finalizer.finalize_cancel(workflow_run_id, reason="policy_revoked")
                else:
                    self._cancel_unstarted(node)
                    self.finalizer.finalize_failure(
                        workflow_run_id, reason=reason or "preparation_failed"
                    )
                break
            except InjectedFault:
                # A simulated crash leaves durable state exactly as committed;
                # recovery, not a fabricated failure mapping, owns the next step.
                raise
            except Exception:
                self._cancel_unstarted(node)
                self.finalizer.finalize_failure(workflow_run_id, reason="node_failed")
                break
        return self._require_run(workflow_run_id)

    async def recover(self, workflow_run_id: str) -> WorkflowRun:
        """Finish cancel/recovery mappings from durable facts after reconciliation.

        Blocking unknown Tool outcomes keep the run blocked; a resolved
        user-cancel intent closes through the fixed cancellation mapping; an
        ordinary crash path resumes without rerunning completed work.
        """

        run = self._require_run(workflow_run_id)
        if run.status.terminal:
            return run
        nodes = self.journal.workflows.list_nodes(self.workspace_id, workflow_run_id)
        active = next(
            (
                node
                for node in nodes
                if node.status in (WorkflowStatus.RUNNING, WorkflowStatus.BLOCKED)
            ),
            None,
        )
        if active is None:
            return await self.run(workflow_run_id)
        report = self._leaf_report(active)
        if report is not None and any(item.blocking for item in report.items):
            return self.finalizer.mark_blocked(
                workflow_run_id, user_cancel=run.pending_terminal_intent == "user_cancel"
            )
        if run.pending_terminal_intent == "user_cancel":
            leaf = self.journal.get_task_run(self.workspace_id, active.leaf_task_run_id)
            if leaf is not None and leaf.status is TaskRunStatus.READY_FOR_ACCEPTANCE:
                self.transitions.complete_node(active.node_run_id)
            return self.finalizer.finalize_cancel(workflow_run_id, reason="user_cancelled")
        if active.status is WorkflowStatus.BLOCKED:
            self.transitions.resume_blocked_node(active.node_run_id)
        if run.status is WorkflowStatus.BLOCKED:
            self.transitions.resume_blocked_run(run.workflow_run_id)
        return await self.run(workflow_run_id)

    async def _drive_node(
        self,
        run: WorkflowRun,
        revision: WorkflowRevision,
        node_def: AgentNode,
        node: NodeRun,
        cap: int,
    ) -> None:
        leaf_session_id, leaf_task_run_id = self._ensure_leaf(run, node)
        hooks = WorkflowLeafHooks(
            self.journal,
            workspace_id=self.workspace_id,
            context=WorkflowLeafContext(
                workflow_run_id=run.workflow_run_id,
                workflow_revision_id=revision.workflow_revision_id,
                node_run_id=node.node_run_id,
                node=node_def,
                leaf_session_id=leaf_session_id,
                leaf_task_run_id=leaf_task_run_id,
                effective_node_generation_request_cap=cap,
            ),
            artifacts=self.artifacts,
            id_source=self.id_source,
            clock=self.clock,
        )
        session = Session(leaf_session_id)
        persistence = SessionPersistence(
            workspace_id=self.workspace_id,
            journal=self.journal,
            store_session=self.store_session,
            id_source=self.id_source,
            model=node_def.resolved_model_ref,
            run_policy=None,
            runtime_instance_id=self.runtime_instance_id,
            artifacts=self.artifacts,
            clock=_ClockAdapter(self.clock),
            skill_selection=self.skill_selection,
            workflow_leaf=hooks,
            faults=self.faults,
        )
        root = self.journal.get_task_run(self.workspace_id, run.root_task_run_id)
        factory = AgentFactory(
            self.preparation,
            self.agent_publication,
            version_id=node_def.agent_definition_ref.version_id,
            session=session,
            task_run_id=leaf_task_run_id,
            invoking_session_id=root.session_id,
        )
        error_message: str | None = None
        cancelled = False
        attempts = 0
        self._live_node_run_id = node.node_run_id
        try:
            while True:
                attempts += 1
                node = self.transitions.get_node(node.node_run_id)
                leaf = self.journal.get_task_run(self.workspace_id, leaf_task_run_id)
                if node.status is WorkflowStatus.QUEUED:
                    persistence.attach(session)
                    prepared = factory.prepare_new(
                        agent_run_id=self.id_source.new_id("arun"),
                        model=node_def.resolved_model_ref,
                        max_agent_generation_requests=cap,
                        require_enabled=False,
                    )
                    text = self._contract_text(run)
                    drive_error = await self._drive(
                        session,
                        text,
                        client_message_id=f"workflow:{node.node_run_id}",
                        prepared=prepared,
                    )
                elif (
                    leaf is not None
                    and leaf.status is TaskRunStatus.OPEN
                    and self.journal.has_open_turn_submission(self.workspace_id, leaf_session_id)
                    and self._leaf_report(node) is None
                ):
                    # Ordinary crash path: resume the open Turn from frozen evidence.
                    # Bind the Definition's prompt assembler (via the factory)
                    # before restoring, so projection rebuilds never quarantine
                    # the leaf for lack of its prompt owner.
                    persistence.attach(session)
                    agent_run = self.journal.get_agent_run(self.workspace_id, node.agent_run_id)
                    prepared = factory.rehydrate(agent_run.snapshot, agent_run_id=node.agent_run_id)
                    persistence.restore_into(session)
                    if self._committed_final_assistant(leaf_session_id):
                        # The final Assistant message is durable; replay only the
                        # terminal commit (committer re-runs from durable facts,
                        # never a second model request).
                        session.finish_turn(FinishReason.STOP)
                        drive_error = None
                    else:
                        drive_error = await self._drive(
                            session,
                            "",
                            client_message_id=f"workflow:{node.node_run_id}",
                            prepared=prepared,
                            resume=True,
                        )
                else:
                    drive_error = None
                if drive_error is not None:
                    error_message = drive_error
                settled = self._settle(run.workflow_run_id, node, error_message, cancelled)
                if settled != "resume" or attempts >= _MAX_RESUME_ATTEMPTS:
                    return
        except asyncio.CancelledError:
            cancelled = True
            self._settle(run.workflow_run_id, node, error_message, cancelled)
        finally:
            self._live_node_run_id = None

    async def _drive(
        self,
        session: Session,
        text: str,
        *,
        client_message_id: str,
        prepared,
        resume: bool = False,
    ) -> str | None:
        loop = AgentLoop(
            prepared.provider,
            prepared.model,
            prepared.context_builder,
            id_source=self.id_source,
            clock=_ClockAdapter(self.clock),
            tool_executor=prepared.tool_executor,
            runtime_control=None,
            retry_sleep=self.retry_sleep,
        )
        error_message = None
        stream = loop.run_task(
            session,
            text,
            client_message_id=client_message_id,
            resume_current_turn=resume,
            prepared=prepared,
        )
        try:
            async for event in stream:
                if event.type == "error":
                    error_message = event.payload.get("message") or error_message
        finally:
            await stream.aclose()
        return error_message

    # Terminal settlement --------------------------------------------------------

    def _settle(
        self,
        workflow_run_id: str,
        node: NodeRun,
        error_message: str | None,
        cancelled: bool,
    ) -> str:
        """Map durable leaf facts to the fixed terminal mapping; 'resume' continues."""

        reason = classify_terminal_reason(error_message)
        run = self._require_run(workflow_run_id)
        if run.status.terminal:
            return "terminal"
        node = self.transitions.get_node(node.node_run_id) or node
        if reason == "policy_revoked":
            self._cancel_unstarted(node)
            self.finalizer.finalize_cancel(workflow_run_id, reason="policy_revoked")
            return "terminal"
        leaf = (
            self.journal.get_task_run(self.workspace_id, node.leaf_task_run_id)
            if node.leaf_task_run_id is not None
            else None
        )
        if leaf is not None and leaf.status is TaskRunStatus.READY_FOR_ACCEPTANCE:
            self.finalizer.finalize_success(workflow_run_id)
            return "terminal"
        if leaf is not None and leaf.status is TaskRunStatus.FAILED:
            self.finalizer.finalize_failure(workflow_run_id, reason=reason or "node_failed")
            return "terminal"
        report = self._leaf_report(node) if node.conversation_session_id else None
        blocking = report is not None and any(item.blocking for item in report.items)
        user_cancel = cancelled or (leaf is not None and leaf.status is TaskRunStatus.CANCELLED)
        if blocking:
            self.finalizer.mark_blocked(workflow_run_id, user_cancel=user_cancel)
            return "blocked"
        if user_cancel:
            self.finalizer.finalize_cancel(workflow_run_id, reason="user_cancelled")
            return "terminal"
        if (
            leaf is not None
            and leaf.status is TaskRunStatus.OPEN
            and node.status is WorkflowStatus.RUNNING
            and self.journal.has_open_turn_submission(
                self.workspace_id, node.conversation_session_id
            )
        ):
            return "resume"
        self._cancel_unstarted(node)
        self.finalizer.finalize_failure(workflow_run_id, reason=reason or "node_failed")
        return "terminal"

    # Admission helpers ----------------------------------------------------------

    def _pre_admission_gate(
        self,
        run: WorkflowRun,
        revision: WorkflowRevision,
        node_def: AgentNode,
        node: NodeRun,
    ) -> str | None:
        """Revocation gate at every not-yet-started node admission."""

        if node.status is not WorkflowStatus.QUEUED:
            return None
        revoked = (
            self.journal.workflows.get_revocation(self.workspace_id, revision.workflow_revision_id)
            is not None
            or self.journal.agent_definitions.get_revocation(
                self.workspace_id, node_def.agent_definition_ref.version_id
            )
            is not None
        )
        if not revoked:
            return None
        self._cancel_unstarted(node)
        self.finalizer.finalize_cancel(run.workflow_run_id, reason="policy_revoked")
        return "policy_revoked"

    def _cancel_unstarted(self, node: NodeRun) -> None:
        current = self.transitions.get_node(node.node_run_id)
        if current is not None and current.status is WorkflowStatus.QUEUED:
            self.transitions.cancel_node(node.node_run_id)

    def _ensure_leaf(self, run: WorkflowRun, node: NodeRun) -> tuple[str, str]:
        owned = self._leaf_ownership(node.node_run_id)
        if owned is not None:
            return owned
        session_id = self.id_source.new_id("ses")
        task_run_id = self.id_source.new_id("task")
        stamp = self.clock()
        self.lifecycle.create_leaf(
            node.node_run_id,
            DurableSession(
                session_id=session_id,
                workspace_id=self.workspace_id,
                created_at=stamp,
                updated_at=stamp,
            ),
            DurableTaskRun(
                task_run_id=task_run_id,
                session_id=session_id,
                workspace_id=self.workspace_id,
                purpose=TaskRunPurpose.WORKFLOW_NODE,
                created_at=stamp,
                updated_at=stamp,
            ),
        )
        return session_id, task_run_id

    def _leaf_ownership(self, node_run_id: str) -> tuple[str, str] | None:
        return self.journal.workflows.get_leaf_ownership(self.workspace_id, node_run_id)

    def _contract_text(self, run: WorkflowRun) -> str:
        from morrow.core.workflows.contracts import TaskContract

        binding = run.input_artifacts[0]
        stored = self.artifacts.get(binding.artifact_id)
        read = self.artifacts.read(binding.artifact_id, max_bytes=stored.byte_size)
        contract = TaskContract.model_validate_json(read.content)
        parts = [contract.objective]
        if contract.scope:
            parts.append("Scope:\n" + "\n".join(f"- {item}" for item in contract.scope))
        if contract.constraints:
            parts.append("Constraints:\n" + "\n".join(f"- {item}" for item in contract.constraints))
        return "\n\n".join(parts)

    def _leaf_report(self, node: NodeRun):
        if node.conversation_session_id is None:
            return None
        log = restore_conversation_log(
            self.journal, self.workspace_id, node.conversation_session_id
        )
        return self.recovery.discover(node.conversation_session_id, log)

    def _committed_final_assistant(self, leaf_session_id: str) -> bool:
        log = restore_conversation_log(self.journal, self.workspace_id, leaf_session_id)
        turns = log.snapshot().public_turns()
        return bool(turns and turns[-1].final_assistant is not None and not turns[-1].is_closed)

    def _require_run(self, workflow_run_id: str) -> WorkflowRun:
        run = self.transitions.get_run(workflow_run_id)
        if run is None:
            raise ApplicationError(ApplicationErrorCode.NOT_FOUND, "WorkflowRun is missing")
        return run

    def _node_for(self, run: WorkflowRun, node_def: AgentNode) -> NodeRun:
        nodes = self.journal.workflows.list_nodes(self.workspace_id, run.workflow_run_id)
        return next(node for node in nodes if node.node_id == node_def.node_id)
