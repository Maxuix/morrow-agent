"""Narrow persistence and internal lifecycle ports for later Workflow consumers."""

from typing import Protocol

from morrow.core.domain import (
    DurableSession,
    DurableTaskRun,
    DurableTaskRunTransition,
    DurableTurn,
    TaskRunStatus,
)
from morrow.core.workflows.contracts import ArtifactBinding
from morrow.core.workflows.definitions import (
    WorkflowDefinitionHead,
    WorkflowRevision,
    WorkflowRevisionRevocation,
)
from morrow.core.workflows.runs import NodeRun, WorkflowRun


class WorkflowJournalPort(Protocol):
    def get_revision(self, workspace_id: str, revision_id: str) -> WorkflowRevision | None: ...
    def list_revisions(self, workspace_id: str) -> tuple[WorkflowRevision, ...]: ...
    def get_head(self, workspace_id: str, definition_id: str) -> WorkflowDefinitionHead | None: ...
    def store_compiled_revision(
        self, revision: WorkflowRevision, head: WorkflowDefinitionHead, *, expected_row_version: int
    ) -> WorkflowRevision: ...
    def set_enabled(
        self, workspace_id: str, definition_id: str, *, enabled: bool, expected_row_version: int
    ) -> WorkflowDefinitionHead: ...
    def get_revocation(
        self, workspace_id: str, revision_id: str
    ) -> WorkflowRevisionRevocation | None: ...
    def put_revocation(self, value: WorkflowRevisionRevocation) -> WorkflowRevisionRevocation: ...
    def create_run(self, value: WorkflowRun, nodes: tuple[NodeRun, ...]) -> WorkflowRun: ...
    def get_run(self, workspace_id: str, run_id: str) -> WorkflowRun | None: ...
    def get_node(self, workspace_id: str, node_run_id: str) -> NodeRun | None: ...
    def list_nodes(self, workspace_id: str, workflow_run_id: str) -> tuple[NodeRun, ...]: ...
    def save_run(self, value: WorkflowRun, *, expected_row_version: int) -> WorkflowRun: ...
    def save_node(self, value: NodeRun, *, expected_row_version: int) -> NodeRun: ...
    def active_for_root(self, workspace_id: str, task_run_id: str) -> WorkflowRun | None: ...
    def bind_artifact(
        self,
        workspace_id: str,
        run_id: str,
        binding: ArtifactBinding,
        *,
        node_run_id: str = "",
        direction: str = "input",
    ) -> ArtifactBinding: ...


class WorkflowTaskLifecyclePort(Protocol):
    def create_workflow_leaf(
        self, workspace_id: str, node_run_id: str, session: DurableSession, task: DurableTaskRun
    ) -> DurableTaskRun: ...
    def create_workflow_turn(
        self, workspace_id: str, node_run_id: str, turn: DurableTurn
    ) -> DurableTurn: ...
    def transition_workflow_task(
        self,
        workspace_id: str,
        workflow_run_id: str,
        task_run_id: str,
        *,
        target: TaskRunStatus,
        transition: DurableTaskRunTransition,
        expected_row_version: int,
    ) -> DurableTaskRun: ...
