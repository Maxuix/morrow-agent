"""Workflow terminal finalization and root TaskOutcome production.

Every terminal mapping commits the Node/Workflow terminal facts, the root
TaskRun transition and the Workflow evidence outcome in one Operational Store
transaction. All facts are read from durable state, so recovery finishes the
finalizer without rerunning any node.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from morrow.application.workflows.evidence import workflow_task_outcome
from morrow.application.workflows.transitions import WorkflowTransitionService
from morrow.core.application import ApplicationError, ApplicationErrorCode
from morrow.core.domain import (
    TASK_OUTCOME_ID_PREFIX,
    TASK_TRANSITION_ID_PREFIX,
    ArtifactReference,
    DurableTaskRun,
    DurableTaskRunTransition,
    TaskOutcome,
    TaskOutcomeEvidenceKind,
    TaskOutcomeEvidenceRef,
    TaskOutcomeTrigger,
    TaskRunStatus,
)
from morrow.core.execution import ToolExecutionDisposition, ToolExecutionState
from morrow.core.models import utc_now
from morrow.core.workflows.contracts import (
    RESULT_DRIVING_KIND,
    ReviewReport,
    parse_workflow_payload,
)
from morrow.core.workflows.definitions import WorkflowRevision
from morrow.core.workflows.runs import NodeRun, WorkflowRun, WorkflowStatus

RESULT_SUCCEEDED = "succeeded"
RESULT_NEEDS_REVISION = "needs_revision"


def compute_workflow_result(
    revision: WorkflowRevision,
    nodes: tuple[NodeRun, ...],
    bindings,
    *,
    artifacts=None,
) -> str:
    """Result semantics over the exported required outputs.

    Only required output refs whose contract kind is ReviewReport can yield
    ``needs_revision``. Any blocking verdict among those exact exported refs
    drives the result; a ReviewReport that is not exported is evidence only.
    """

    outputs = {
        (node.node_id, slot.slot): slot for node in revision.nodes for slot in node.output_contracts
    }
    nodes_by_id = {node.node_id: node for node in nodes}
    bound = {
        (node_run_id, binding.name): binding
        for node_run_id, direction, binding in bindings
        if direction == "output"
    }
    for ref in revision.required_outputs:
        slot = outputs.get((ref.node_id, ref.output_slot))
        if slot is None or slot.kind != RESULT_DRIVING_KIND:
            continue
        producer = nodes_by_id.get(ref.node_id)
        binding = (
            bound.get((producer.node_run_id, ref.output_slot)) if producer is not None else None
        )
        if binding is None or artifacts is None:
            continue
        stored = artifacts.get(binding.artifact_id)
        if stored is None:
            continue
        payload = parse_workflow_payload(
            slot.kind, artifacts.read(binding.artifact_id, max_bytes=stored.byte_size).content
        )
        if isinstance(payload, ReviewReport) and payload.blocking:
            return RESULT_NEEDS_REVISION
    return RESULT_SUCCEEDED


class WorkflowOutcomeFinalizer:
    def __init__(
        self,
        journal,
        *,
        workspace_id: str,
        transitions: WorkflowTransitionService,
        id_source,
        clock: Callable[[], datetime] = utc_now,
        artifacts=None,
    ) -> None:
        self.journal = journal
        self.workspace_id = workspace_id
        self.transitions = transitions
        self.id_source = id_source
        self.clock = clock
        self.artifacts = artifacts

    # Terminal mappings ----------------------------------------------------------

    def finalize_success(self, workflow_run_id: str) -> WorkflowRun:
        run = self.transitions.get_run(workflow_run_id)
        if run is not None and run.status.terminal:
            return run

        def work(txn) -> WorkflowRun:
            run = txn.workflows.get_run(self.workspace_id, workflow_run_id)
            if run.status.terminal:
                return run
            revision = txn.workflows.get_revision(self.workspace_id, run.workflow_revision_id)
            nodes = txn.workflows.list_nodes(self.workspace_id, workflow_run_id)
            for node in nodes:
                self.transitions.complete_node(node.node_run_id)
            bindings = txn.workflows.list_bindings(self.workspace_id, workflow_run_id)
            result = compute_workflow_result(
                revision,
                nodes,
                bindings,
                artifacts=self.artifacts,
            )
            # The root transition lands while the Run is still nonterminal; the
            # Run closes after it, then the marked snapshot records both.
            root = txn.get_task_run(self.workspace_id, run.root_task_run_id)
            transition = self._transition_record(
                root, TaskRunStatus.READY_FOR_ACCEPTANCE, reason="workflow_completed"
            )
            ready_root = txn.transition_workflow_task(
                self.workspace_id,
                workflow_run_id,
                root.task_run_id,
                target=TaskRunStatus.READY_FOR_ACCEPTANCE,
                transition=transition,
                expected_row_version=root.row_version,
            )
            run = self.transitions.complete_run(workflow_run_id, result_status=result)
            outcome = self._build_outcome(
                txn,
                run,
                revision,
                nodes,
                ready_root,
                trigger=TaskOutcomeTrigger.SNAPSHOT,
                summary=f"Workflow '{revision.name}' completed with result {result}.",
                basis_extra=(f"workflow_result={result}", f"node_count={len(nodes)}"),
                markers=(transition,),
            )
            txn.put_task_outcome(self.workspace_id, outcome)
            return run

        return self.journal.transact(work)

    def finalize_failure(self, workflow_run_id: str, *, reason: str) -> WorkflowRun:
        return self._finalize_non_success(
            workflow_run_id,
            run_target=WorkflowStatus.FAILED,
            root_target=TaskRunStatus.FAILED,
            node_active_target=WorkflowStatus.FAILED,
            reason=reason,
        )

    def finalize_cancel(
        self, workflow_run_id: str, *, reason: str = "user_cancelled"
    ) -> WorkflowRun:
        return self._finalize_non_success(
            workflow_run_id,
            run_target=WorkflowStatus.CANCELLED,
            root_target=TaskRunStatus.CANCELLED,
            node_active_target=WorkflowStatus.CANCELLED,
            reason=reason,
        )

    def finalize_abandon(self, workflow_run_id: str) -> WorkflowRun:
        """Recovery-only abandon of a blocked run; unknown evidence is preserved.

        The blocked NodeRun, its Artifacts and its side effects stay untouched;
        only queued nodes are cancelled. The root delegates to the existing
        ABANDONED transition and the Run closes as ``cancelled(reason=abandoned)``
        in the same transaction.
        """

        current = self.transitions.get_run(workflow_run_id)
        if current is not None and current.status.terminal:
            return current

        def work(txn) -> WorkflowRun:
            run = txn.workflows.get_run(self.workspace_id, workflow_run_id)
            if run.status.terminal:
                return run
            if run.status is not WorkflowStatus.BLOCKED:
                raise ApplicationError(
                    ApplicationErrorCode.INVALID,
                    "recovery-only abandon requires a blocked Workflow; active runs use "
                    "their owning foreground cancellation",
                )
            revision = txn.workflows.get_revision(self.workspace_id, run.workflow_revision_id)
            nodes = txn.workflows.list_nodes(self.workspace_id, workflow_run_id)
            for node in nodes:
                if node.status is WorkflowStatus.QUEUED:
                    self.transitions.cancel_node(node.node_run_id)
            root = txn.get_task_run(self.workspace_id, run.root_task_run_id)
            if not root.status.is_terminal:
                transition = self._transition_record(
                    root, TaskRunStatus.ABANDONED, reason="workflow_abandoned"
                )
                closed_root = txn.transition_workflow_task(
                    self.workspace_id,
                    workflow_run_id,
                    root.task_run_id,
                    target=TaskRunStatus.ABANDONED,
                    transition=transition,
                    expected_row_version=root.row_version,
                )
                outcome = self._build_outcome(
                    txn,
                    run,
                    revision,
                    nodes,
                    closed_root,
                    trigger=TaskOutcomeTrigger.TERMINAL_CLOSE,
                    summary=f"Workflow '{revision.name}' was abandoned while blocked.",
                    basis_extra=("workflow_terminal=abandoned",),
                    markers=None,
                )
                txn.put_task_outcome(self.workspace_id, outcome)
            return self.transitions.cancel_run(workflow_run_id)

        return self.journal.transact(work)

    def _finalize_non_success(
        self,
        workflow_run_id: str,
        *,
        run_target: WorkflowStatus,
        root_target: TaskRunStatus,
        node_active_target: WorkflowStatus,
        reason: str,
    ) -> WorkflowRun:
        current = self.transitions.get_run(workflow_run_id)
        if current is not None and current.status.terminal:
            return current

        def work(txn) -> WorkflowRun:
            run = txn.workflows.get_run(self.workspace_id, workflow_run_id)
            if run.status.terminal:
                return run
            revision = txn.workflows.get_revision(self.workspace_id, run.workflow_revision_id)
            nodes = txn.workflows.list_nodes(self.workspace_id, workflow_run_id)
            for node in nodes:
                if node.status is WorkflowStatus.RUNNING or node.status is WorkflowStatus.BLOCKED:
                    if node_active_target is WorkflowStatus.FAILED:
                        self.transitions.fail_node(node.node_run_id)
                    else:
                        self.transitions.cancel_node(node.node_run_id)
                elif node.status is WorkflowStatus.QUEUED:
                    self.transitions.cancel_node(node.node_run_id)
            # The root closes while the Run is still nonterminal; the Run's own
            # terminal fact lands in the same transaction afterwards.
            root = txn.get_task_run(self.workspace_id, run.root_task_run_id)
            if not root.status.is_terminal and root.status is not root_target:
                transition = self._transition_record(root, root_target, reason=f"workflow_{reason}")
                closed_root = txn.transition_workflow_task(
                    self.workspace_id,
                    workflow_run_id,
                    root.task_run_id,
                    target=root_target,
                    transition=transition,
                    expected_row_version=root.row_version,
                )
                outcome = self._build_outcome(
                    txn,
                    run,
                    revision,
                    nodes,
                    closed_root,
                    trigger=TaskOutcomeTrigger.TERMINAL_CLOSE,
                    summary=f"Workflow '{revision.name}' closed: {reason}.",
                    basis_extra=(f"workflow_terminal={reason}",),
                    markers=None,
                )
                txn.put_task_outcome(self.workspace_id, outcome)
            if run_target is WorkflowStatus.FAILED:
                run = self.transitions.fail_run(workflow_run_id)
            else:
                run = self.transitions.cancel_run(workflow_run_id)
            return run

        return self.journal.transact(work)

    def mark_blocked(self, workflow_run_id: str, *, user_cancel: bool) -> WorkflowRun:
        """Commit blocked only for the affected Workflow; the root stays OPEN."""

        if user_cancel:
            self.transitions.set_pending_user_cancel(workflow_run_id)
        if self.transitions.get_run(workflow_run_id) is None:
            raise ValueError("WorkflowRun is missing")
        for node in self.journal.workflows.list_nodes(self.workspace_id, workflow_run_id):
            if node.status is WorkflowStatus.RUNNING:
                self.transitions.block_node(node.node_run_id)
        return self.transitions.block_run(workflow_run_id)

    # Evidence projection --------------------------------------------------------

    def _transition_record(
        self, task: DurableTaskRun, target: TaskRunStatus, *, reason: str
    ) -> DurableTaskRunTransition:
        # The journal rejects a stale attempt stamp; the field defaults to 1.
        return DurableTaskRunTransition(
            transition_id=self.id_source.new_id(TASK_TRANSITION_ID_PREFIX),
            workspace_id=self.workspace_id,
            session_id=task.session_id,
            task_run_id=task.task_run_id,
            from_status=task.status,
            to_status=target,
            reason=reason,
            attempt=task.attempt,
            created_at=self.clock(),
        )

    def _build_outcome(
        self,
        txn,
        run: WorkflowRun,
        revision: WorkflowRevision,
        nodes: tuple[NodeRun, ...],
        root: DurableTaskRun,
        *,
        trigger: TaskOutcomeTrigger,
        summary: str,
        basis_extra: tuple[str, ...],
        markers: tuple[DurableTaskRunTransition, ...] | None,
    ) -> TaskOutcome:
        """Bounded deterministic projection in stable Node order from leaf links."""

        bindings = txn.workflows.list_bindings(self.workspace_id, run.workflow_run_id)
        bound_outputs = {
            (node_run_id, binding.name): binding
            for node_run_id, direction, binding in bindings
            if direction == "output"
        }
        artifact_refs: list[ArtifactReference] = []
        evidence_refs: list[TaskOutcomeEvidenceRef] = [
            TaskOutcomeEvidenceRef(
                kind=TaskOutcomeEvidenceKind.WORKFLOW_RUN,
                reference_id=run.workflow_run_id,
                role=("workflow_result_snapshot" if markers else "workflow_terminal_run"),
            )
        ]
        if markers:
            evidence_refs.append(
                TaskOutcomeEvidenceRef(
                    kind=TaskOutcomeEvidenceKind.TASK_TRANSITION,
                    reference_id=markers[0].transition_id,
                    role="workflow_ready_transition",
                )
            )
        executions = []
        for node in sorted(nodes, key=lambda item: item.node_id):
            if node.agent_run_id is not None:
                evidence_refs.append(
                    TaskOutcomeEvidenceRef(
                        kind=TaskOutcomeEvidenceKind.AGENT_RUN,
                        reference_id=node.agent_run_id,
                        role="workflow_node_agent_run",
                    )
                )
            if node.leaf_task_run_id is not None:
                for turn in txn.list_task_turns(self.workspace_id, node.leaf_task_run_id):
                    evidence_refs.append(
                        TaskOutcomeEvidenceRef(
                            kind=TaskOutcomeEvidenceKind.TURN,
                            reference_id=turn.turn_id,
                            role="workflow_leaf_turn",
                        )
                    )
                executions.extend(
                    txn.list_task_executions(self.workspace_id, node.leaf_task_run_id)
                )
        for execution in executions:
            evidence_refs.append(
                TaskOutcomeEvidenceRef(
                    kind=TaskOutcomeEvidenceKind.TOOL_EXECUTION,
                    reference_id=execution.tool_execution_id,
                    role="tool_execution",
                )
            )
        for ref in revision.required_outputs:
            producer = next(
                (node for node in nodes if node.node_id == ref.node_id),
                None,
            )
            binding = (
                bound_outputs.get((producer.node_run_id, ref.output_slot))
                if producer is not None
                else None
            )
            if binding is not None:
                artifact_refs.append(
                    ArtifactReference(artifact_id=binding.artifact_id, role="workflow_result")
                )
        outcomes = txn.list_task_outcomes(self.workspace_id, root.task_run_id)
        return workflow_task_outcome(
            outcome_id=self.id_source.new_id(TASK_OUTCOME_ID_PREFIX),
            workspace_id=self.workspace_id,
            session_id=root.session_id,
            task_run_id=root.task_run_id,
            version=len(outcomes) + 1,
            trigger=trigger,
            task_status=root.status,
            summary=summary,
            goal_reference=TaskOutcomeEvidenceRef(
                kind=TaskOutcomeEvidenceKind.ARTIFACT,
                reference_id=run.input_artifacts[0].artifact_id,
                role="workflow_input",
            ),
            changed_paths=tuple(
                sorted(
                    {
                        evidence.relative_path
                        for execution in executions
                        if execution.facts is not None
                        for evidence in execution.facts.files
                    }
                )
            ),
            validation_facts=tuple(
                sorted(
                    {
                        f"{execution.tool_name}:{execution.disposition.value}"
                        for execution in executions
                        if execution.state
                        in {ToolExecutionState.HANDLER_COMPLETED, ToolExecutionState.CLOSED}
                    }
                )
            ),
            side_effects=tuple(
                sorted(
                    {
                        f"{execution.tool_name}:{execution.intent.effect_class.value}"
                        for execution in executions
                    }
                )
            ),
            unresolved_items=tuple(
                sorted(
                    f"{execution.tool_name}:{execution.disposition.value}"
                    for execution in executions
                    if execution.state is not ToolExecutionState.CLOSED
                    or execution.disposition
                    in {
                        ToolExecutionDisposition.FAILED,
                        ToolExecutionDisposition.INTERRUPTED,
                        ToolExecutionDisposition.UNKNOWN,
                    }
                )
            ),
            completion_basis=(
                f"trigger={trigger.value}",
                f"task_status={root.status.value}",
                *basis_extra,
                f"tool_execution_count={len(executions)}",
                f"prior_outcome_count={len(outcomes)}",
            ),
            evidence_refs=tuple(evidence_refs),
            artifact_refs=tuple(artifact_refs),
            created_at=self.clock(),
        )
