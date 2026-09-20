"""Session ownership resolution across the workflow execution chain.

One resolver answers "which workflow, node and root conversation does this
Session belong to" from durable relations only: ``NodeRun.
conversation_session_id`` for isolated node conversations,
``WorkflowRun.root_task_run_id`` joined to ``task_runs`` for root sessions,
and ``workflow_task_plan_provenance`` for the frozen planning context of a
run. Missing relations are reported as explicit issues; the resolver never
substitutes a "nearest" workflow.
"""

from morrow.core.application import ApplicationError, ApplicationErrorCode

_DIRECT_PLAN = {
    "mode": "direct",
    "origin": None,
    "planning_binding_id": None,
    "draft_id": None,
    "draft_version": None,
}


class SessionWorkflowOwnership:
    """Resolves Session -> NodeRun -> WorkflowRun -> root Task -> root Session."""

    def __init__(self, journal, workspace_id):
        self.journal = journal
        self.workspace_id = workspace_id

    def resolve(self, session_id) -> dict:
        if self.journal.get_session(self.workspace_id, session_id) is None:
            raise ApplicationError(ApplicationErrorCode.NOT_FOUND, "Session is missing")
        context = {
            "session_id": session_id,
            "role": "plain",
            "issues": [],
            "node_run_id": None,
            "node_id": None,
            "node_status": None,
            "node_attempt": None,
            "workflow_run_id": None,
            "workflow_status": None,
            "root_task_run_id": None,
            "root_session_id": None,
            "plan": None,
        }
        isolated = []
        rooted_here = False
        for node in self.journal.workflows.nodes_for_conversation_session(
            self.workspace_id, session_id
        ):
            run = self.journal.workflows.get_run(self.workspace_id, node.workflow_run_id)
            if run is None:
                context["issues"].append("run_missing:" + node.workflow_run_id)
                isolated.append(node)
                continue
            root = self.journal.get_task_run(self.workspace_id, run.root_task_run_id)
            if root is not None and root.session_id == session_id:
                # An invoking_session node drives on the root Session itself;
                # that is root ownership, not an isolated execution detail.
                rooted_here = True
                continue
            if root is None:
                context["issues"].append("root_task_missing:" + run.root_task_run_id)
            isolated.append(node)
        if isolated:
            return self._node_context(context, isolated[-1])
        if rooted_here or self.journal.workflows.runs_for_root_session(
            self.workspace_id, session_id
        ):
            context["role"] = "root"
        return context

    def _node_context(self, context, node) -> dict:
        run = self.journal.workflows.get_run(self.workspace_id, node.workflow_run_id)
        context.update(
            role="node",
            node_run_id=node.node_run_id,
            node_id=node.node_id,
            node_status=node.status.value,
            node_attempt=node.attempt,
        )
        if run is None:
            context["plan"] = None
            return context
        context.update(
            workflow_run_id=run.workflow_run_id,
            workflow_status=run.status.value,
            root_task_run_id=run.root_task_run_id,
        )
        root = self.journal.get_task_run(self.workspace_id, run.root_task_run_id)
        if root is not None:
            context["root_session_id"] = root.session_id
        context["plan"] = self._plan_context(run.workflow_revision_id)
        return context

    def _plan_context(self, revision_id):
        provenance = self.journal.workflows.get_task_plan_provenance(self.workspace_id, revision_id)
        if provenance is None:
            return dict(_DIRECT_PLAN)
        return {
            "mode": "frozen",
            "origin": provenance.origin,
            "planning_binding_id": provenance.planning_binding_id,
            "draft_id": provenance.draft_id,
            "draft_version": provenance.draft_version,
        }
