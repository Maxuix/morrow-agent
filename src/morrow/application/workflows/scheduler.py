"""The single WorkflowScheduler every graph shape reuses.

Stable serial admission is the default; explicitly bounded read-only frontiers
may overlap after their frozen runtime and permission proofs pass. Each node binds its
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
from morrow.application.node_steer import NodeSteerService
from morrow.application.recovery import RecoveryService
from morrow.application.turns import SessionPersistence
from morrow.application.workflows.finalizer import (
    RESULT_NEEDS_REVISION,
    WorkflowOutcomeFinalizer,
    compute_workflow_result,
)
from morrow.application.workflows.leaf import (
    SegmentContinuation,
    WorkflowLeafContext,
    WorkflowLeafHooks,
)
from morrow.application.workflows.outputs import EffectiveOutputResolver
from morrow.application.workflows.parallel import (
    ReadFrontier,
    guard_read_executor,
    prove_read_runtime,
    read_contract_error,
)
from morrow.application.workflows.recovery import WorkflowAbandonService
from morrow.application.workflows.submit import make_submit_node_result_tool
from morrow.application.workflows.tasks import WorkflowTaskLifecycle
from morrow.application.workflows.transitions import WorkflowTransitionService
from morrow.core.application import ApplicationError, ApplicationErrorCode
from morrow.core.capabilities import ProcessIsolation
from morrow.core.domain import DurableSession, DurableTaskRun, TaskRunPurpose, TaskRunStatus
from morrow.core.faults import InjectedFault
from morrow.core.models import AgentStopCode, FinishReason, utc_now
from morrow.core.permissions import workspace_root_digest
from morrow.core.workflows.contracts import (
    RESULT_DRIVING_KIND,
    SUBMIT_SCHEMA_VERSION,
    ArtifactBinding,
    ContractRef,
    node_output_artifact_id,
    parse_workflow_payload,
    workflow_payload_excerpt,
)
from morrow.core.workflows.definitions import AgentNode, WorkflowRevision
from morrow.core.workflows.replan import (
    AffectedTaskFact,
    ReplanEvidenceRef,
    ReplanRequest,
    ReplanSignal,
)
from morrow.core.workflows.runs import NodeRun, WorkflowRun, WorkflowStatus
from morrow.runtime.agent import AgentLoop
from morrow.runtime.durable_log import restore_conversation_log
from morrow.runtime.session import Session
from morrow.runtime.tools import ToolContractAuditError, ToolExecutor, ToolRegistry

_REASON_PREFIXES = {
    "budget_exhausted",
    "deadline_exceeded",
    "output_contract_unsatisfied",
    "policy_revoked",
    "read_contract_drift",
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
        preference_loader=None,
        agent_preference_loader=None,
        initialize_context=None,
        retry_sleep=None,
        faults=None,
        mutation=None,
        change_capture=None,
        event_observer=None,
        activity_observer=None,
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
        self.preference_loader = preference_loader
        self.agent_preference_loader = agent_preference_loader
        self.initialize_context = initialize_context
        self.retry_sleep = retry_sleep
        self.faults = faults
        self.mutation = mutation
        self.change_capture = change_capture
        # Application-layer progress seam; composition wires it to a stream
        # projection. Never set by default, and observation never aborts work.
        self.event_observer = event_observer
        self.activity_observer = activity_observer
        # Leaf steering seam (P6.3): the same durable control queue as ordinary
        # chat, consumed by the leaf's AgentLoop at request boundaries.
        self.leaf_runtime_control = NodeSteerService(
            journal,
            workspace_id=workspace_id,
            id_source=id_source,
            clock=self.clock,
        )
        # Durable pause authority for leaf drives (P03/P04): the AgentLoop's
        # optional control seam. The pause acceptance path calls wake_run so a
        # driver waiting on the first model token wakes immediately.
        from morrow.application.execution_pause import NodePauseControl

        self.pause_control = NodePauseControl(journal, workspace_id)
        # One shared wake bus: every pause acceptance (run commands, plan
        # change, replan review) wakes live leaf drivers through the sole
        # pause-fact writer.
        self.transitions.pause_control = self.pause_control
        self.replan = None
        self.outputs = EffectiveOutputResolver(journal, workspace_id=workspace_id)
        self.lifecycle = WorkflowTaskLifecycle(journal, workspace_id=workspace_id)
        self.recovery = RecoveryService(journal, workspace_id=workspace_id, id_source=id_source)
        self._live_node_run_id: str | None = None
        self._live_node_run_ids: set[str] = set()

    # Driving ------------------------------------------------------------------

    async def run(self, workflow_run_id: str, *, cancelled_is_user: bool = True) -> WorkflowRun:
        """Drive every not-yet-completed node in stable order; never reruns completed work."""

        run = self._require_run(workflow_run_id)
        if run.status.terminal:
            return run
        if run.status is WorkflowStatus.BLOCKED:
            raise ApplicationError(
                ApplicationErrorCode.NEEDS_RECOVERY,
                "Workflow is blocked; resolve its recovery items first",
            )
        if run.status is WorkflowStatus.PAUSED:
            raise ApplicationError(
                ApplicationErrorCode.INVALID,
                "Workflow is paused; resume it before driving again",
            )
        revision = self.journal.workflows.get_revision(self.workspace_id, run.workflow_revision_id)
        node_defs = {node.node_id: node for node in revision.nodes}
        execution_ids = {
            item.node_id
            for item in self.journal.workflows.list_execution_nodes(
                self.workspace_id, workflow_run_id
            )
        }
        execution_order = tuple(
            item for item in stable_execution_order(revision) if item in execution_ids
        )
        # A crash may interrupt the admission barrier after a later sibling was
        # admitted but before an earlier one. Drain/recover the admitted cohort
        # before considering queued nodes, retaining every completed leaf.
        active_reads = tuple(
            (node_defs[node.node_id], node)
            for node in self.journal.workflows.list_nodes(self.workspace_id, workflow_run_id)
            if node.status is WorkflowStatus.RUNNING and node.parallel_read_digest is not None
        )
        if active_reads:
            await self._drive_frontier(
                run, revision, active_reads, cancelled_is_user=cancelled_is_user
            )
        for node_id in execution_order:
            if self.replan is not None:
                self.replan.process_signals(workflow_run_id)
                await self.replan.dispatch_pending()
            run = self._require_run(workflow_run_id)
            # BLOCKED admits nothing until recovery resolves it, PAUSED waits for
            # the user's resume, and DRAINING settles Active nodes without ever
            # admitting a queued one.
            if run.status.terminal or run.status in (
                WorkflowStatus.BLOCKED,
                WorkflowStatus.PAUSED,
            ):
                break
            node_def = node_defs[node_id]
            node = self._node_for(run, node_def)
            if node.status is WorkflowStatus.COMPLETED:
                continue
            frontier = self._ready_read_frontier(run, revision, execution_order, node_id)
            if len(frontier) > 1:
                await self._drive_frontier(
                    run, revision, frontier, cancelled_is_user=cancelled_is_user
                )
                continue
            if node.status in (WorkflowStatus.FAILED, WorkflowStatus.CANCELLED):
                self.finalizer.finalize_failure(workflow_run_id, reason="node_failed")
                break
            gate = self._pre_admission_gate(run, revision, node_def, node)
            if gate is not None:
                break
            if node.status is WorkflowStatus.QUEUED:
                if run.status is WorkflowStatus.DRAINING:
                    continue
                # Early deterministic refusal avoids preparing a Provider that
                # cannot be called. The durable model-request admission seam
                # repeats this lineage-wide check atomically and remains the
                # authority when concurrent callers race this projection.
                workflow_cap = run.budget_snapshot.max_agent_generation_requests
                remaining = (
                    workflow_cap
                    - self.journal.count_lineage_agent_requests(
                        self.workspace_id, run.effective_lineage_budget_root_run_id
                    )
                    if workflow_cap is not None
                    else None
                )
                if remaining is not None and remaining <= 0:
                    self.finalizer.finalize_failure(workflow_run_id, reason="budget_exhausted")
                    break
                if (
                    run.admission_deadline_at is not None
                    and self.clock() > run.admission_deadline_at
                ):
                    self.finalizer.finalize_failure(workflow_run_id, reason="deadline_exceeded")
                    break
                nodes_by_id = self._nodes_by_id(run)
                finite_caps = tuple(
                    value
                    for value in (
                        node_def.declared_node_max_agent_generation_requests,
                        remaining,
                    )
                    if value is not None
                )
                cap = min(finite_caps) if finite_caps else None
            else:
                cap = node.effective_node_generation_request_cap
            try:
                if node.status is WorkflowStatus.QUEUED:
                    self._require_ready(run, revision, node_def, nodes_by_id)
                if cancelled_is_user:
                    await self._drive_node(run, revision, node_def, node, cap)
                else:
                    await self._drive_node(
                        run,
                        revision,
                        node_def,
                        node,
                        cap,
                        cancelled_is_user=False,
                    )
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
        if self.replan is not None:
            self.replan.process_signals(workflow_run_id)
            await self.replan.dispatch_pending()
        self._finalize_when_all_completed(workflow_run_id)
        return self._settle_drain(workflow_run_id)

    def _ready_read_frontier(self, run, revision, order, first):
        if run.status not in {WorkflowStatus.QUEUED, WorkflowStatus.RUNNING}:
            return ()
        if run.budget_snapshot.max_concurrency < 2:
            return ()
        nodes = self._nodes_by_id(run)
        if any(
            n.status in {WorkflowStatus.RUNNING, WorkflowStatus.BLOCKED} for n in nodes.values()
        ):
            return ()
        definitions = {n.node_id: n for n in revision.nodes}
        frontier = []
        for node_id in order[order.index(first) :]:
            node = nodes[node_id]
            if node.status is WorkflowStatus.COMPLETED:
                continue
            definition = definitions[node_id]
            # Keep the stable serial boundary: never jump past a Writer or a
            # dependency that has not completed at this fixed frontier.
            if (
                node.status is not WorkflowStatus.QUEUED
                or definition.access_mode != "read"
                or definition.conversation_scope != "isolated"
                or any(
                    edge.to_node_id == node_id
                    and edge.from_node_id in nodes
                    and nodes[edge.from_node_id].status is not WorkflowStatus.COMPLETED
                    for edge in revision.edges
                )
            ):
                break
            self._require_ready(run, revision, definition, nodes)
            frontier.append((definition, node))
            if len(frontier) == run.budget_snapshot.max_concurrency:
                break
        return tuple(frontier)

    async def _drive_frontier(self, run, revision, entries, *, cancelled_is_user):
        frontier = ReadFrontier(tuple(definition.node_id for definition, _ in entries))
        if all(node.parallel_read_digest is not None for _, node in entries):
            frontier.proofs = {d.node_id: n.parallel_read_digest for d, n in entries}
            frontier.ready.set()
            frontier.admitted.set()
        outcomes = {}

        async def drive(definition, node):
            try:
                if node.status is WorkflowStatus.QUEUED:
                    self._require_unrevoked(revision, definition)
                error = await self._drive_node(
                    run,
                    revision,
                    definition,
                    node,
                    definition.declared_node_max_agent_generation_requests,
                    cancelled_is_user=cancelled_is_user,
                    frontier=frontier,
                )
                outcomes[node.node_id] = error
                current = self.transitions.get_node(node.node_run_id)
                leaf = self.journal.get_task_run(self.workspace_id, current.leaf_task_run_id)
                return leaf is not None and leaf.status is TaskRunStatus.READY_FOR_ACCEPTANCE
            except InjectedFault:
                frontier.crashed = True
                raise
            except ToolContractAuditError:
                raise read_contract_error() from None
            except ApplicationError as exc:
                if exc.code is ApplicationErrorCode.NEEDS_RECOVERY:
                    frontier.crashed = True
                raise
            finally:
                frontier.finished[definition.node_id].set()

        tasks = [asyncio.create_task(drive(d, n)) for d, n in entries]
        cancelled = False
        crash = None
        pending = set(tasks)
        try:
            while pending:
                done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
                if any(task.cancelled() or task.exception() or not task.result() for task in done):
                    current = self._require_run(run.workflow_run_id)
                    interrupted = None
                    interrupted_node: NodeRun | None = None
                    for _, admitted_node in entries:
                        current_node = self.transitions.get_node(admitted_node.node_run_id)
                        if current_node is None:
                            continue
                        interrupted = self._latest_interruption(
                            current_node.conversation_session_id
                        )
                        if interrupted is not None:
                            interrupted_node = current_node
                            break
                    if interrupted is not None and not current.pause_requested:
                        reason = (
                            "process_interrupt"
                            if interrupted.stop_code is AgentStopCode.PROCESS_INTERRUPTED
                            else "provider_failure"
                        )
                        self.transitions.request_pause(
                            run.workflow_run_id,
                            # Key the pause cycle by the interrupted terminal
                            # itself: a run-wide constant id would make a second
                            # interruption reuse the first (already resumed)
                            # pause point and fail the suspend.
                            command_id=(
                                f"auto_interrupt_{interrupted_node.node_run_id}"
                                f"_{interrupted.sequence}"
                                if interrupted_node is not None
                                else f"auto_interrupt_{run.workflow_run_id}"
                            ),
                            reason=reason,
                        )
                        # The durable pause authority wakes every sibling. Let
                        # each leaf close its own Turn and segment so no active
                        # continuation is stranded behind the frontier barrier.
                        continue
                    if current.pause_requested and current.status is WorkflowStatus.DRAINING:
                        # Pause drain: a finished sibling parks at its own
                        # suspended segment; every remaining sibling observes
                        # the same durable pause and settles on its own.
                        # Cancelling it here would strand an active segment
                        # and block the drain forever (BUG-GUI-002).
                        continue
                    frontier.controlled_cancel = True
                    for task in tasks:
                        if not task.done():
                            task.cancel()
                    break
        except asyncio.CancelledError:
            cancelled = True
            if not cancelled_is_user:
                frontier.crashed = True
            for task in tasks:
                if not task.done():
                    task.cancel()
        joined = asyncio.gather(*tasks, return_exceptions=True)
        while True:
            try:
                results = await asyncio.shield(joined)
                break
            except asyncio.CancelledError:
                cancelled = True
                if not cancelled_is_user:
                    frontier.crashed = True
                # Repeated foreground cancellation cannot interrupt leaf cleanup.
        for (definition, node), result in zip(entries, results, strict=True):
            if isinstance(result, InjectedFault):
                crash = result
            elif isinstance(result, ApplicationError):
                if result.code is ApplicationErrorCode.NEEDS_RECOVERY:
                    crash = result
                outcomes[definition.node_id] = result.message
                if classify_terminal_reason(result.message) == "read_contract_drift":
                    self.transitions.fail_node(node.node_run_id)
        if cancelled and not cancelled_is_user:
            raise asyncio.CancelledError
        if crash is not None:
            raise crash
        # A parallel leaf returns before the serial settlement path so the
        # admission barrier can join every sibling.  When one of those leaves
        # closed its Turn with a typed model/process interruption, settle that
        # durable fact before deciding whether the frontier failed.  Otherwise
        # the run finalizer can close the root while the interrupted leaf
        # remains OPEN, losing the continuation safe point for that sibling.
        current = self._require_run(run.workflow_run_id)
        if not current.status.terminal and any(
            self._latest_interruption(
                (self.transitions.get_node(node.node_run_id) or node).conversation_session_id
            )
            is not None
            for _, node in entries
        ):
            for definition, node in entries:
                current_node = self.transitions.get_node(node.node_run_id)
                if current_node is not None and current_node.status is WorkflowStatus.RUNNING:
                    self._settle(
                        run.workflow_run_id,
                        current_node,
                        outcomes.get(definition.node_id),
                        False,
                    )
            current = self._require_run(run.workflow_run_id)
        # Preserve every successfully committed leaf, even if a sibling failed
        # or the user cancelled while this output waited behind the barrier.
        for definition, node in entries:
            self._publish_completed_leaf(run, definition, node)
        current = self._require_run(run.workflow_run_id)
        if current.status.terminal:
            return
        nodes = [self.transitions.get_node(node.node_run_id) for _, node in entries]
        if any(
            (report := self._leaf_report(node)) is not None
            and any(item.blocking for item in report.items)
            for node in nodes
            if node.conversation_session_id
        ):
            self.finalizer.mark_blocked(run.workflow_run_id, user_cancel=cancelled)
            return
        reason = next(
            (
                classify_terminal_reason(outcomes.get(d.node_id))
                for d, _ in entries
                if classify_terminal_reason(outcomes.get(d.node_id))
            ),
            None,
        )
        if cancelled or reason == "policy_revoked":
            self.finalizer.finalize_cancel(run.workflow_run_id, reason=reason or "user_cancelled")
        elif current.pause_requested:
            # Pause drain: every interrupted sibling parks at its own
            # suspended segment (or defers a mid-drain error to the resumed
            # continuation, matching the single-node settle path). Completing
            # the drain — never a failure mapping — owns the paused transition.
            # Nodes the outer loop already consumed via this frontier never
            # get their own re-drive iteration, so settle them here: each
            # settle closes its own segment, which is exactly the per-node
            # barrier complete_drain waits on.
            for definition, node in entries:
                current_node = self.transitions.get_node(node.node_run_id)
                if current_node is not None and current_node.status is WorkflowStatus.RUNNING:
                    self._settle(
                        run.workflow_run_id,
                        current_node,
                        outcomes.get(definition.node_id),
                        False,
                    )
            return
        elif all(n.status in {WorkflowStatus.COMPLETED, WorkflowStatus.QUEUED} for n in nodes) and (
            self.journal.workflows.list_replan_signals(
                self.workspace_id, run.workflow_run_id, pending_only=True
            )
        ):
            # A fallback leaf may have closed admission with a signal before
            # the outer Coordinator had a chance to request Pause. Preserve
            # queued siblings until it consumes the settled closure evidence.
            self.transitions.request_pause(
                run.workflow_run_id,
                command_id=f"auto_pause_{run.workflow_run_id}",
                reason="node_boundary",
            )
            return
        elif any(n.status is not WorkflowStatus.COMPLETED for n in nodes):
            self.finalizer.finalize_failure(run.workflow_run_id, reason=reason or "node_failed")

    def _publish_completed_leaf(self, run, definition, node):
        node = self.transitions.get_node(node.node_run_id)
        if node.status is WorkflowStatus.COMPLETED or node.leaf_task_run_id is None:
            return
        leaf = self.journal.get_task_run(self.workspace_id, node.leaf_task_run_id)
        if leaf is None or leaf.status is not TaskRunStatus.READY_FOR_ACCEPTANCE:
            return
        if node.parallel_read_digest is None:
            self.transitions.complete_node(node.node_run_id)
            return

        def work(txn):
            if node.parallel_read_digest is not None:
                for contract in definition.output_contracts:
                    metadata = self.artifacts.get(
                        node_output_artifact_id(node.node_run_id, contract.slot)
                    )
                    if metadata is None:
                        if contract.required_for_node_completion:
                            raise ApplicationError(
                                ApplicationErrorCode.NEEDS_RECOVERY,
                                "Workflow candidate output is missing",
                            )
                        continue
                    txn.workflows.bind_artifact(
                        self.workspace_id,
                        run.workflow_run_id,
                        ArtifactBinding(
                            name=contract.slot,
                            artifact_id=metadata.artifact_id,
                            contract=ContractRef(kind=contract.kind, version=contract.version),
                        ),
                        node_run_id=node.node_run_id,
                        direction="output",
                    )
            self.transitions.complete_node(node.node_run_id)

        self.journal.transact(work)

    def _require_settled(self, run: WorkflowRun, node: NodeRun) -> None:
        """A returned drive must leave its node terminal or the run blocked.

        Without this guard an exhausted resume loop would let the serial order
        advance while the node is still RUNNING, admitting a sibling whose
        predecessors completed on a fork. The guard yields to drain semantics:
        on a draining/paused run the pause fact owns the next step.
        """

        current_run = self._require_run(run.workflow_run_id)
        if current_run.status is not WorkflowStatus.RUNNING:
            return
        current_node = self.transitions.get_node(node.node_run_id) or node
        if current_node.status.terminal or current_node.status is WorkflowStatus.BLOCKED:
            return
        self.finalizer.finalize_failure(run.workflow_run_id, reason="node_failed")

    def _settle_drain(self, workflow_run_id: str) -> WorkflowRun:
        """A drained run with no Active nodes left becomes paused."""

        return self.transitions.complete_drain(workflow_run_id)

    def _finalize_when_all_completed(self, workflow_run_id: str) -> WorkflowRun:
        run = self._require_run(workflow_run_id)
        if run.status in (WorkflowStatus.RUNNING, WorkflowStatus.DRAINING):
            nodes = self.journal.workflows.list_nodes(self.workspace_id, workflow_run_id)
            if nodes and all(node.status is WorkflowStatus.COMPLETED for node in nodes):
                # Crash-safe: a run whose last node completed but whose success
                # finalization was interrupted is finished here from durable facts.
                if not self._pause_for_blocking_review(workflow_run_id):
                    self.finalizer.finalize_success(workflow_run_id)
                run = self._require_run(workflow_run_id)
        return run

    async def recover(self, workflow_run_id: str, *, cancelled_is_user: bool = True) -> WorkflowRun:
        """Finish cancel/recovery mappings from durable facts after reconciliation.

        Blocking unknown Tool outcomes keep the run blocked; a resolved
        user-cancel intent closes through the fixed cancellation mapping; an
        ordinary crash path resumes without rerunning completed work.
        """

        run = self._require_run(workflow_run_id)
        if run.status.terminal:
            return run
        if run.status is WorkflowStatus.PAUSED:
            raise ApplicationError(
                ApplicationErrorCode.INVALID,
                "Workflow is paused; resume it before driving again",
            )
        nodes = self.journal.workflows.list_nodes(self.workspace_id, workflow_run_id)
        active = tuple(
            node
            for node in nodes
            if node.status in (WorkflowStatus.RUNNING, WorkflowStatus.BLOCKED)
        )
        if not active:
            return await self.run(workflow_run_id, cancelled_is_user=cancelled_is_user)
        revision = self.journal.workflows.get_revision(self.workspace_id, run.workflow_revision_id)
        definitions = {n.node_id: n for n in revision.nodes}
        # A later sibling can own the blocking fact or a committed completion.
        # Inspect the entire active set before resuming or closing the root.
        blocking = any(
            (report := self._leaf_report(node)) is not None
            and any(item.blocking for item in report.items)
            for node in active
        )
        if blocking or run.pending_terminal_intent == "user_cancel":
            for node in active:
                self._publish_completed_leaf(run, definitions[node.node_id], node)
        if blocking:
            return self.finalizer.mark_blocked(
                workflow_run_id, user_cancel=run.pending_terminal_intent == "user_cancel"
            )
        if run.pending_terminal_intent == "user_cancel":
            return self.finalizer.finalize_cancel(workflow_run_id, reason="user_cancelled")
        for node in active:
            if self.transitions.get_node(node.node_run_id).status is WorkflowStatus.BLOCKED:
                self.transitions.resume_blocked_node(node.node_run_id)
        if run.status is WorkflowStatus.BLOCKED:
            if run.pause_requested:
                # Resolve-success under a standing pause returns to draining and
                # never admits queued nodes; the drain settles after Active work.
                self.transitions.resume_blocked_run_to_draining(run.workflow_run_id)
            else:
                self.transitions.resume_blocked_run(run.workflow_run_id)
        return await self.run(workflow_run_id, cancelled_is_user=cancelled_is_user)

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
            live_node_run_id=next(iter(self._live_node_run_ids), self._live_node_run_id),
        )

    async def _drive_node(
        self,
        run: WorkflowRun,
        revision: WorkflowRevision,
        node_def: AgentNode,
        node: NodeRun,
        cap: int | None,
        *,
        cancelled_is_user: bool = True,
        frontier: ReadFrontier | None = None,
    ) -> str | None:
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
                submission_protocol_version=revision.submission_protocol_version,
            ),
            artifacts=self.artifacts,
            transitions=self.transitions,
            id_source=self.id_source,
            clock=self.clock,
            parallel_read_digest=node.parallel_read_digest,
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
            preference_loader=self.preference_loader,
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
            parallel_read_candidate=frontier is not None or node.parallel_read_digest is not None,
            preset_preference=(
                self.agent_preference_loader(node_def.agent_definition_ref.definition_id)
                if self.agent_preference_loader is not None
                else None
            ),
        )
        error_message: str | None = None
        cancelled = False
        attempts = 0
        self._live_node_run_id = node.node_run_id
        self._live_node_run_ids.add(node.node_run_id)
        prepared_to_close = None
        try:
            if self.initialize_context is not None:
                self.initialize_context(session)
            while True:
                attempts += 1
                node = self.transitions.get_node(node.node_run_id)
                leaf = self.journal.get_task_run(self.workspace_id, leaf_task_run_id)
                if node.status is WorkflowStatus.QUEUED:
                    persistence.attach(session)
                    if node_def.conversation_scope == "invoking_session":
                        persistence.restore_into(session)
                    generation, sources = self._frozen_generation(revision, node_def)
                    prepared = factory.prepare_new(
                        agent_run_id=self.id_source.new_id("arun"),
                        model=node_def.resolved_model_ref,
                        max_agent_generation_requests=cap,
                        require_enabled=False,
                        generation=generation,
                        settings_sources=sources,
                    )
                    prepared = self._compose_leaf_runtime(prepared, hooks)
                    prepared_to_close = prepared
                    if frontier is not None:
                        prepared = await frontier.prepare(
                            node_def.node_id,
                            prepared,
                            session,
                            hooks,
                            source_proven=factory.parallel_read_proven,
                        )
                    text = self._contract_text(run, node_def)
                    drive_options = (
                        {}
                        if cancelled_is_user
                        else {"cancelled_is_user": self._user_cancel_reader(run)}
                    )
                    if frontier is not None:
                        drive_options.update(frontier=frontier, node_id=node_def.node_id)
                        drive_options["cancelled_is_user"] = lambda: (
                            frontier.closes_on_cancel(cancelled_is_user)
                            or self._user_cancel_requested(run.workflow_run_id)
                        )
                    prepared_to_close = None
                    drive_error = await self._drive(
                        session,
                        text,
                        client_message_id=self._client_message_id(run, node_def, node),
                        prepared=prepared,
                        observer_identity=self._observer_identity(
                            run, node, node_def, leaf_session_id
                        ),
                        **drive_options,
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
                    prepared = factory.rehydrate(
                        agent_run.snapshot,
                        agent_run_id=node.agent_run_id,
                        mechanism_transform=lambda executor: self._compose_leaf_executor(
                            executor, hooks
                        ),
                    )
                    prepared_to_close = prepared
                    persistence.restore_into(session)
                    if node.parallel_read_digest is not None:
                        proof = prove_read_runtime(
                            prepared, session, source_proven=factory.parallel_read_proven
                        )
                        permission = self.journal.get_permission_snapshot_for_run(
                            self.workspace_id, node.agent_run_id
                        )
                        if (
                            proof != node.parallel_read_digest
                            or permission is None
                            or permission.workspace_root_digest
                            != workspace_root_digest(session.workspace_capability.root)
                        ):
                            raise read_contract_error()
                        prepared = replace(
                            prepared,
                            tool_executor=guard_read_executor(prepared.tool_executor, hooks),
                        )
                    if self._committed_final_assistant(leaf_session_id):
                        # The final Assistant message is durable; replay only the
                        # terminal commit (committer re-runs from durable facts,
                        # never a second model request).
                        session.finish_turn(FinishReason.STOP)
                        drive_error = None
                    else:
                        drive_options = (
                            {}
                            if cancelled_is_user
                            else {"cancelled_is_user": self._user_cancel_reader(run)}
                        )
                        if frontier is not None:
                            drive_options["cancelled_is_user"] = lambda: (
                                frontier.closes_on_cancel(cancelled_is_user)
                                or self._user_cancel_requested(run.workflow_run_id)
                            )
                        prepared_to_close = None
                        drive_error = await self._drive(
                            session,
                            "",
                            client_message_id=self._client_message_id(run, node_def, node),
                            prepared=prepared,
                            resume=True,
                            observer_identity=self._observer_identity(
                                run, node, node_def, leaf_session_id
                            ),
                            **drive_options,
                        )
                elif (
                    leaf is not None
                    and leaf.status is TaskRunStatus.OPEN
                    and not self.journal.has_open_turn_submission(
                        self.workspace_id, leaf_session_id
                    )
                    and run.pause_requested is False
                    and (
                        continuation_point := self._pending_continuation(
                            run.workflow_run_id, node.node_run_id
                        )
                    )
                    is not None
                ):
                    # User continuation on the original leaf (P04.1, D02): the
                    # same Session/TaskRun accepts a new Turn/AgentRun and the
                    # successor execution segment; frozen selections, deadline,
                    # budget and revocation re-checks all run at admission.
                    # This is deliberately separate from the crash-resume
                    # branch above, which replays an open Turn.
                    self._require_unrevoked(revision, node_def)
                    # The continuation context is the leaf's durable history:
                    # original goal, committed tool results, interrupted turn
                    # (spec 2.2/3.2.1). Positions continue, never restart.
                    # Narrow projection restore only: the crash-recovery
                    # coordinator owns open-turn re-entry, which a deliberate
                    # pause must not trigger. The log is restored before
                    # attach so the durable writer binds the restored log.
                    from morrow.runtime.durable_log import restore_conversation_log

                    session.log = restore_conversation_log(
                        self.journal, self.workspace_id, leaf_session_id
                    )
                    persistence.attach(session)
                    generation, sources = self._frozen_generation(revision, node_def)
                    prepared = factory.prepare_new(
                        agent_run_id=self.id_source.new_id("arun"),
                        model=node_def.resolved_model_ref,
                        max_agent_generation_requests=cap,
                        require_enabled=False,
                        generation=generation,
                        settings_sources=sources,
                        # A continuation turn deliberately carries the leaf's
                        # durable history (original goal, committed results);
                        # the fresh-log rule only guards first admissions.
                        allow_history=True,
                    )
                    prepared = self._compose_leaf_runtime(prepared, hooks)
                    hooks.continuation = SegmentContinuation(
                        command_id=continuation_point.continuation_command_id,
                        input=continuation_point.continuation_input,
                        control_generation=continuation_point.fact.control_generation,
                    )
                    if node.parallel_read_digest is not None:
                        proof = prove_read_runtime(
                            prepared, session, source_proven=factory.parallel_read_proven
                        )
                        permission = self.journal.get_permission_snapshot_for_run(
                            self.workspace_id, node.agent_run_id
                        )
                        if (
                            proof != node.parallel_read_digest
                            or permission is None
                            or permission.workspace_root_digest
                            != workspace_root_digest(session.workspace_capability.root)
                        ):
                            raise read_contract_error()
                        prepared = replace(
                            prepared,
                            tool_executor=guard_read_executor(prepared.tool_executor, hooks),
                        )
                    prepared_to_close = None
                    drive_error = await self._drive(
                        session,
                        continuation_point.continuation_input or "继续",
                        client_message_id=continuation_point.continuation_command_id,
                        prepared=prepared,
                        observer_identity=self._observer_identity(
                            run, node, node_def, leaf_session_id
                        ),
                        **(
                            {}
                            if cancelled_is_user
                            else {"cancelled_is_user": self._user_cancel_reader(run)}
                        ),
                    )
                    hooks.continuation = None
                else:
                    drive_error = None
                if drive_error is not None:
                    error_message = drive_error
                if frontier is not None and frontier.parallel:
                    return error_message
                settled = self._settle(run.workflow_run_id, node, error_message, cancelled)
                if settled != "resume" or attempts >= _MAX_RESUME_ATTEMPTS:
                    return error_message
        except asyncio.CancelledError:
            if not (cancelled_is_user or self._user_cancel_requested(run.workflow_run_id)) or (
                frontier is not None and frontier.crashed
            ):
                raise
            cancelled = True
            if frontier is None:
                self._settle(run.workflow_run_id, node, error_message, cancelled)
            return error_message
        finally:
            self._live_node_run_ids.discard(node.node_run_id)
            self._live_node_run_id = None
            if prepared_to_close is not None:
                await prepared_to_close.aclose()

    def _pending_continuation(self, workflow_run_id: str, node_run_id: str | None = None):
        from morrow.application.execution_pause import pending_continuation

        return pending_continuation(
            self.journal,
            self.workspace_id,
            workflow_run_id,
            node_run_id=node_run_id,
        )

    def _suspend_node_segment(self, workflow_run_id: str, node: NodeRun) -> bool:
        """Close the node's active segment and suspend the pause cycle atomically.

        The interrupted Turn already committed its terminal; this records the
        bounded safe point so the run's drain can settle to ``paused`` and a
        later continuation can be accepted on the same leaf.
        """

        from morrow.core.execution_pause import PauseSafetyPoint

        terminal = self._latest_interruption(node.conversation_session_id)

        def work(txn):
            point = txn.workflows.execution_pause.latest_pause_point(
                self.workspace_id,
                owner="workflow_run",
                owner_id=workflow_run_id,
            )
            if point is None:
                # A user pause accepted without a command id (run pause button)
                # leaves no pause-point row; create the cycle now so the safe
                # point and the continuation refs have a durable owner.
                from morrow.core.contracts import PauseIntentFact
                from morrow.core.execution_pause import WorkflowPausePoint

                generation = txn.workflows.execution_pause.next_control_generation(
                    self.workspace_id, owner="workflow_run", owner_id=workflow_run_id
                )
                now = self.clock()
                point = txn.workflows.execution_pause.insert_pause_point(
                    WorkflowPausePoint(
                        pause_point_id=f"pause_{workflow_run_id}_{generation}",
                        workspace_id=self.workspace_id,
                        fact=PauseIntentFact(
                            control_generation=generation,
                            command_id=f"auto_suspend_{workflow_run_id}_{generation}",
                            owner="workflow_run",
                            owner_id=workflow_run_id,
                            reason="user_interrupt",
                            lifecycle="requested",
                            requested_at=now,
                        ),
                        node_run_id=None,
                        created_at=now,
                        updated_at=now,
                    )
                )
            if point.fact.lifecycle not in ("requested", "quiescing", "suspended"):
                return False
            segment = txn.workflows.current_segment(node.node_run_id) if node.node_run_id else None
            if segment is not None and segment.status == "active":
                txn.workflows.close_segment(
                    self.workspace_id,
                    segment.segment_id,
                    status="interrupted",
                    pause_reason=point.fact.reason,
                    expected_row_version=segment.row_version,
                )
            if point.fact.lifecycle == "suspended":
                # A sibling node already carried this run-level cycle to
                # suspended. The point is a singleton, but the segment is
                # per-node: ours was closed above, so report success without
                # re-advancing the lifecycle or rewriting the recorded refs.
                return True
            if point.fact.lifecycle == "requested":
                fact = point.fact.model_copy(update={"lifecycle": "quiescing"})
                point = txn.workflows.execution_pause.save_pause_point(
                    point.model_copy(update={"fact": fact, "updated_at": self.clock()}),
                    expected_row_version=point.row_version,
                )
            now = self.clock()
            requests = (
                txn.list_model_requests(
                    self.workspace_id,
                    segment.agent_run_id if segment is not None else node.agent_run_id,
                )
                if (segment is not None and segment.agent_run_id) or node.agent_run_id
                else ()
            )
            safety = PauseSafetyPoint(
                stop_code=terminal.stop_code if terminal is not None else None,
                task_run_id=node.leaf_task_run_id,
                committed_position=terminal.sequence if terminal is not None else None,
                interrupted_turn_id=(
                    terminal.turn_id
                    if terminal is not None and terminal.turn_id is not None
                    else segment.turn_id
                    if segment is not None
                    else None
                ),
                interrupted_agent_run_id=(
                    segment.agent_run_id if segment is not None else node.agent_run_id
                ),
                interrupted_request_id=requests[-1].model_request_id if requests else None,
                closed_tool_call_ids=(
                    terminal.interrupted_call_ids[:64] if terminal is not None else ()
                ),
                note="segment suspended after the typed interrupted terminal",
            )
            fact = point.fact.model_copy(update={"lifecycle": "suspended", "suspended_at": now})
            txn.workflows.execution_pause.save_pause_point(
                point.model_copy(
                    update={
                        "fact": fact,
                        "safety": safety,
                        "segment_id": segment.segment_id if segment is not None else None,
                        "node_run_id": node.node_run_id,
                        "row_version": point.row_version + 1,
                        "updated_at": now,
                    }
                ),
                expected_row_version=point.row_version,
            )
            return True

        try:
            return self.journal.transact(work)
        except Exception:
            return False

    def _user_cancel_requested(self, workflow_run_id: str) -> bool:
        """The durable user-cancel intent, authoritative over driver identity (D12).

        A driver is created once with ``cancelled_is_user=False`` to keep the
        process-loss recovery boundary; a later explicit user cancel must still
        close the leaf through its normal cancellation path.
        """

        run = self.journal.workflows.get_run(self.workspace_id, workflow_run_id)
        return run is not None and run.pending_terminal_intent == "user_cancel"

    def _user_cancel_reader(self, run):
        """Deferred reader for AgentLoop: evaluated at cancellation time."""

        return lambda: self._user_cancel_requested(run.workflow_run_id)

    def _observer_identity(self, run, node, node_def, leaf_session_id) -> dict | None:
        if self.event_observer is None:
            return None
        return {
            "workflow_run_id": run.workflow_run_id,
            "node_run_id": node.node_run_id,
            "node_id": node_def.node_id,
            "session_id": leaf_session_id,
            "agent_run_id": node.agent_run_id,
        }

    def _observe(self, identity: dict | None, event) -> None:
        """Forward one leaf event to the progress seam; never aborts execution."""

        if self.event_observer is None or identity is None:
            return
        try:
            self.event_observer.node_event(
                event,
                workflow_run_id=identity["workflow_run_id"],
                node_run_id=identity["node_run_id"],
                node_id=identity["node_id"],
                session_id=identity["session_id"],
                agent_run_id=identity["agent_run_id"],
            )
        except Exception:
            # Projection failures are bounded and best-effort by contract.
            return

    async def _drive(
        self,
        session: Session,
        text: str,
        *,
        client_message_id: str,
        prepared,
        resume: bool = False,
        cancelled_is_user: bool | Callable[[], bool] = True,
        frontier: ReadFrontier | None = None,
        node_id: str | None = None,
        observer_identity: dict | None = None,
    ) -> str | None:
        loop = AgentLoop(
            prepared.provider,
            prepared.model,
            prepared.context_builder,
            id_source=self.id_source,
            clock=_ClockAdapter(self.clock),
            tool_executor=prepared.tool_executor,
            runtime_control=self.leaf_runtime_control,
            pause_control=self.pause_control,
            steering_mode="inject",
            retry_sleep=self.retry_sleep,
            activity_observer=(
                _BoundReasoningObserver(
                    self.activity_observer, dict(observer_identity), session.session_id
                )
                if self.activity_observer is not None and observer_identity is not None
                else None
            ),
        )
        error_message = None
        # The drive-level identity snapshot predates AgentRun creation, so the
        # run id only becomes known here; keep the seam's identity exact (P2.3,
        # P5.1 replaces the snapshot with per-event identity).
        if observer_identity is not None:
            observer_identity["agent_run_id"] = prepared.agent_run_id
        stream = loop.run_task(
            session,
            text,
            client_message_id=client_message_id,
            resume_current_turn=resume,
            prepared=prepared,
            cancelled_is_user=cancelled_is_user,
        )
        try:
            async for event in stream:
                self._observe(observer_identity, event)
                if event.type == "turn.started" and frontier is not None:
                    await frontier.wait_started(node_id)
                if event.type == "error":
                    error_message = event.payload.get("message") or error_message
        except asyncio.CancelledError:
            if frontier is not None:
                # Cancellation at the admission barrier is outside AgentLoop's
                # own awaits. Deliver it to the suspended stream so driver loss
                # still follows its crash path instead of GeneratorExit cleanup.
                try:
                    await stream.athrow(asyncio.CancelledError())
                    async for _event in stream:
                        pass
                except (StopAsyncIteration, asyncio.CancelledError):
                    pass
            raise
        finally:
            await stream.aclose()
        # Completion expiry (P6.4 / proposal 5.2): steering that outlived the
        # node never forwards to a later node — it is marked expired durably.
        try:
            expired = self.leaf_runtime_control.supersede_steering(session.session_id)
        except Exception:
            # Expiry is best-effort cleanup; it must never fail the drive.
            expired = 0
        if expired and self.activity_observer is not None and observer_identity is not None:
            self._expire_receipt(observer_identity, session.session_id, expired)
        return error_message

    def _expire_receipt(self, identity: dict, session_id: str, count: int) -> None:
        """Project one expired-steer receipt onto leaf and root streams."""
        try:
            observer = self.activity_observer
            if observer is None:
                return
            observer.steer_expired(identity, session_id, count)
        except Exception:
            return

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
        if (
            error_message is not None
            and error_message.startswith("paused:")
            and node.status is WorkflowStatus.QUEUED
        ):
            # Pause won the admission race: the rolled-back Turn leaves the node
            # queued, and the draining run settles without a failure mapping.
            return "paused"
        if reason == "policy_revoked":
            self.finalizer.finalize_cancel(workflow_run_id, reason="policy_revoked")
            return "terminal"
        if reason in ("budget_exhausted", "deadline_exceeded"):
            # A hard budget/deadline limit is a clear terminal mapping. Without
            # this branch the interrupted-conversion below would park the run
            # again, so every resume would instantly re-pause with zero
            # progress and the limit would never surface.
            self.finalizer.finalize_failure(workflow_run_id, reason=reason)
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
            revision = self.journal.workflows.get_revision(
                self.workspace_id, run.workflow_revision_id
            )
            definition = next(n for n in revision.nodes if n.node_id == node.node_id)
            self._publish_completed_leaf(run, definition, node)
            if self.journal.workflows.list_replan_signals(
                self.workspace_id, workflow_run_id, pending_only=True
            ):
                if user_cancel:
                    self.finalizer.finalize_cancel(workflow_run_id, reason="user_cancelled")
                    return "terminal"
                return "continue"
            nodes = self.journal.workflows.list_nodes(self.workspace_id, workflow_run_id)
            if all(item.status is WorkflowStatus.COMPLETED for item in nodes):
                # Success finalization waits for every declared node, not only
                # the exported-output producers.
                if self._pause_for_blocking_review(workflow_run_id):
                    return "continue"
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
        if (
            leaf is not None
            and leaf.status is TaskRunStatus.OPEN
            and node.status is WorkflowStatus.RUNNING
        ):
            # A model/provider interruption closes the Turn but deliberately
            # leaves its Workflow TaskRun open. Convert that terminal into the
            # same durable pause cycle used by the user pause path. This keeps
            # the graph alive and makes the next resume append a successor
            # segment instead of rerunning the node from its first admission.
            interrupted = self._latest_interruption(node.conversation_session_id)
            if interrupted is not None:
                stop_code = interrupted.stop_code or AgentStopCode.PROCESS_INTERRUPTED
                reason = (
                    "process_interrupt"
                    if stop_code is AgentStopCode.PROCESS_INTERRUPTED
                    else "provider_failure"
                )
                try:
                    self.transitions.request_pause(
                        workflow_run_id,
                        # Key the pause cycle by the interrupted terminal, not a
                        # node-admission constant: a second interruption of the
                        # same admission must open a fresh cycle, and re-running
                        # the settlement for the same terminal must stay
                        # idempotent under the identical id.
                        command_id=f"auto_interrupt_{node.node_run_id}_{interrupted.sequence}",
                        reason=reason,
                    )
                except ApplicationError:
                    return "terminal"
                if self._suspend_node_segment(workflow_run_id, node):
                    return "paused"
        if leaf is not None and leaf.status is TaskRunStatus.OPEN and run.pause_requested:
            # A typed interrupted terminal closed this leaf's Turn while the
            # user pause stands: suspend the node's segment with its safe
            # point and settle the drain without a failure mapping (P04.2).
            if self._suspend_node_segment(workflow_run_id, node):
                return "paused"
        self.finalizer.finalize_failure(workflow_run_id, reason=reason or "node_failed")
        return "terminal"

    def _latest_interruption(self, session_id: str | None):
        """Read only the terminal fact for a leaf; never infer from an error string."""
        if not session_id:
            return None
        snapshot = restore_conversation_log(self.journal, self.workspace_id, session_id).snapshot()
        if not snapshot.records:
            return None
        terminal = snapshot.records[-1]
        if getattr(terminal, "finish_reason", None) is not FinishReason.INTERRUPTED:
            return None
        return terminal

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
            predecessor = nodes_by_id.get(edge.from_node_id)
            if predecessor is None:
                # A missing child NodeRun denotes immutable inherited Past.
                continue
            if predecessor.status is not WorkflowStatus.COMPLETED:
                raise ApplicationError(
                    ApplicationErrorCode.NEEDS_RECOVERY,
                    f"node {node_def.node_id} is not ready: predecessor "
                    f"{edge.from_node_id} is {predecessor.status.value}",
                )
        for binding in node_def.input_bindings:
            if binding.source == "workflow_input":
                continue
            ref = binding.node_output
            if self.outputs.resolve(run.workflow_run_id, ref.node_id, ref.output_slot) is None:
                raise ApplicationError(
                    ApplicationErrorCode.NEEDS_RECOVERY,
                    f"node {node_def.node_id} is not ready: input {binding.input_name} has "
                    "no bound producer Artifact",
                )

    def _compose_leaf_runtime(self, prepared, hooks: WorkflowLeafHooks):
        """Inject mechanism tools and freeze Coder bash to the native sandbox."""

        return replace(
            prepared, tool_executor=self._compose_leaf_executor(prepared.tool_executor, hooks)
        )

    def _compose_leaf_executor(self, executor, hooks):
        if executor is None:
            return None
        extra = []
        node = hooks.context.node
        extra.append(
            make_submit_node_result_tool(
                hooks,
                node.output_contracts,
                protocol_version=getattr(
                    hooks.context,
                    "submission_protocol_version",
                    SUBMIT_SCHEMA_VERSION,
                ),
            )
        )
        self._attach_delivery_boundary(hooks, executor)
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
            return executor
        registry = ToolRegistry()
        for tool in executor.tool_set.tools.values():
            registry.register(tool)
        for tool in extra:
            registry.register(tool)
        return ToolExecutor(
            registry.snapshot(),
            executor.run_policy,
            approval_port=executor.approval_port,
            capability_policy=executor.capability_policy,
            expected_process_isolation=isolation,
        )

    def _attach_delivery_boundary(self, hooks, executor) -> None:
        """Delivery files are read through the node's own frozen workspace boundary."""

        policy = getattr(executor, "capability_policy", None)
        workspace = getattr(policy, "workspace", None)
        if (
            workspace is not None
            and getattr(workspace, "workspace_id", None) == self.workspace_id
            and getattr(workspace, "root", None) is not None
        ):
            hooks.workspace_root = workspace.root

    def _frozen_generation(self, revision: WorkflowRevision, node_def: AgentNode):
        origin = self.journal.workflows.get_task_plan_provenance(
            self.workspace_id, revision.workflow_revision_id
        )
        if origin is None:
            return None, None
        item = next(
            (row for row in origin.frozen_selections if row.node_id == node_def.node_id),
            None,
        )
        if item is None:
            return None, None
        from morrow.core.agent_runs import SettingSource
        from morrow.core.models import GenerationOptions

        scope = {
            "node": "explicit",
            "agent": "explicit",
            "session_snapshot": "session",
            "adapter_default": "adapter",
        }[item.generation_source]
        return (
            GenerationOptions(reasoning_effort=item.resolved_generation),
            {"generation": SettingSource(scope=scope, revision=0)},
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
            produced = self.outputs.resolve(run.workflow_run_id, ref.node_id, ref.output_slot)
            if produced is None:
                raise ApplicationError(
                    ApplicationErrorCode.NEEDS_RECOVERY,
                    f"node {node_def.node_id} input {binding.input_name} is missing",
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

    def _pause_for_blocking_review(self, workflow_run_id: str) -> bool:
        """Chat task-plans pause for a fix+new Review instead of completing needs_revision."""

        run = self._require_run(workflow_run_id)
        if run.status.terminal or self.replan is None:
            return False
        session_id = self.replan._session_for_run(run)
        if (
            session_id is None
            or self.replan.planning is None
            or self.replan.planning.repo.binding(self.workspace_id, session_id) is None
        ):
            return False
        revision = self.journal.workflows.get_revision(self.workspace_id, run.workflow_revision_id)
        nodes = self.journal.workflows.list_nodes(self.workspace_id, workflow_run_id)
        bindings = self.journal.workflows.list_bindings(self.workspace_id, workflow_run_id)
        try:
            result = compute_workflow_result(
                revision,
                nodes,
                bindings,
                artifacts=self.finalizer.artifacts,
                resolver=self.finalizer.outputs,
                workflow_run_id=workflow_run_id,
            )
        except ApplicationError:
            return False
        if result != RESULT_NEEDS_REVISION:
            return False
        review_ids = {
            item.node_id
            for item in revision.nodes
            if any(slot.kind == RESULT_DRIVING_KIND for slot in item.output_contracts)
        }
        producer = next(
            (
                node
                for node in nodes
                if node.node_id in review_ids and node.status is WorkflowStatus.COMPLETED
            ),
            None,
        )
        if producer is None:
            return False
        existing = self.journal.workflows.list_replan_signals(self.workspace_id, workflow_run_id)
        if not any(signal.node_run_id == producer.node_run_id for signal in existing):
            self.journal.workflows.put_replan_signal(
                ReplanSignal(
                    signal_id="rsig_" + producer.node_run_id.removeprefix("nrun_"),
                    workspace_id=self.workspace_id,
                    workflow_run_id=workflow_run_id,
                    node_run_id=producer.node_run_id,
                    request=ReplanRequest(
                        schema_version=2,
                        reason="scope_correction",
                        evidence_refs=(ReplanEvidenceRef(kind="submitted_slot", slot="review"),),
                        affected_facts=(
                            AffectedTaskFact(
                                summary=(
                                    "Review reported blocking findings; add a General repair "
                                    "and a new Review. Keep the existing review output."
                                ),
                                impact="invalidate",
                            ),
                        ),
                    ),
                    created_at=self.clock(),
                )
            )
        if not run.pause_requested:
            self.transitions.request_pause(
                workflow_run_id,
                command_id=f"auto_pause_{workflow_run_id}",
                reason="node_boundary",
            )
        return True

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


class _BoundReasoningObserver:
    """Binds one leaf drive's identity to the activity reasoning seam."""

    def __init__(self, activity_observer, identity: dict, session_id: str) -> None:
        self._observer = activity_observer
        self._identity = identity
        self._session_id = session_id

    def reasoning_delta(self, *, turn_id: str, attempt_ordinal: int, fragment: str) -> None:
        self._observer.leaf_reasoning(
            self._identity,
            session_id=self._session_id,
            turn_id=turn_id,
            attempt_ordinal=attempt_ordinal,
            fragment=fragment,
        )

    def tool_observation(self, **fact) -> None:
        self._observer.leaf_tool_observation(self._identity, self._session_id, fact)

    def tool_output(self, *, call_id: str, text: str) -> None:
        self._observer.leaf_tool_output(self._identity, self._session_id, call_id, text)
