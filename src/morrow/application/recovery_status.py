"""Read-only recovery capability projection for the Chat surface.

The recovery service owns durable discovery and decisions. This module only
projects already persisted facts: it never restores a Session runtime, creates
a RecoveryReport, mutates a queue, or starts a driver. That distinction keeps a
status refresh safe to retry and safe to perform automatically after a socket
reconnect.
"""

from __future__ import annotations

from morrow.core.domain import SessionHealth, canonical_json_bytes, sha256_digest
from morrow.core.execution import RecoveryClassification
from morrow.core.recovery import (
    RecoveryAction,
    RecoveryCheck,
    RecoveryDisplayState,
    RecoveryOwner,
    RecoveryReport,
    RecoveryReportStatus,
    RecoveryResolution,
    RecoveryStatus,
)
from morrow.core.workflows.runs import WorkflowRun, WorkflowStatus

_CLASSIFICATION_SUMMARIES = {
    RecoveryClassification.COMPLETED: "执行结果已记录，可确认这项恢复记录。",
    RecoveryClassification.NEVER_STARTED: "执行尚未开始，可确认或结束这项恢复记录。",
    RecoveryClassification.SAFE_TO_RETRY: "执行结果未完成，不能自动重试；请确认或结束这项恢复记录。",
    RecoveryClassification.REQUIRES_RECONCILIATION: "执行结果需要根据已记录的证据核对。",
    RecoveryClassification.OUTCOME_UNKNOWN: "执行结果未知，必须先核对副作用；不能直接重试。",
}


class RecoveryStatusProjection:
    """Build a bounded, safe recovery status from durable owner facts."""

    def __init__(self, *, manager, journal, workspace_id):
        self.manager = manager
        self.journal = journal
        self.workspace_id = workspace_id

    def build(self, session_id: str) -> RecoveryStatus:
        session = self.manager.require_session(session_id)
        execution = getattr(self.manager, "execution", None)
        execution_view = execution.build(session_id) if execution is not None else None
        runs = self._workflow_runs(session_id)

        if session.health is SessionHealth.QUARANTINED:
            return self._make(
                session_id,
                display_state=RecoveryDisplayState.QUARANTINED,
                owner=self._owner_for_runs(runs),
                safe_summary="此对话已被隔离；保留原始证据，不会继续执行。",
                allowed=(RecoveryAction.NEW_SESSION,),
                decision_required=True,
                target=runs[0].workflow_run_id if runs else None,
                execution=execution_view,
                session_health=session.health.value,
            )

        workflow_status = self._workflow_status(session_id, runs, execution_view)
        if workflow_status is not None:
            return workflow_status

        planning_status = self._planning_status(session_id, execution_view, session.health.value)
        if planning_status is not None:
            return planning_status

        report = self.journal.get_open_report(self.workspace_id, session_id)
        if report is not None:
            return self._report_status(report, owner=RecoveryOwner.CHAT, execution=execution_view)

        pending = self._pending_chat_run(session_id)
        if pending is not None:
            return self._make(
                session_id,
                display_state=RecoveryDisplayState.RESUMABLE,
                owner=RecoveryOwner.CHAT,
                safe_summary="上一轮对话已中断；可以从已保存的检查点继续。",
                allowed=(RecoveryAction.RESUME, RecoveryAction.NEW_SESSION),
                decision_required=False,
                target=pending,
                target_kind="agent_run",
                execution=execution_view,
                session_health=session.health.value,
            )

        if session.health is SessionHealth.NEEDS_RECOVERY:
            return self._make(
                session_id,
                display_state=RecoveryDisplayState.NEEDS_REVIEW,
                owner=RecoveryOwner.CHAT,
                safe_summary="此对话需要恢复核对，但当前没有可直接继续的目标。",
                allowed=(RecoveryAction.REVIEW, RecoveryAction.NEW_SESSION),
                decision_required=True,
                execution=execution_view,
                session_health=session.health.value,
            )

        return self._make(
            session_id,
            display_state=RecoveryDisplayState.IDLE,
            owner=None,
            safe_summary="当前没有需要处理的恢复状态。",
            allowed=(),
            decision_required=False,
            execution=execution_view,
            session_health=session.health.value,
        )

    def _workflow_runs(self, session_id: str) -> tuple[WorkflowRun, ...]:
        runs: dict[str, WorkflowRun] = {}
        for run in self.journal.workflows.active_runs_for_root_session(
            self.workspace_id, session_id
        ):
            runs[run.workflow_run_id] = run
        for run in self.journal.workflows.active_runs_for_leaf_session(
            self.workspace_id, session_id
        ):
            runs[run.workflow_run_id] = run
        return tuple(sorted(runs.values(), key=lambda item: item.workflow_run_id))

    @staticmethod
    def _owner_for_runs(runs: tuple[WorkflowRun, ...]) -> RecoveryOwner | None:
        return RecoveryOwner.WORKFLOW if runs else None

    def _workflow_status(self, session_id, runs, execution):
        if not runs:
            return None
        run = runs[0]
        report = self._workflow_report(run, session_id)
        if report is not None:
            return self._report_status(
                report,
                owner=RecoveryOwner.WORKFLOW,
                target=run.workflow_run_id,
                execution=execution,
            )
        if run.status is WorkflowStatus.BLOCKED or (
            execution is not None
            and execution.get("owner") == RecoveryOwner.WORKFLOW.value
            and execution.get("state") == "needs_recovery"
        ):
            return self._make(
                session_id,
                display_state=RecoveryDisplayState.NEEDS_REVIEW,
                owner=RecoveryOwner.WORKFLOW,
                safe_summary="工作流执行需要服务端核对；请先查看运行状态。",
                allowed=(RecoveryAction.REVIEW, RecoveryAction.NEW_SESSION),
                decision_required=True,
                target=run.workflow_run_id,
                execution=execution,
                session_health=None,
            )
        if run.status is WorkflowStatus.PAUSED:
            return self._make(
                session_id,
                display_state=RecoveryDisplayState.PAUSED,
                owner=RecoveryOwner.WORKFLOW,
                safe_summary="工作流已暂停；可以由工作流 owner 继续。",
                allowed=(RecoveryAction.CONTINUE, RecoveryAction.NEW_SESSION),
                decision_required=False,
                target=run.workflow_run_id,
                execution=execution,
                session_health=None,
            )
        return None

    def _workflow_report(self, run: WorkflowRun, session_id: str) -> RecoveryReport | None:
        # A report belongs to the isolated leaf Session, while the user-facing
        # recovery banner normally lives on the root Session. Resolve the
        # owning leaf without exposing that implementation detail in the UI.
        candidates: list[str] = [session_id]
        for node in self.journal.workflows.list_nodes(self.workspace_id, run.workflow_run_id):
            if node.conversation_session_id and node.conversation_session_id not in candidates:
                candidates.append(node.conversation_session_id)
        for candidate in candidates:
            report = self.journal.get_open_report(self.workspace_id, candidate)
            if report is not None:
                return report
        return None

    def _planning_status(self, session_id, execution, session_health):
        planning = getattr(self.manager, "planning", None)
        if planning is None:
            return None
        operations = planning.repo.operations(self.workspace_id, session_id)
        open_operations = [item for item in operations if item.status in {"queued", "running"}]
        if open_operations:
            operation = open_operations[-1]
            lifecycle = planning.repo.latest_pause_lifecycle(
                self.workspace_id, operation.planning_operation_id
            )
            if lifecycle == "suspended":
                return self._make(
                    session_id,
                    display_state=RecoveryDisplayState.PAUSED,
                    owner=RecoveryOwner.PLANNING,
                    safe_summary="计划生成已暂停；可以从生成检查点继续。",
                    allowed=(RecoveryAction.RESUME_GENERATION, RecoveryAction.NEW_SESSION),
                    decision_required=False,
                    target=operation.planning_operation_id,
                    execution=execution,
                    session_health=session_health,
                )
            if lifecycle in {"requested", "quiescing"}:
                return self._make(
                    session_id,
                    display_state=RecoveryDisplayState.CHECKING,
                    owner=RecoveryOwner.PLANNING,
                    safe_summary="计划生成正在等待安全暂停点。",
                    allowed=(RecoveryAction.NEW_SESSION,),
                    decision_required=False,
                    target=operation.planning_operation_id,
                    execution=execution,
                    session_health=session_health,
                )
            return None

        failed = [
            item
            for item in operations
            if item.status == "failed" and item.error_code == "needs_recovery"
        ]
        if failed:
            operation = failed[-1]
            return self._make(
                session_id,
                display_state=RecoveryDisplayState.UNSUPPORTED,
                owner=RecoveryOwner.PLANNING,
                safe_summary="这次计划生成无法安全接续；请在新对话中重新说明目标。",
                allowed=(RecoveryAction.NEW_SESSION, RecoveryAction.REVIEW),
                decision_required=True,
                target=operation.planning_operation_id,
                execution=execution,
                session_health=session_health,
            )
        return None

    def _report_status(self, report, *, owner, target=None, execution):
        if report.status is RecoveryReportStatus.QUARANTINED:
            state = RecoveryDisplayState.QUARANTINED
        elif any(
            item.classification is RecoveryClassification.OUTCOME_UNKNOWN
            for item in report.blocking_open
        ):
            state = RecoveryDisplayState.UNKNOWN_SIDE_EFFECT
        elif report.blocking_open:
            state = RecoveryDisplayState.NEEDS_REVIEW
        else:
            state = RecoveryDisplayState.RESUMABLE

        checks = tuple(
            RecoveryCheck(
                summary=self._check_summary(item),
                classification=item.classification.value,
                allowed_actions=tuple(
                    resolution.value
                    for resolution in item.allowed_resolutions
                    if resolution is not RecoveryResolution.RETRY
                ),
                opaque_target=report.report_id,
                opaque_item=item.item_id,
            )
            for item in report.items
            if item.resolution is None
        )
        actions: list[RecoveryAction] = []
        if report.blocking_open:
            for item in report.blocking_open:
                for resolution in item.allowed_resolutions:
                    if resolution is RecoveryResolution.ACKNOWLEDGE:
                        self._append_unique(actions, RecoveryAction.ACKNOWLEDGE)
                    elif resolution is RecoveryResolution.ABORT:
                        self._append_unique(actions, RecoveryAction.ABORT)
                    elif resolution is RecoveryResolution.QUARANTINE:
                        self._append_unique(actions, RecoveryAction.QUARANTINE)
        else:
            actions.append(
                RecoveryAction.RESUME_WORKFLOW
                if owner is RecoveryOwner.WORKFLOW
                else RecoveryAction.RESUME
            )
        if owner is RecoveryOwner.CHAT:
            self._append_unique(actions, RecoveryAction.NEW_SESSION)
        else:
            self._append_unique(actions, RecoveryAction.REVIEW)
            self._append_unique(actions, RecoveryAction.NEW_SESSION)
        summary = (
            "上一执行存在未知副作用，必须先核对后决定。"
            if state is RecoveryDisplayState.UNKNOWN_SIDE_EFFECT
            else "上一执行需要处理恢复项后才能继续。"
            if state is RecoveryDisplayState.NEEDS_REVIEW
            else "恢复项已处理，可以由 owner 继续原执行。"
        )
        return self._make(
            report.session_id,
            display_state=state,
            owner=owner,
            safe_summary=summary,
            allowed=tuple(actions),
            decision_required=bool(report.blocking_open),
            target=target or report.report_id,
            target_kind="report" if owner is RecoveryOwner.CHAT else "workflow_run",
            checks=checks,
            execution=execution,
            session_health=None,
        )

    @staticmethod
    def _check_summary(item) -> str:
        classification = item.classification
        detail = _CLASSIFICATION_SUMMARIES.get(classification, "执行项需要根据保存的证据核对。")
        return f"有一项工具执行需要处理：{detail}"

    def _pending_chat_run(self, session_id: str) -> str | None:
        if session_id in getattr(self.manager, "drivers", {}):
            return None
        control = self.journal.interactions.control(session_id)
        if not control["paused"]:
            return None
        entry = self.journal.interactions.latest(self.workspace_id, session_id)
        if entry is None or entry.get("agent_run_id") is None:
            return None
        if entry.get("status") not in {"consumed", "blocked"}:
            return None
        target = entry["agent_run_id"]
        if self.journal.get_agent_run_terminal_metrics(self.workspace_id, target) is not None:
            return None
        return target

    def _make(
        self,
        session_id,
        *,
        display_state,
        owner,
        safe_summary,
        allowed,
        decision_required,
        target=None,
        target_kind=None,
        checks=(),
        execution=None,
        session_health=None,
    ):
        if target_kind is None and target is not None:
            target_kind = (
                "workflow_run"
                if owner is RecoveryOwner.WORKFLOW
                else "planning_operation"
                if owner is RecoveryOwner.PLANNING
                else "agent_run"
            )
        revision_source = {
            "session_id": session_id,
            "display_state": display_state.value,
            "owner": owner.value if owner is not None else None,
            "target": target,
            "target_kind": target_kind,
            "checks": [check.model_dump(mode="json") for check in checks],
            "execution_revision": execution.get("revision") if execution else None,
            "session_health": session_health,
        }
        revision = int(sha256_digest(canonical_json_bytes(revision_source))[:12], 16)
        return RecoveryStatus(
            display_state=display_state,
            owner=owner,
            safe_summary=safe_summary,
            allowed_actions=tuple(
                action.value if isinstance(action, RecoveryAction) else action for action in allowed
            ),
            opaque_target=target,
            opaque_target_kind=target_kind,
            decision_required=decision_required,
            revision=revision,
            checks=tuple(checks),
        )

    @staticmethod
    def _append_unique(values: list[RecoveryAction], value: RecoveryAction) -> None:
        if value not in values:
            values.append(value)
