"""Read-only Workflow/Node projections for inspection; no business state here."""

from __future__ import annotations

from dataclasses import dataclass

from morrow.core.artifacts import ArtifactMetadata
from morrow.core.execution import ToolExecutionState
from morrow.core.workflows.contracts import ArtifactBinding
from morrow.core.workflows.definitions import WorkflowRevision
from morrow.core.workflows.runs import NodeRun, WorkflowArtifactImport, WorkflowRun, WorkflowStatus


@dataclass(frozen=True)
class WorkflowNodeView:
    node: NodeRun
    output_bindings: tuple[ArtifactBinding, ...]
    artifacts: tuple[ArtifactMetadata, ...]
    approval_pending: bool = False


@dataclass(frozen=True)
class WorkflowRunView:
    run: WorkflowRun
    revision: WorkflowRevision
    nodes: tuple[WorkflowNodeView, ...]
    input_artifacts: tuple[ArtifactMetadata, ...]
    agent_generation_request_count: int
    lineage_agent_generation_request_count: int
    inherited_artifacts: tuple[WorkflowArtifactImport, ...]
    usage_availability: str
    terminal_outcome: object | None
    actionable_status: str | None


@dataclass(frozen=True)
class WorkflowRunRecoveryView:
    """Minimum authoritative context needed to compose run recovery."""

    workflow_run_id: str
    session_id: str


@dataclass(frozen=True)
class AgentDefinitionView:
    definition_id: str
    source: object | None
    source_revision: int | None
    origin: str
    head: object | None
    published_version: object | None
    revoked: bool
    desired_ahead_of_published: bool


@dataclass(frozen=True)
class WorkflowDefinitionView:
    workflow_definition_id: str
    source: object | None
    source_revision: int | None
    origin: str
    head: object | None
    published_revision: WorkflowRevision | None
    revoked: bool
    desired_ahead_of_published: bool


@dataclass(frozen=True)
class WorkflowRevisionView:
    revision: WorkflowRevision
    revoked: bool


class WorkflowQueryService:
    """Minimum projection needed to inspect a run, its nodes and its Artifacts."""

    def __init__(
        self,
        journal,
        *,
        workspace_id: str,
        agent_sources=None,
        workflow_sources=None,
        agent_builtins=(),
        workflow_builtins=(),
    ) -> None:
        self.journal = journal
        self.workspace_id = workspace_id
        self.agent_sources = agent_sources
        self.workflow_sources = workflow_sources
        self.agent_builtins = {item.definition_id: item for item in agent_builtins}
        self.workflow_builtins = {item.workflow_definition_id: item for item in workflow_builtins}

    def list_runs(self, *, limit: int = 100, after: str | None = None) -> tuple[WorkflowRun, ...]:
        values = self.journal.workflows.list_runs(self.workspace_id)
        return self._page(values, key=lambda item: item.workflow_run_id, limit=limit, after=after)

    def list_agent_definitions(
        self, *, limit: int = 100, after: str | None = None
    ) -> tuple[AgentDefinitionView, ...]:
        desired, source_revision = self._desired_agents()
        versions = self.journal.agent_definitions.list_versions(self.workspace_id)
        ids = (
            set(desired)
            | set(self.agent_builtins)
            | {item.source.definition_id for item in versions}
        )
        views = []
        for identity in sorted(ids):
            source = self.agent_builtins.get(identity) or desired.get(identity)
            origin = "builtin" if identity in self.agent_builtins else "user"
            head = self.journal.agent_definitions.get_head(self.workspace_id, identity)
            version = (
                self.journal.agent_definitions.get_version(self.workspace_id, head.version_id)
                if head is not None
                else None
            )
            views.append(
                AgentDefinitionView(
                    definition_id=identity,
                    source=source,
                    source_revision=0 if origin == "builtin" else source_revision,
                    origin=origin,
                    head=head,
                    published_version=version,
                    revoked=bool(
                        version
                        and self.journal.agent_definitions.get_revocation(
                            self.workspace_id, version.version_id
                        )
                    ),
                    desired_ahead_of_published=bool(
                        source is not None
                        and (head is None or head.source_hash != source.content_hash)
                    ),
                )
            )
        return self._page(
            tuple(views), key=lambda item: item.definition_id, limit=limit, after=after
        )

    def get_agent_definition(self, definition_id: str) -> AgentDefinitionView | None:
        desired, source_revision = self._desired_agents()
        source = self.agent_builtins.get(definition_id) or desired.get(definition_id)
        head = self.journal.agent_definitions.get_head(self.workspace_id, definition_id)
        version = (
            self.journal.agent_definitions.get_version(self.workspace_id, head.version_id)
            if head is not None
            else None
        )
        if source is None and head is None:
            versions = self.journal.agent_definitions.list_versions(self.workspace_id)
            version = next(
                (item for item in versions if item.source.definition_id == definition_id), None
            )
            if version is None:
                return None
        origin = "builtin" if definition_id in self.agent_builtins else "user"
        return AgentDefinitionView(
            definition_id=definition_id,
            source=source,
            source_revision=0 if origin == "builtin" else source_revision,
            origin=origin,
            head=head,
            published_version=version,
            revoked=bool(
                version
                and self.journal.agent_definitions.get_revocation(
                    self.workspace_id, version.version_id
                )
            ),
            desired_ahead_of_published=bool(
                source is not None and (head is None or head.source_hash != source.content_hash)
            ),
        )

    def list_workflow_definitions(
        self, *, limit: int = 100, after: str | None = None
    ) -> tuple[WorkflowDefinitionView, ...]:
        desired, source_revision = self._desired_workflows()
        revisions = self.journal.workflows.list_revisions(self.workspace_id)
        ids = (
            set(desired)
            | set(self.workflow_builtins)
            | {item.workflow_definition_id for item in revisions}
        )
        views = []
        for identity in sorted(ids):
            source = self.workflow_builtins.get(identity) or desired.get(identity)
            origin = "builtin" if identity in self.workflow_builtins else "user"
            head = self.journal.workflows.get_head(self.workspace_id, identity)
            revision = (
                self.journal.workflows.get_revision(self.workspace_id, head.workflow_revision_id)
                if head is not None
                else None
            )
            views.append(
                WorkflowDefinitionView(
                    workflow_definition_id=identity,
                    source=source,
                    source_revision=0 if origin == "builtin" else source_revision,
                    origin=origin,
                    head=head,
                    published_revision=revision,
                    revoked=bool(
                        revision
                        and self.journal.workflows.get_revocation(
                            self.workspace_id, revision.workflow_revision_id
                        )
                    ),
                    desired_ahead_of_published=bool(
                        source is not None
                        and (head is None or head.source_hash != source.content_hash)
                    ),
                )
            )
        return self._page(
            tuple(views),
            key=lambda item: item.workflow_definition_id,
            limit=limit,
            after=after,
        )

    def get_workflow_definition(self, definition_id: str) -> WorkflowDefinitionView | None:
        desired, source_revision = self._desired_workflows()
        source = self.workflow_builtins.get(definition_id) or desired.get(definition_id)
        head = self.journal.workflows.get_head(self.workspace_id, definition_id)
        revision = (
            self.journal.workflows.get_revision(self.workspace_id, head.workflow_revision_id)
            if head is not None
            else None
        )
        if source is None and head is None:
            revisions = self.journal.workflows.list_revisions(self.workspace_id)
            revision = next(
                (item for item in revisions if item.workflow_definition_id == definition_id), None
            )
            if revision is None:
                return None
        origin = "builtin" if definition_id in self.workflow_builtins else "user"
        return WorkflowDefinitionView(
            workflow_definition_id=definition_id,
            source=source,
            source_revision=0 if origin == "builtin" else source_revision,
            origin=origin,
            head=head,
            published_revision=revision,
            revoked=bool(
                revision
                and self.journal.workflows.get_revocation(
                    self.workspace_id, revision.workflow_revision_id
                )
            ),
            desired_ahead_of_published=bool(
                source is not None and (head is None or head.source_hash != source.content_hash)
            ),
        )

    def list_workflow_revisions(
        self,
        *,
        workflow_definition_id: str | None = None,
        limit: int = 100,
        after: str | None = None,
    ) -> tuple[WorkflowRevisionView, ...]:
        values = (
            WorkflowRevisionView(
                item,
                bool(
                    self.journal.workflows.get_revocation(
                        self.workspace_id, item.workflow_revision_id
                    )
                ),
            )
            for item in self.journal.workflows.list_revisions(self.workspace_id)
            if workflow_definition_id is None
            or item.workflow_definition_id == workflow_definition_id
        )
        return self._page(
            tuple(values),
            key=lambda item: item.revision.workflow_revision_id,
            limit=limit,
            after=after,
        )

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
            nodes.append(
                WorkflowNodeView(
                    node=node,
                    output_bindings=outputs,
                    artifacts=artifacts,
                    approval_pending=self._approval_pending(node),
                )
            )
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
            lineage_agent_generation_request_count=self.journal.count_lineage_agent_requests(
                self.workspace_id, run.effective_lineage_budget_root_run_id
            ),
            inherited_artifacts=self.journal.workflows.list_artifact_imports(
                self.workspace_id, workflow_run_id
            ),
            usage_availability=self._usage_availability(nodes),
            terminal_outcome=self._terminal_outcome(run),
            actionable_status=self._actionable_status(run),
        )

    def get_run_recovery_view(self, workflow_run_id: str) -> WorkflowRunRecoveryView | None:
        run = self.journal.workflows.get_run(self.workspace_id, workflow_run_id)
        if run is None:
            return None
        root = self.journal.get_task_run(self.workspace_id, run.root_task_run_id)
        if root is None:
            raise ValueError("Workflow root TaskRun is missing")
        return WorkflowRunRecoveryView(workflow_run_id=workflow_run_id, session_id=root.session_id)

    def get_node_view(self, node_run_id: str) -> WorkflowNodeView | None:
        node = self.journal.workflows.get_node(self.workspace_id, node_run_id)
        if node is None:
            return None
        bindings = self.journal.workflows.list_bindings(self.workspace_id, node.workflow_run_id)
        outputs = tuple(
            binding
            for bound_node_id, direction, binding in bindings
            if bound_node_id == node_run_id and direction == "output"
        )
        artifacts = tuple(
            metadata
            for metadata in (
                self.journal.get_artifact(self.workspace_id, item.artifact_id) for item in outputs
            )
            if metadata is not None
        )
        return WorkflowNodeView(
            node=node,
            output_bindings=outputs,
            artifacts=artifacts,
            approval_pending=self._approval_pending(node),
        )

    def _approval_pending(self, node: NodeRun) -> bool:
        """True while a running node waits on an unconsumed Approval.

        During a drain this is the projection that explains why the Workflow
        stays draining: the node is neither blocked nor finished.
        """

        if node.status is not WorkflowStatus.RUNNING or node.agent_run_id is None:
            return False
        return any(
            execution.state is ToolExecutionState.AWAITING_APPROVAL
            for execution in self.journal.list_executions(
                self.workspace_id, agent_run_id=node.agent_run_id
            )
        )

    @staticmethod
    def _actionable_status(run: WorkflowRun) -> str | None:
        if run.status is WorkflowStatus.BLOCKED:
            return "resolve unknown Tool evidence, then resume or abandon this blocked Workflow"
        if run.status is WorkflowStatus.DRAINING:
            return "draining: in-flight nodes settle first, then the Workflow pauses"
        if run.status is WorkflowStatus.PAUSED:
            return "paused: resume with `morrow workflow resume` to continue"
        return None

    def _desired_agents(self):
        if self.agent_sources is None:
            return {}, None
        document = self.agent_sources.load(self.workspace_id)
        return {item.definition_id: item for item in document.definitions}, document.revision

    def _desired_workflows(self):
        if self.workflow_sources is None:
            return {}, None
        document = self.workflow_sources.load(self.workspace_id)
        return (
            {item.workflow_definition_id: item for item in document.definitions},
            document.revision,
        )

    def _usage_availability(self, nodes) -> str:
        admitted = [item for item in nodes if item.node.agent_run_id is not None]
        if not admitted:
            return "unavailable"
        for item in admitted:
            metrics = self.journal.get_agent_run_terminal_metrics(
                self.workspace_id, item.node.agent_run_id
            )
            if metrics is None or metrics.usage.availability.value != "available":
                return "unavailable"
        return "available"

    def _terminal_outcome(self, run):
        if not run.status.terminal:
            return None
        outcomes = self.journal.list_task_outcomes(self.workspace_id, run.root_task_run_id)
        return outcomes[-1] if outcomes else None

    @staticmethod
    def _page(values, *, key, limit, after):
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("query limit must be between 1 and 100")
        ordered = tuple(sorted(values, key=key))
        if after is not None:
            ordered = tuple(item for item in ordered if key(item) > after)
        return ordered[:limit]
