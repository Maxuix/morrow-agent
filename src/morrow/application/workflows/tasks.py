"""Explicit Scheduler/Recovery lifecycle boundary; not a Workflow executor."""

from morrow.core.workflows.ports import WorkflowTaskLifecyclePort


class WorkflowTaskLifecycle:
    def __init__(self, journal: WorkflowTaskLifecyclePort, *, workspace_id: str):
        self.journal = journal
        self.workspace_id = workspace_id

    def create_leaf(self, node_run_id, session, task):
        return self.journal.create_workflow_leaf(self.workspace_id, node_run_id, session, task)

    def create_turn(self, node_run_id, turn):
        return self.journal.create_workflow_turn(self.workspace_id, node_run_id, turn)

    def transition(self, workflow_run_id, task_run_id, *, target, transition, expected_row_version):
        return self.journal.transition_workflow_task(
            self.workspace_id,
            workflow_run_id,
            task_run_id,
            target=target,
            transition=transition,
            expected_row_version=expected_row_version,
        )
