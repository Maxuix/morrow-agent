"""The single serial WorkflowScheduler every graph shape reuses.

Stage 7 admits one node at a time in stable Revision order. Each node binds its
pre-created queued NodeRun through AgentFactory and the existing AgentLoop; the
scheduler owns no chat history, no Tool execution and no second state machine —
terminal truth is always re-derived from durable leaf facts.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime

from morrow.application.agent_definitions.factory import AgentFactory
from morrow.application.recovery import RecoveryService
from morrow.application.turns import SessionPersistence
from morrow.application.workflows.finalizer import WorkflowOutcomeFinalizer
from morrow.application.workflows.leaf import WorkflowLeafContext, WorkflowLeafHooks
from morrow.application.workflows.recovery import WorkflowAbandonService
from morrow.application.workflows.submit import make_submit_node_result_tool
from morrow.application.workflows.tasks import WorkflowTaskLifecycle
from morrow.application.workflows.transitions import WorkflowTransitionService
from morrow.core.application import ApplicationError, ApplicationErrorCode
from morrow.core.capabilities import ProcessIsolation
from morrow.core.domain import DurableSession, DurableTaskRun, TaskRunPurpose, TaskRunStatus
from morrow.core.faults import InjectedFault
from morrow.core.models import FinishReason, utc_now
from morrow.core.workflows.contracts import (
    SUBMISSION_OUTPUT_KINDS,
    ArtifactBinding,
    ContractRef,
    parse_workflow_payload,
    workflow_payload_excerpt,
)
from morrow.core.workflows.definitions import AgentNode, WorkflowRevision
from morrow.core.workflows.runs import NodeRun, WorkflowRun, WorkflowStatus
from morrow.runtime.agent import AgentLoop
from morrow.runtime.durable_log import restore_conversation_log
from morrow.runtime.session import Session
from morrow.runtime.tools import ToolExecutor, ToolRegistry

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


def stable_execution_order(revision: WorkflowRevision) -> tuple[str, ...]:
    """One deterministic topological order over the frozen Revision DAG.

    Kahn's algorithm with a sorted ready set: among the currently admissible
    nodes the lexicographically smallest ``node_id`` runs first, so execution
    order depends only on frozen Revision data. The compiler already rejects
    cycles; a remainder here means corrupt durable evidence.
    """

    node_ids = sorted(node.node_id for node in revision.nodes)
    incoming = {node_id: 0 for node_id in node_ids}
    consumers: dict[str, list[str]] = {node_id: [] for node_id in node_ids}
    for edge in revision.edges:
        incoming[edge.to_node_id] += 1
        consumers[edge.from_node_id].append(edge.to_node_id)
    ready = sorted(node_id for node_id in node_ids if incoming[node_id] == 0)
    order: list[str] = []
    while ready:
        current = ready.pop(0)
        order.append(current)
        for target in sorted(consumers[current]):
            incoming[target] -= 1
            if incoming[target] == 0:
                ready.append(target)
        ready.sort()
    if len(order) != len(node_ids):
        raise ApplicationError(
            ApplicationErrorCode.NEEDS_RECOVERY,
            "Workflow revision edges no longer form a DAG; durable evidence is inconsistent",
        )
    return tuple(order)


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
        mutation=None,
        change_capture=None,
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
        self.mutation = mutation
        self.change_capture = change_capture
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
        node_defs = {node.node_id: node for node in revision.nodes}
        for node_id in stable_execution_order(revision):
            run = self._require_run(workflow_run_id)
            # BLOCKED is nonterminal but admits nothing until recovery resolves it.
            if run.status.terminal or run.status is WorkflowStatus.BLOCKED:
                break
            node_def = node_defs[node_id]
            node = self._node_for(run, node_def)
            if node.status is WorkflowStatus.COMPLETED:
                continue
            if node.status in (WorkflowStatus.FAILED, WorkflowStatus.CANCELLED):
                self.finalizer.finalize_failure(workflow_run_id, reason="node_failed")
                break
            gate = self._pre_admission_gate(run, revision, node_def, node)
            if gate is not None:
                break
            if node.status is WorkflowStatus.QUEUED:
                # Budget/deadline gate admission only. An admitted node's next
                # request is enforced at the durable purpose=agent seam, and a
                # crash replay needing no new request must still complete.
                remaining = (
                    run.budget_snapshot.max_agent_generation_requests
                    - self.journal.count_workflow_agent_requests(
                        self.workspace_id, run.workflow_run_id
                    )
                )
                if remaining <= 0:
                    self.finalizer.finalize_failure(workflow_run_id, reason="budget_exhausted")
                    break
                if self.clock() > run.admission_deadline_at:
                    self.finalizer.finalize_failure(workflow_run_id, reason="deadline_exceeded")
                    break
                nodes_by_id = self._nodes_by_id(run)
            else:
                remaining = None
            cap = (
                min(node_def.declared_node_max_agent_generation_requests, remaining)
                if remaining is not None
                else node.effective_node_generation_request_cap
            )
            try:
                if node.status is WorkflowStatus.QUEUED:
                    self._require_ready(run, revision, node_def, nodes_by_id)
                    self._bind_node_inputs(run, node_def, nodes_by_id)
                await self._drive_node(run, revision, node_def, node, cap)
                self._require_settled(run, node)
            except ApplicationError as exc:
                if exc.code is ApplicationErrorCode.NEEDS_RECOVERY:
                    # Corrupt durable evidence is a recovery signal, never a
                    # fabricated failure mapping.
                    raise
                reason = classify_terminal_reason(exc.message)
                if reason == "policy_revoked":
                    self.finalizer.finalize_cancel(workflow_run_id, reason="policy_revoked")
                else:
                    self.finalizer.finalize_failure(
                        workflow_run_id, reason=reason or "preparation_failed"
                    )
                break
            except InjectedFault:
                # A simulated crash leaves durable state exactly as committed;
                # recovery, not a fabricated failure mapping, owns the next step.
                raise
            except Exception:
                self.finalizer.finalize_failure(workflow_run_id, reason="node_failed")
                break
        return self._finalize_when_all_completed(workflow_run_id)

    def _require_settled(self, run: WorkflowRun, node: NodeRun) -> None:
        """A returned drive must leave its node terminal or the run blocked.

        Without this guard an exhausted resume loop would let the serial order
        advance while the node is still RUNNING, admitting a sibling whose
        predecessors completed on a fork.
        """

        current_run = self._require_run(run.workflow_run_id)
        if current_run.status.terminal or current_run.status is WorkflowStatus.BLOCKED:
            return
        current_node = self.transitions.get_node(node.node_run_id) or node
        if current_node.status.terminal or current_node.status is WorkflowStatus.BLOCKED:
            return
        self.finalizer.finalize_failure(run.workflow_run_id, reason="node_failed")

    def _finalize_when_all_completed(self, workflow_run_id: str) -> WorkflowRun:
        run = self._require_run(workflow_run_id)
        if not run.status.terminal and run.status is not WorkflowStatus.BLOCKED:
            nodes = self.journal.workflows.list_nodes(self.workspace_id, workflow_run_id)
            if nodes and all(node.status is WorkflowStatus.COMPLETED for node in nodes):
                # Crash-safe: a run whose last node completed but whose success
                # finalization was interrupted is finished here from durable facts.
                self.finalizer.finalize_success(workflow_run_id)
                run = self._require_run(workflow_run_id)
        return run

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

    def abandon(self, workflow_run_id: str, *, expected_row_version: int) -> WorkflowRun:
        """Recovery-only abandon of an OCC-current blocked run.

        Running/queued work is rejected towards its owning foreground
        cancellation, and an exact live handle still held by this process is
        rejected outright; liveness is never inferred from missing terminal
        facts or PID absence.
        """

        return WorkflowAbandonService(
            transitions=self.transitions,
            finalizer=self.finalizer,
        ).abandon(
            workflow_run_id,
            expected_row_version=expected_row_version,
            live_node_run_id=self._live_node_run_id,
        )

    async def _drive_node(
        self,
        run: WorkflowRun,
        revision: WorkflowRevision,
        node_def: AgentNode,
        node: NodeRun,
        cap: int,
    ) -> None:
        leaf_session_id, leaf_task_run_id = self._ensure_leaf(run, node_def, node)
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
            transitions=self.transitions,
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
            mutation=self.mutation,
            change_capture=self.change_capture,
        )
        root = self.journal.get_task_run(self.workspace_id, run.root_task_run_id)
        factory = AgentFactory(
            self.preparation,
            self.agent_publication,
            version_id=node_def.agent_definition_ref.version_id,
            session=session,
            task_run_id=leaf_task_run_id,
            invoking_session_id=root.session_id,
            conversation_scope=node_def.conversation_scope,
            resolved_tool_requirements=node_def.resolved_tool_requirements,
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
                    if node_def.conversation_scope == "invoking_session":
                        persistence.restore_into(session)
                    prepared = factory.prepare_new(
                        agent_run_id=self.id_source.new_id("arun"),
                        model=node_def.resolved_model_ref,
                        max_agent_generation_requests=cap,
                        require_enabled=False,
                    )
                    prepared = self._compose_leaf_runtime(prepared, hooks)
                    text = self._contract_text(run, node_def)
                    drive_error = await self._drive(
                        session,
                        text,
                        client_message_id=self._client_message_id(run, node_def, node),
                        prepared=prepared,
                    )
                elif (
                    leaf is not None
                    and leaf.status is TaskRunStatus.OPEN
                    and self.journal.has_open_turn_submission(self.workspace_id, leaf_session_id)
                    and self._leaf_report(node) is None
                ):
                    # Ordinary crash path: resume the open Turn from frozen evidence.
                    # Recovery resume re-checks one-way revocation of the frozen
                    # Revision and AgentDefinitionVersion before any replay.
                    self._require_unrevoked(revision, node_def)
                    # Bind the Definition's prompt assembler (via the factory)
                    # before restoring, so projection rebuilds never quarantine
                    # the leaf for lack of its prompt owner.
                    persistence.attach(session)
                    agent_run = self.journal.get_agent_run(self.workspace_id, node.agent_run_id)
                    prepared = factory.rehydrate(agent_run.snapshot, agent_run_id=node.agent_run_id)
                    prepared = self._compose_leaf_runtime(prepared, hooks)
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
                            client_message_id=self._client_message_id(run, node_def, node),
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
            self.finalizer.finalize_cancel(workflow_run_id, reason="policy_revoked")
            return "terminal"
        leaf = (
            self.journal.get_task_run(self.workspace_id, node.leaf_task_run_id)
            if node.leaf_task_run_id is not None
            else None
        )
        report = self._leaf_report(node) if node.conversation_session_id else None
        blocking = report is not None and any(item.blocking for item in report.items)
        user_cancel = cancelled or (leaf is not None and leaf.status is TaskRunStatus.CANCELLED)
        if blocking:
            self.finalizer.mark_blocked(workflow_run_id, user_cancel=user_cancel)
            return "blocked"
        if leaf is not None and leaf.status is TaskRunStatus.READY_FOR_ACCEPTANCE:
            # A committed leaf stays completed even when a cancellation arrives
            # afterwards; the cancel still stops every not-yet-started node.
            self.transitions.complete_node(node.node_run_id)
            nodes = self.journal.workflows.list_nodes(self.workspace_id, workflow_run_id)
            if all(item.status is WorkflowStatus.COMPLETED for item in nodes):
                # Success finalization waits for every declared node, not only
                # the exported-output producers.
                self.finalizer.finalize_success(workflow_run_id)
                return "terminal"
            if user_cancel:
                self.finalizer.finalize_cancel(workflow_run_id, reason="user_cancelled")
                return "terminal"
            return "continue"
        if user_cancel:
            self.finalizer.finalize_cancel(workflow_run_id, reason="user_cancelled")
            return "terminal"
        if leaf is not None and leaf.status is TaskRunStatus.FAILED:
            self.finalizer.finalize_failure(workflow_run_id, reason=reason or "node_failed")
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
        if not self._is_revoked(revision, node_def):
            return None
        # The finalizer cancels the queued node in the same transaction as the
        # run/root closure, so no cancellable intermediate state exists.
        self.finalizer.finalize_cancel(run.workflow_run_id, reason="policy_revoked")
        return "policy_revoked"

    def _is_revoked(self, revision: WorkflowRevision, node_def: AgentNode) -> bool:
        return (
            self.journal.workflows.get_revocation(self.workspace_id, revision.workflow_revision_id)
            is not None
            or self.journal.agent_definitions.get_revocation(
                self.workspace_id, node_def.agent_definition_ref.version_id
            )
            is not None
        )

    def _require_unrevoked(self, revision: WorkflowRevision, node_def: AgentNode) -> None:
        if self._is_revoked(revision, node_def):
            raise ApplicationError(
                ApplicationErrorCode.INVALID,
                "policy_revoked: the frozen Revision or AgentDefinitionVersion was revoked",
            )

    def _ensure_leaf(self, run: WorkflowRun, node_def: AgentNode, node: NodeRun) -> tuple[str, str]:
        if node_def.conversation_scope == "invoking_session":
            root = self.journal.get_task_run(self.workspace_id, run.root_task_run_id)
            if root is None:
                raise ApplicationError(
                    ApplicationErrorCode.NEEDS_RECOVERY,
                    "Workflow invoking root TaskRun is missing",
                )
            return root.session_id, root.task_run_id
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

    @staticmethod
    def _client_message_id(run: WorkflowRun, node_def: AgentNode, node: NodeRun) -> str:
        if node_def.conversation_scope == "invoking_session":
            if run.invoking_client_message_id is None:
                raise ApplicationError(
                    ApplicationErrorCode.NEEDS_RECOVERY,
                    "Direct Workflow client-message binding is missing",
                )
            return run.invoking_client_message_id
        return f"workflow:{node.node_run_id}"

    def _leaf_ownership(self, node_run_id: str) -> tuple[str, str] | None:
        return self.journal.workflows.get_leaf_ownership(self.workspace_id, node_run_id)

    def _nodes_by_id(self, run: WorkflowRun) -> dict[str, NodeRun]:
        nodes = self.journal.workflows.list_nodes(self.workspace_id, run.workflow_run_id)
        return {node.node_id: node for node in nodes}

    def _require_ready(
        self,
        run: WorkflowRun,
        revision: WorkflowRevision,
        node_def: AgentNode,
        nodes_by_id: dict[str, NodeRun],
    ) -> None:
        """Readiness is derived from the frozen graph and durable bindings.

        Every incoming-edge predecessor must be completed and every declared
        node-output input must already be bound to its producer's Artifact.
        A control-only edge orders nodes without inventing an Artifact. The
        serial order plus the fixed failure mapping make a violation here a
        durable-state inconsistency, never an optional-node skip.
        """

        for edge in revision.edges:
            if edge.to_node_id != node_def.node_id:
                continue
            predecessor = nodes_by_id[edge.from_node_id]
            if predecessor.status is not WorkflowStatus.COMPLETED:
                raise ApplicationError(
                    ApplicationErrorCode.NEEDS_RECOVERY,
                    f"node {node_def.node_id} is not ready: predecessor "
                    f"{edge.from_node_id} is {predecessor.status.value}",
                )
        bound_outputs = {
            (node_run_id, binding.name)
            for node_run_id, direction, binding in self.journal.workflows.list_bindings(
                self.workspace_id, run.workflow_run_id
            )
            if direction == "output"
        }
        for binding in node_def.input_bindings:
            if binding.source == "workflow_input":
                continue
            ref = binding.node_output
            producer = nodes_by_id[ref.node_id]
            if (producer.node_run_id, ref.output_slot) not in bound_outputs:
                raise ApplicationError(
                    ApplicationErrorCode.NEEDS_RECOVERY,
                    f"node {node_def.node_id} is not ready: input {binding.input_name} has "
                    "no bound producer Artifact",
                )

    def _bind_node_inputs(
        self,
        run: WorkflowRun,
        node_def: AgentNode,
        nodes_by_id: dict[str, NodeRun],
    ) -> None:
        """Durably bind every declared input before admission; replay is a no-op."""

        node_run_id = nodes_by_id[node_def.node_id].node_run_id
        bound_bindings: list[ArtifactBinding] = []
        for binding in node_def.input_bindings:
            if binding.source == "workflow_input":
                source = run.input_artifacts[0]
                bound = ArtifactBinding(
                    name=binding.input_name,
                    artifact_id=source.artifact_id,
                    contract=source.contract,
                )
            else:
                ref = binding.node_output
                producer = nodes_by_id[ref.node_id]
                produced = next(
                    produced
                    for nid, direction, produced in self.journal.workflows.list_bindings(
                        self.workspace_id, run.workflow_run_id
                    )
                    if nid == producer.node_run_id
                    and direction == "output"
                    and produced.name == ref.output_slot
                )
                bound = ArtifactBinding(
                    name=binding.input_name,
                    artifact_id=produced.artifact_id,
                    contract=ContractRef(
                        kind=binding.accepts.kind, version=binding.accepts.version
                    ),
                )
            bound_bindings.append(bound)
        self.transitions.bind_node_inputs(node_run_id, tuple(bound_bindings))

    def _compose_leaf_runtime(self, prepared, hooks: WorkflowLeafHooks):
        """Inject mechanism tools and freeze Coder bash to the native sandbox."""

        executor = prepared.tool_executor
        if executor is None:
            return prepared
        extra = []
        node = hooks.context.node
        if any(contract.kind in SUBMISSION_OUTPUT_KINDS for contract in node.output_contracts):
            extra.append(make_submit_node_result_tool(hooks, node.output_contracts))
        isolation = executor.expected_process_isolation
        patch_required = any(
            contract.kind == "ImplementationPatch" and contract.required_for_node_completion
            for contract in node.output_contracts
        )
        has_bash = "bash" in executor.tool_set.tools
        if patch_required and has_bash and isolation is ProcessIsolation.HOST:
            raise ApplicationError(
                ApplicationErrorCode.INVALID,
                "uncapturable_host_bash: complete ImplementationPatch cannot use Host-mode bash",
            )
        if patch_required and has_bash:
            isolation = ProcessIsolation.NATIVE_SANDBOX
        if not extra and isolation is executor.expected_process_isolation:
            return prepared
        registry = ToolRegistry()
        for tool in executor.tool_set.tools.values():
            registry.register(tool)
        for tool in extra:
            registry.register(tool)
        return replace(
            prepared,
            tool_executor=ToolExecutor(
                registry.snapshot(),
                executor.run_policy,
                approval_port=executor.approval_port,
                capability_policy=executor.capability_policy,
                expected_process_isolation=isolation,
            ),
        )

    def _contract_text(self, run: WorkflowRun, node_def: AgentNode) -> str:
        """Leaf input: its Node Task Contract plus the explicitly bound Artifacts.

        No other leaf's ConversationLog and no unbound Artifact is ever read.
        """

        from morrow.core.workflows.contracts import TaskContract

        if node_def.conversation_scope == "invoking_session":
            source = run.input_artifacts[0]
            stored = self.artifacts.get(source.artifact_id)
            read = self.artifacts.read(source.artifact_id, max_bytes=stored.byte_size)
            return self._format_contract(TaskContract.model_validate_json(read.content))

        nodes_by_id = self._nodes_by_id(run)
        parts = [self._format_contract(node_def.task_contract)]
        for binding in node_def.input_bindings:
            if binding.source == "workflow_input":
                source = run.input_artifacts[0]
                stored = self.artifacts.get(source.artifact_id)
                read = self.artifacts.read(source.artifact_id, max_bytes=stored.byte_size)
                contract = TaskContract.model_validate_json(read.content)
                parts.append(
                    f"Bound Workflow input '{binding.input_name}':\n"
                    + self._format_contract(contract)
                )
                continue
            ref = binding.node_output
            producer = nodes_by_id[ref.node_id]
            produced = next(
                item
                for nid, direction, item in self.journal.workflows.list_bindings(
                    self.workspace_id, run.workflow_run_id
                )
                if nid == producer.node_run_id
                and direction == "output"
                and item.name == ref.output_slot
            )
            stored = self.artifacts.get(produced.artifact_id)
            read = self.artifacts.read(produced.artifact_id, max_bytes=stored.byte_size)
            kind = produced.contract.kind
            if kind == "TaskContract":
                payload = TaskContract.model_validate_json(read.content)
                rendered = self._format_contract(payload)
            else:
                payload = parse_workflow_payload(kind, read.content)
                rendered = workflow_payload_excerpt(payload)
            parts.append(
                f"Bound input '{binding.input_name}' "
                f"(node '{ref.node_id}' slot '{ref.output_slot}'):\n{rendered}"
            )
        return "\n\n".join(parts)

    @staticmethod
    def _format_contract(contract) -> str:
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
