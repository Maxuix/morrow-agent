"""Provider-independent Workflow recovery commands."""

from __future__ import annotations

from morrow.core.application import ApplicationError, ApplicationErrorCode
from morrow.core.workflows.runs import WorkflowRun, WorkflowStatus


class WorkflowAbandonService:
    """Recovery-only abandon over durable Workflow state.

    The command deliberately has no Provider or Session runtime dependency.  A
    foreground scheduler may pass its exact live NodeRun handle; a standalone
    recovery process has no such local handle and passes ``None``.
    """

    def __init__(self, *, transitions, finalizer) -> None:
        self.transitions = transitions
        self.finalizer = finalizer

    def abandon(
        self,
        workflow_run_id: str,
        *,
        expected_row_version: int,
        live_node_run_id: str | None = None,
    ) -> WorkflowRun:
        run = self.transitions.get_run(workflow_run_id)
        if run is None:
            raise ValueError("WorkflowRun is missing")
        if run.status.terminal:
            return run
        if run.status is not WorkflowStatus.BLOCKED:
            raise ApplicationError(
                ApplicationErrorCode.INVALID,
                "only a blocked Workflow can be abandoned; active runs use their owning "
                "foreground cancellation",
            )
        if run.row_version != expected_row_version:
            raise ApplicationError(ApplicationErrorCode.STALE, "Workflow run row version is stale")
        if live_node_run_id is not None:
            live = self.transitions.get_node(live_node_run_id)
            if live is not None and live.workflow_run_id == workflow_run_id:
                raise ApplicationError(
                    ApplicationErrorCode.CONFLICT,
                    "this process still owns a live handle for the Workflow run",
                )
        return self.finalizer.finalize_abandon(workflow_run_id)
