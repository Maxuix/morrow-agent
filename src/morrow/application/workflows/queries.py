"""Read-only Workflow/Node projections for inspection; no business state here."""

from __future__ import annotations

from dataclasses import dataclass

from morrow.core.artifacts import ArtifactMetadata
from morrow.core.workflows.contracts import ArtifactBinding
from morrow.core.workflows.definitions import WorkflowRevision
from morrow.core.workflows.runs import NodeRun, WorkflowRun


@dataclass(frozen=True)
class WorkflowNodeView:
    node: NodeRun
    output_bindings: tuple[ArtifactBinding, ...]
    artifacts: tuple[ArtifactMetadata, ...]


@dataclass(frozen=True)
class WorkflowRunView:
    run: WorkflowRun
    revision: WorkflowRevision
    nodes: tuple[WorkflowNodeView, ...]
    input_artifacts: tuple[ArtifactMetadata, ...]
    agent_generation_request_count: int


class WorkflowQueryService:
    """Minimum projection needed to inspect a run, its nodes and its Artifacts."""

    def __init__(self, journal, *, workspace_id: str) -> None:
        self.journal = journal
        self.workspace_id = workspace_id

    def list_runs(self) -> tuple[WorkflowRun, ...]:
        return self.journal.workflows.list_runs(self.workspace_id)

    def get_run_view(self, workflow_run_id: str) -> WorkflowRunView | None:
        run = self.journal.workflows.get_run(self.workspace_id, workflow_run_id)
        if run is None:
            return None
        revision = self.journal.workflows.get_revision(self.workspace_id, run.workflow_revision_id)
        bindings = self.journal.workflows.list_bindings(self.workspace_id, workflow_run_id)
        nodes = []
        for node in self.journal.workflows.list_nodes(self.workspace_id, workflow_run_id):
            outputs = tuple(
                binding
                for node_run_id, direction, binding in bindings
                if node_run_id == node.node_run_id and direction == "output"
            )
            artifacts = tuple(
                metadata
                for metadata in (
                    self.journal.get_artifact(self.workspace_id, binding.artifact_id)
                    for binding in outputs
                )
                if metadata is not None
            )
            nodes.append(WorkflowNodeView(node=node, output_bindings=outputs, artifacts=artifacts))
        inputs = tuple(
            metadata
            for metadata in (
                self.journal.get_artifact(self.workspace_id, binding.artifact_id)
                for binding in run.input_artifacts
            )
            if metadata is not None
        )
        return WorkflowRunView(
            run=run,
            revision=revision,
            nodes=tuple(nodes),
            input_artifacts=inputs,
            agent_generation_request_count=self.journal.count_workflow_agent_requests(
                self.workspace_id, workflow_run_id
            ),
        )
