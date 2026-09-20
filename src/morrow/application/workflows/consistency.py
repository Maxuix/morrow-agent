"""Dry-run consistency check and safe settlement for finished Workflow runs.

The scan is read-only and bounded: it lists durable residue that a finished run
should never keep — an open leaf TaskRun, an unclosed leaf turn, a missing
terminal metric, a non-terminal node on a terminal run, an open root task, or a
blocking recovery item.

Settlement is opt-in, idempotent and auditable. It only closes a leaf TaskRun
through the journal's owned-leaf transition (terminal Workflows may drain owned
leaf Tasks) when the leaf has no open turn and no unresolved blocking recovery
item; it never runs a business tool, never rewrites a cancelled run back to
running, and never fabricates a tool result.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from morrow.core.application import ApplicationCommandReceipt
from morrow.core.domain import (
    TASK_TRANSITION_ID_PREFIX,
    DurableTaskRunTransition,
    TaskRunStatus,
    canonical_json_bytes,
    sha256_digest,
)
from morrow.core.workflows.runs import WorkflowStatus

CONSISTENCY_OPERATION = "workflow_consistency_settle"
MAX_RUNS = 200

NODE_OPEN_STATUSES = frozenset(
    {WorkflowStatus.QUEUED, WorkflowStatus.RUNNING, WorkflowStatus.BLOCKED}
)


@dataclass(frozen=True)
class ConsistencyFinding:
    kind: str
    workflow_run_id: str
    session_id: str | None = None
    node_run_id: str | None = None
    task_run_id: str | None = None
    agent_run_id: str | None = None
    detail: str = ""

    def wire(self) -> dict:
        return {
            "kind": self.kind,
            "workflow_run_id": self.workflow_run_id,
            "session_id": self.session_id,
            "node_run_id": self.node_run_id,
            "task_run_id": self.task_run_id,
            "agent_run_id": self.agent_run_id,
            "detail": self.detail,
        }


@dataclass
class ConsistencyReport:
    findings: list[ConsistencyFinding] = field(default_factory=list)
    runs_scanned: int = 0
    settled: list[dict] = field(default_factory=list)
    skipped: list[dict] = field(default_factory=list)
    replayed: bool = False

    def wire(self) -> dict:
        return {
            "runs_scanned": self.runs_scanned,
            "findings": [item.wire() for item in self.findings],
            "settled": list(self.settled),
            "skipped": list(self.skipped),
            "replayed": self.replayed,
        }


class WorkflowConsistencyService:
    def __init__(self, journal, workspace_id, *, id_source=None, clock=None):
        self.journal = journal
        self.workspace_id = workspace_id
        self.id_source = id_source
        self.clock = clock or journal.now

    # Scan --------------------------------------------------------------------

    def scan(self, *, session_id: str | None = None) -> ConsistencyReport:
        report = ConsistencyReport()
        for run in self._runs(session_id):
            report.runs_scanned += 1
            if not run.status.terminal:
                continue
            report.findings.extend(self._run_findings(run))
        return report

    def _runs(self, session_id):
        if session_id is not None:
            return self.journal.workflows.runs_for_root_session(self.workspace_id, session_id)[
                -MAX_RUNS:
            ]
        return self.journal.workflows.list_runs(self.workspace_id)[-MAX_RUNS:]

    def _run_findings(self, run) -> list[ConsistencyFinding]:
        findings: list[ConsistencyFinding] = []
        root = self.journal.get_task_run(self.workspace_id, run.root_task_run_id)
        session_id = root.session_id if root is not None else None
        if root is not None and not root.status.is_terminal:
            findings.append(
                ConsistencyFinding(
                    "root_task_open",
                    run.workflow_run_id,
                    session_id,
                    task_run_id=root.task_run_id,
                    detail=f"root TaskRun is {root.status.value}",
                )
            )
        for node in self.journal.workflows.list_nodes(self.workspace_id, run.workflow_run_id):
            if node.status in NODE_OPEN_STATUSES:
                findings.append(
                    ConsistencyFinding(
                        "node_not_terminal",
                        run.workflow_run_id,
                        session_id,
                        node_run_id=node.node_run_id,
                        detail=f"NodeRun is {node.status.value}",
                    )
                )
            leaf = (
                self.journal.get_task_run(self.workspace_id, node.leaf_task_run_id)
                if node.leaf_task_run_id
                else None
            )
            if leaf is not None and not leaf.status.is_terminal:
                findings.append(
                    ConsistencyFinding(
                        "open_leaf_task",
                        run.workflow_run_id,
                        session_id,
                        node_run_id=node.node_run_id,
                        task_run_id=leaf.task_run_id,
                        detail=f"leaf TaskRun is {leaf.status.value}",
                    )
                )
            if node.conversation_session_id and self.journal.has_open_turn_submission(
                self.workspace_id, node.conversation_session_id
            ):
                findings.append(
                    ConsistencyFinding(
                        "open_leaf_turn",
                        run.workflow_run_id,
                        session_id,
                        node_run_id=node.node_run_id,
                        agent_run_id=node.agent_run_id,
                        detail="leaf session still holds an open turn submission",
                    )
                )
            if node.agent_run_id and (
                self.journal.get_agent_run_terminal_metrics(self.workspace_id, node.agent_run_id)
                is None
            ):
                findings.append(
                    ConsistencyFinding(
                        "missing_terminal_metrics",
                        run.workflow_run_id,
                        session_id,
                        node_run_id=node.node_run_id,
                        agent_run_id=node.agent_run_id,
                        detail="AgentRun has no terminal metrics",
                    )
                )
        if session_id is not None:
            open_report = self.journal.get_open_report(self.workspace_id, session_id)
            if open_report is not None and any(
                item.blocking and item.resolution is None for item in open_report.items
            ):
                findings.append(
                    ConsistencyFinding(
                        "blocking_recovery",
                        run.workflow_run_id,
                        session_id,
                        detail="unresolved blocking recovery item; resolve it explicitly",
                    )
                )
        return findings

    # Settle ------------------------------------------------------------------

    def settle(self, *, session_id: str | None = None, command_id: str) -> ConsistencyReport:
        """Close only what is provably safe; everything else stays reported."""

        report = self.scan(session_id=session_id)
        # The request identity is the target scope, not the current finding
        # list: a retry after a successful settle must replay, not conflict
        # because the residue is already gone.
        digest = sha256_digest(
            canonical_json_bytes({"operation": CONSISTENCY_OPERATION, "session_id": session_id})
        )
        existing = self.journal.get_application_command_receipt(self.workspace_id, command_id)
        if existing is not None:
            if existing.operation != CONSISTENCY_OPERATION or existing.request_digest != digest:
                raise ValueError("consistency command ID was reused with a different request")
            report.replayed = True
            return report

        blocked_runs = {
            item.workflow_run_id for item in report.findings if item.kind == "blocking_recovery"
        }
        for item in report.findings:
            if item.kind != "open_leaf_task" or item.task_run_id is None:
                continue
            if item.workflow_run_id in blocked_runs:
                report.skipped.append({**item.wire(), "reason": "blocking_recovery"})
                continue
            if any(
                other.kind == "open_leaf_turn" and other.node_run_id == item.node_run_id
                for other in report.findings
            ):
                report.skipped.append({**item.wire(), "reason": "open_turn_needs_recovery"})
                continue
            applied = self._close_leaf(item)
            if applied:
                report.settled.append(applied)
            else:
                report.skipped.append({**item.wire(), "reason": "already_terminal"})
        self.journal.put_application_command_receipt(
            self.workspace_id,
            ApplicationCommandReceipt(
                command_id=command_id,
                workspace_id=self.workspace_id,
                session_id=session_id,
                operation=CONSISTENCY_OPERATION,
                request_digest=digest,
                result_kind="workflow_run",
                result_id=report.settled[0]["workflow_run_id"] if report.settled else None,
            ),
        )
        return report

    def _close_leaf(self, item: ConsistencyFinding) -> dict | None:
        leaf = self.journal.get_task_run(self.workspace_id, item.task_run_id or "")
        if leaf is None or leaf.status.is_terminal:
            return None
        transition = DurableTaskRunTransition(
            transition_id=(
                self.id_source.new_id(TASK_TRANSITION_ID_PREFIX)
                if self.id_source is not None
                else f"ttr_consistency_{leaf.task_run_id}"
            ),
            workspace_id=self.workspace_id,
            session_id=leaf.session_id,
            task_run_id=leaf.task_run_id,
            from_status=leaf.status,
            to_status=TaskRunStatus.CANCELLED,
            reason="consistency_settle",
            attempt=leaf.attempt,
            created_at=self.clock(),
        )
        self.journal.transition_workflow_task(
            self.workspace_id,
            item.workflow_run_id,
            leaf.task_run_id,
            target=TaskRunStatus.CANCELLED,
            transition=transition,
            expected_row_version=leaf.row_version,
        )
        return {
            "workflow_run_id": item.workflow_run_id,
            "node_run_id": item.node_run_id,
            "task_run_id": leaf.task_run_id,
            "from_status": leaf.status.value,
            "to_status": TaskRunStatus.CANCELLED.value,
            "reason": "consistency_settle",
        }
