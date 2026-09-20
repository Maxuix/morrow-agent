"""Restricted recovery of historical serial model-failure Workflow failures.

Targets exactly the recorded shape the screenshot-era failures left behind: a
terminal ``failed`` WorkflowRun whose single failed node parked on a
Provider-layer model fault (network/timeout/rate-limit/auth) with complete
durable evidence. The recovery is one application transaction that reopens the
same business identities (root Task, leaf Task, NodeRun) and rebuilds the
durable pause cycle so the ordinary resume/segment-admission path takes over:

1. Every terminal fact, log record, request settlement and historical
   TaskOutcome stays untouched; new execution appends successor segments.
2. Failed -> running applies only to the one vouched failed node; cancelled
   siblings recover to queued only when proven never-started (no segments, no
   leaf ownership, no agent run).
3. Ordinary ``save_run``/``save_node`` paths keep refusing terminal->running
   transitions; this module is the only sanctioned writer and re-validates the
   full eligibility inside its transaction under OCC.
4. An ineligible record refuses with concrete reasons and never silently
   creates replacement nodes.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from morrow.core.application import (
    ApplicationCommandDisposition,
    ApplicationCommandReceipt,
    ApplicationError,
    ApplicationErrorCode,
)
from morrow.core.contracts import PauseIntentFact
from morrow.core.domain import (
    TASK_TRANSITION_ID_PREFIX,
    DurableTaskRunTransition,
    TaskRunStatus,
)
from morrow.core.execution_pause import PauseSafetyPoint, WorkflowPausePoint
from morrow.core.models import AgentStopCode
from morrow.core.workflows.runs import WorkflowRun, WorkflowStatus
from morrow.runtime.durable_log import restore_conversation_log

RECOVERY_OPERATION = "workflow_historical_recovery"

#: Provider-layer faults the durable request ledger can vouch for. A bare
#: ``internal`` stop code is ambiguous by contract and never recovers; the
#: business-failure stop codes (validation, verifier, call limits, ...) keep
#: their failed semantics.
RECOVERABLE_STOP_CODES = frozenset(
    {
        AgentStopCode.PROVIDER_AUTH,
        AgentStopCode.PROVIDER_NETWORK,
        AgentStopCode.PROVIDER_RATE_LIMIT,
        AgentStopCode.PROVIDER_TIMEOUT,
    }
)


@dataclass(frozen=True)
class HistoricalRecoveryAssessment:
    """Read-only verdict of one historical recovery eligibility check."""

    workflow_run_id: str
    eligible: bool
    reasons: tuple[str, ...] = ()
    failed_node_run_id: str | None = None
    leaf_task_run_id: str | None = None
    leaf_session_id: str | None = None
    leaf_segment_id: str | None = None
    leaf_agent_run_id: str | None = None
    stop_code: str | None = None
    restart_node_run_ids: tuple[str, ...] = ()


class WorkflowHistoricalRecoveryService:
    """One restricted application operation over durable Workflow state."""

    def __init__(
        self,
        *,
        journal,
        workspace_id: str,
        recovery,
        id_source,
        clock,
    ) -> None:
        self.journal = journal
        self.workspace_id = workspace_id
        self.recovery = recovery
        self.id_source = id_source
        self.clock = clock

    # Assessment --------------------------------------------------------------

    def assess(self, workflow_run_id: str) -> HistoricalRecoveryAssessment:
        """Read-only eligibility verdict with concrete refusal reasons."""

        return self._assess_on(self.journal, workflow_run_id)

    def _assess_on(self, j, workflow_run_id: str) -> HistoricalRecoveryAssessment:
        ws = self.workspace_id

        def refuse(*reasons: str) -> HistoricalRecoveryAssessment:
            return HistoricalRecoveryAssessment(
                workflow_run_id=workflow_run_id, eligible=False, reasons=reasons
            )

        run = j.workflows.get_run(ws, workflow_run_id)
        if run is None:
            return refuse("WorkflowRun is missing")
        if run.status is not WorkflowStatus.FAILED:
            return refuse(f"Workflow status is {run.status.value}; only failed runs recover")
        if run.superseded_reason:
            return refuse(f"Workflow was superseded ({run.superseded_reason})")
        for other in j.workflows.list_runs(ws):
            if other.parent_run_id == workflow_run_id:
                return refuse("a later rerun already took over this lineage")

        revision = j.workflows.get_revision(ws, run.workflow_revision_id)
        if revision is None:
            return refuse("frozen Workflow revision is missing")
        if j.workflows.get_revocation(ws, run.workflow_revision_id) is not None:
            return refuse("frozen Workflow revision was revoked")

        nodes = j.workflows.list_nodes(ws, workflow_run_id)
        failed = [n for n in nodes if n.status is WorkflowStatus.FAILED]
        if len(failed) != 1:
            return refuse(f"expected exactly one failed node, found {len(failed)}")
        failed_node = failed[0]
        restart_ids: list[str] = []
        for node in nodes:
            if (
                node.status is WorkflowStatus.COMPLETED
                or node.node_run_id == failed_node.node_run_id
            ):
                continue
            if node.status is WorkflowStatus.CANCELLED:
                if j.workflows.segments_for_node(ws, node.node_run_id):
                    return refuse(
                        f"cancelled node {node.node_id} has execution segments; "
                        "its failure provenance cannot be proven"
                    )
                if j.workflows.get_leaf_ownership(ws, node.node_run_id) is not None:
                    return refuse(
                        f"cancelled node {node.node_id} owns a leaf; "
                        "it was started, so it cannot be auto-requeued"
                    )
                if node.agent_run_id is not None:
                    return refuse(
                        f"cancelled node {node.node_id} has an agent run; "
                        "it was started, so it cannot be auto-requeued"
                    )
                restart_ids.append(node.node_run_id)
                continue
            return refuse(
                f"node {node.node_id} is {node.status.value}; only one failed "
                "node with completed or never-started siblings recovers"
            )

        definition = next((n for n in revision.nodes if n.node_id == failed_node.node_id), None)
        if definition is None:
            return refuse("failed node is missing from the frozen revision")
        ref = definition.agent_definition_ref
        version = j.agent_definitions.get_version(ws, ref.version_id)
        if version is None or (
            version.source.definition_id,
            version.content_hash,
        ) != (ref.definition_id, ref.content_hash):
            return refuse("frozen Agent evidence is inconsistent")
        if j.agent_definitions.get_revocation(ws, ref.version_id) is not None:
            return refuse("frozen Agent definition was revoked")

        ownership = j.workflows.get_leaf_ownership(ws, failed_node.node_run_id)
        leaf_session_id = failed_node.conversation_session_id
        if failed_node.leaf_task_run_id is None:
            return refuse("failed node has no bound leaf Task")
        if ownership != (leaf_session_id, failed_node.leaf_task_run_id):
            return refuse("leaf ownership does not match the node's leaf Session/Task")

        leaf_task = j.get_task_run(ws, failed_node.leaf_task_run_id)
        if leaf_task is None:
            return refuse("leaf TaskRun is missing")
        if leaf_task.status not in (TaskRunStatus.FAILED, TaskRunStatus.OPEN):
            return refuse(f"leaf Task is {leaf_task.status.value}; it must be failed or open")
        leaf_session = j.get_session(ws, leaf_session_id)
        if leaf_session is None:
            return refuse("leaf Session is missing")
        if leaf_session.current_task_run_id != leaf_task.task_run_id:
            return refuse("leaf Task is no longer the Session's current task")

        segments = j.workflows.segments_for_node(ws, failed_node.node_run_id)
        if not segments:
            return refuse("failed node has no execution segments")
        last = segments[-1]
        if last.status not in ("interrupted", "active"):
            return refuse(f"ending segment is {last.status}; it contradicts the failure")
        if last.agent_run_id is None:
            return refuse("ending segment has no AgentRun to vouch for the failure")
        metrics = j.get_agent_run_terminal_metrics(ws, last.agent_run_id)
        if metrics is None:
            return refuse("request ledger holds no terminal metrics for the last failure")
        if metrics.finish_reason != "error":
            return refuse(
                f"last failure finished as {metrics.finish_reason}; only an error settles"
            )
        if metrics.stop_code not in RECOVERABLE_STOP_CODES:
            return refuse(
                f"last failure stop_code={metrics.stop_code} is not a vouched Provider-layer fault"
            )

        root = j.get_task_run(ws, run.root_task_run_id)
        if root is None:
            return refuse("root TaskRun is missing")
        if root.status not in (TaskRunStatus.FAILED, TaskRunStatus.OPEN):
            return refuse(f"root Task is {root.status.value}; it must be failed or open")
        root_session = j.get_session(ws, root.session_id)
        if root_session is None or root_session.current_task_run_id != root.task_run_id:
            return refuse("root Task is no longer the Session's current task")

        if j.has_open_turn_submission(ws, root.session_id):
            return refuse("the root Session still has an open Turn")
        if j.has_open_turn_submission(ws, leaf_session_id):
            return refuse("the leaf Session still has an open Turn")

        log = restore_conversation_log(j, ws, leaf_session_id)
        report = self.recovery.discover(leaf_session_id, log)
        if report is not None and any(item.blocking for item in report.items):
            return refuse("unresolved Recovery items block the failure boundary")

        if run.admission_deadline_at is not None and self.clock() > run.admission_deadline_at:
            return refuse("the original admission deadline has passed")
        cap = run.budget_snapshot.max_agent_generation_requests
        if cap is not None:
            used = j.count_lineage_agent_requests(ws, run.effective_lineage_budget_root_run_id)
            if cap - used <= 0:
                return refuse("the original lineage budget is exhausted")

        return HistoricalRecoveryAssessment(
            workflow_run_id=workflow_run_id,
            eligible=True,
            failed_node_run_id=failed_node.node_run_id,
            leaf_task_run_id=leaf_task.task_run_id,
            leaf_session_id=leaf_session_id,
            leaf_segment_id=last.segment_id,
            leaf_agent_run_id=last.agent_run_id,
            stop_code=metrics.stop_code,
            restart_node_run_ids=tuple(restart_ids),
        )

    # Recovery transaction ------------------------------------------------------

    def recover(self, workflow_run_id: str, *, command_id: str | None = None) -> WorkflowRun:
        """Reopen an eligible failed run inside one restricted transaction.

        Idempotent per ``command_id``: a repeated command replays without
        mutating, and a reused command id with a different request conflicts.
        """

        ws = self.workspace_id
        from morrow.application.api_context import request_digest as compute_digest

        digest = compute_digest(RECOVERY_OPERATION, {"workflow_run_id": workflow_run_id})

        def work(txn) -> WorkflowRun:
            run = txn.workflows.get_run(ws, workflow_run_id)
            if run is None:
                raise ApplicationError(ApplicationErrorCode.NOT_FOUND, "WorkflowRun is missing")
            receipt_id = f"{command_id[:110]}_recover" if command_id else None
            receipt = txn.get_application_command_receipt(ws, receipt_id) if receipt_id else None
            if receipt is not None:
                if receipt.operation != RECOVERY_OPERATION or receipt.request_digest != digest:
                    raise ApplicationError(
                        ApplicationErrorCode.CONFLICT,
                        "command ID was reused with a different request",
                    )
                return txn.workflows.get_run(ws, workflow_run_id)
            if run.status is WorkflowStatus.PAUSED:
                # A prior recovery already committed; the durable pause state
                # owns the continuation and the resume flow takes over.
                return run
            if run.status is not WorkflowStatus.FAILED:
                raise ApplicationError(
                    ApplicationErrorCode.INVALID,
                    f"only a failed Workflow recovers; current status is {run.status.value}",
                )

            assessment = self._assess_on(txn, workflow_run_id)
            if not assessment.eligible:
                raise ApplicationError(
                    ApplicationErrorCode.INVALID,
                    "historical recovery refused: " + "; ".join(assessment.reasons),
                )

            now = self.clock()
            paused = txn.workflows.save_recovered_run(
                run.model_copy(
                    update={
                        "status": WorkflowStatus.PAUSED,
                        "pause_requested": True,
                        "completed_at": None,
                        "row_version": run.row_version + 1,
                    }
                ),
                expected_row_version=run.row_version,
            )

            failed_node = txn.workflows.get_node(ws, assessment.failed_node_run_id)
            leaf = txn.get_task_run(ws, assessment.leaf_task_run_id)
            if leaf.status is TaskRunStatus.FAILED:
                txn.transition_workflow_task(
                    ws,
                    workflow_run_id,
                    leaf.task_run_id,
                    target=TaskRunStatus.OPEN,
                    transition=self._transition_record(leaf, TaskRunStatus.OPEN, command_id),
                    expected_row_version=leaf.row_version,
                )
            root = txn.get_task_run(ws, run.root_task_run_id)
            if root.status is TaskRunStatus.FAILED:
                txn.transition_workflow_task(
                    ws,
                    workflow_run_id,
                    root.task_run_id,
                    target=TaskRunStatus.OPEN,
                    transition=self._transition_record(root, TaskRunStatus.OPEN, command_id),
                    expected_row_version=root.row_version,
                )

            segment = txn.workflows.current_segment(assessment.failed_node_run_id)
            if segment is not None and segment.status == "active":
                # The runtime never finished settling this boundary; the
                # interruption fact the ledger vouches for lands now so the
                # successor segment can reference it.
                txn.workflows.close_segment(
                    ws,
                    segment.segment_id,
                    status="interrupted",
                    pause_reason="provider_failure",
                    expected_row_version=segment.row_version,
                )
            txn.workflows.save_recovered_node(
                failed_node.model_copy(
                    update={
                        "status": WorkflowStatus.RUNNING,
                        "completed_at": None,
                        "row_version": failed_node.row_version + 1,
                    }
                ),
                expected_row_version=failed_node.row_version,
            )
            for node_run_id in assessment.restart_node_run_ids:
                node = txn.workflows.get_node(ws, node_run_id)
                txn.workflows.save_recovered_node(
                    node.model_copy(
                        update={
                            "status": WorkflowStatus.QUEUED,
                            "completed_at": None,
                            "row_version": node.row_version + 1,
                        }
                    ),
                    expected_row_version=node.row_version,
                )

            self._insert_pause_point(txn, assessment, command_id, now)

            if command_id is not None:
                txn.put_application_command_receipt_in_txn(
                    ws,
                    ApplicationCommandReceipt(
                        command_id=receipt_id,
                        workspace_id=ws,
                        operation=RECOVERY_OPERATION,
                        request_digest=digest,
                        disposition=ApplicationCommandDisposition.ACCEPTED,
                        result_kind="workflow_run",
                        result_id=workflow_run_id,
                    ),
                )
            return paused

        return self.journal.transact(work)

    def _insert_pause_point(
        self,
        txn,
        assessment: HistoricalRecoveryAssessment,
        command_id: str | None,
        now: datetime,
    ) -> None:
        ws = self.workspace_id
        generation = txn.workflows.execution_pause.next_control_generation(
            ws, owner="workflow_run", owner_id=assessment.workflow_run_id
        )
        requests = txn.list_model_requests(ws, assessment.leaf_agent_run_id)
        terminal = (
            restore_conversation_log(txn, ws, assessment.leaf_session_id).snapshot().records[-1]
        )
        point = WorkflowPausePoint(
            pause_point_id=f"recover_{assessment.workflow_run_id}_{generation}",
            workspace_id=ws,
            fact=PauseIntentFact(
                control_generation=generation,
                command_id=command_id or f"recover_{assessment.workflow_run_id}_{generation}",
                owner="workflow_run",
                owner_id=assessment.workflow_run_id,
                reason="provider_failure",
                lifecycle="suspended",
                requested_at=now,
                suspended_at=now,
            ),
            node_run_id=assessment.failed_node_run_id,
            safety=PauseSafetyPoint(
                stop_code=getattr(terminal, "stop_code", None),
                task_run_id=assessment.leaf_task_run_id,
                committed_position=getattr(terminal, "sequence", None),
                interrupted_turn_id=getattr(terminal, "turn_id", None),
                interrupted_agent_run_id=assessment.leaf_agent_run_id,
                interrupted_request_id=(requests[-1].model_request_id if requests else None),
                note="historical serial provider failure reopened for continuation",
            ),
            created_at=now,
            updated_at=now,
        )
        txn.workflows.execution_pause.insert_pause_point(point)

    def _transition_record(
        self, task, target: TaskRunStatus, command_id: str | None
    ) -> DurableTaskRunTransition:
        return DurableTaskRunTransition(
            transition_id=self.id_source.new_id(TASK_TRANSITION_ID_PREFIX),
            workspace_id=self.workspace_id,
            session_id=task.session_id,
            task_run_id=task.task_run_id,
            from_status=task.status,
            to_status=target,
            reason="workflow_historical_recovery",
            command_id=command_id,
            attempt=task.attempt + (1 if task.status is TaskRunStatus.FAILED else 0),
            created_at=self.clock(),
        )
