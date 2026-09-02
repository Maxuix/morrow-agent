"""Stage 7 definition management commands over existing persistence owners.

This module is intentionally a thin application boundary. Desired source writes
remain in the bounded YAML adapters, immutable publication remains in the two
publication services, and execution/recovery remain in the unified Workflow
runtime. No command writes Operational Store or YAML records directly.
"""

from __future__ import annotations

from dataclasses import dataclass

from morrow.adapters.state.definition_yaml import (
    AgentDefinitionYamlStore,
    WorkflowDefinitionYamlStore,
)
from morrow.core.agent_definitions import (
    AgentDefinitionDocument,
    AgentDefinitionSource,
)
from morrow.core.application import ApplicationError, ApplicationErrorCode
from morrow.core.models import ModelRef
from morrow.core.workflows.definitions import (
    WorkflowDefinitionDocument,
    WorkflowDefinitionSource,
)
from morrow.core.workflows.runs import WorkflowRun


@dataclass(frozen=True)
class DefinitionSourceResult:
    """A desired-source mutation with its new document revision."""

    source: AgentDefinitionSource | WorkflowDefinitionSource
    source_revision: int


@dataclass(frozen=True)
class ForegroundWorkflowResult:
    """The exact published Revision selected for one foreground execution."""

    workflow_revision_id: str
    published: bool
    run: WorkflowRun


class WorkflowManagementService:
    """One application command surface for Agent and Workflow definitions."""

    def __init__(
        self,
        *,
        workspace_id: str,
        agent_sources: AgentDefinitionYamlStore,
        workflow_sources: WorkflowDefinitionYamlStore,
        agent_publication,
        workflow_publication,
        runtime,
        active_model: ModelRef | None,
        agent_builtins=(),
        workflow_builtins=(),
    ) -> None:
        self.workspace_id = workspace_id
        self.agent_sources = agent_sources
        self.workflow_sources = workflow_sources
        self.agent_publication = agent_publication
        self.workflow_publication = workflow_publication
        self.runtime = runtime
        self.active_model = active_model
        self.agent_builtins = {item.definition_id: item for item in agent_builtins}
        self.workflow_builtins = {item.workflow_definition_id: item for item in workflow_builtins}

    # Desired source ---------------------------------------------------------

    def create_agent_source(
        self, source: AgentDefinitionSource, *, expected_source_revision: int
    ) -> DefinitionSourceResult:
        return self._write_agent(source, expected_source_revision, create=True)

    def update_agent_source(
        self, source: AgentDefinitionSource, *, expected_source_revision: int
    ) -> DefinitionSourceResult:
        return self._write_agent(source, expected_source_revision, create=False)

    def create_workflow_source(
        self, source: WorkflowDefinitionSource, *, expected_source_revision: int
    ) -> DefinitionSourceResult:
        return self._write_workflow(source, expected_source_revision, create=True)

    def update_workflow_source(
        self, source: WorkflowDefinitionSource, *, expected_source_revision: int
    ) -> DefinitionSourceResult:
        return self._write_workflow(source, expected_source_revision, create=False)

    def _write_agent(self, source, expected, *, create):
        if source.definition_id.startswith("builtin_"):
            raise ValueError(
                "built-in Agent sources are read-only; create a new user definition ID"
            )
        current = self.agent_sources.load(self.workspace_id)
        values = {item.definition_id: item for item in current.definitions}
        self._require_create_state(source.definition_id, values, create=create, kind="Agent")
        values[source.definition_id] = source
        written = self.agent_sources.write(
            self.workspace_id,
            AgentDefinitionDocument(definitions=tuple(values.values())),
            expected_revision=expected,
        )
        return DefinitionSourceResult(source, written.revision)

    def _write_workflow(self, source, expected, *, create):
        if source.origin != "user" or source.workflow_definition_id.startswith("builtin_"):
            raise ValueError(
                "built-in Workflow sources are read-only; create a new user definition ID"
            )
        current = self.workflow_sources.load(self.workspace_id)
        values = {item.workflow_definition_id: item for item in current.definitions}
        self._require_create_state(
            source.workflow_definition_id, values, create=create, kind="Workflow"
        )
        values[source.workflow_definition_id] = source
        written = self.workflow_sources.write(
            self.workspace_id,
            WorkflowDefinitionDocument(definitions=tuple(values.values())),
            expected_revision=expected,
        )
        return DefinitionSourceResult(source, written.revision)

    @staticmethod
    def _require_create_state(identity, values, *, create, kind):
        exists = identity in values
        if create and exists:
            raise ValueError(f"{kind} definition already exists; use update")
        if not create and not exists:
            raise ValueError(f"{kind} definition is missing; use create")

    # Pure validation and explicit publication ------------------------------

    def validate_agent(self, definition_id: str):
        source, _revision, _origin = self._agent_source(definition_id)
        return self.agent_publication.validate(source)

    def publish_agent(
        self,
        definition_id: str,
        *,
        expected_head_revision: int,
        command_id: str,
        enabled: bool = True,
    ):
        source, source_revision, origin = self._agent_source(definition_id)
        return self.agent_publication.publish(
            source,
            source_revision=source_revision,
            expected_head_revision=expected_head_revision,
            command_id=command_id,
            enabled=enabled,
            origin=origin,
        )

    def validate_workflow(self, definition_id: str):
        source, _revision = self._workflow_source(definition_id)
        return self.workflow_publication.validate(source, active_model=self.active_model)

    def publish_workflow(
        self,
        definition_id: str,
        *,
        expected_head_revision: int,
        command_id: str,
        enabled: bool = True,
    ):
        source, source_revision = self._workflow_source(definition_id)
        return self.workflow_publication.publish(
            source,
            source_revision=source_revision,
            expected_head_revision=expected_head_revision,
            command_id=command_id,
            active_model=self.active_model,
            enabled=enabled,
        )

    def _agent_source(self, definition_id):
        builtin = self.agent_builtins.get(definition_id)
        if builtin is not None:
            return builtin, 0, "builtin"
        loaded = self.agent_sources.load_definition(self.workspace_id, definition_id)
        return loaded.source, loaded.source_revision, "user"

    def _workflow_source(self, definition_id):
        builtin = self.workflow_builtins.get(definition_id)
        if builtin is not None:
            return builtin, 0
        loaded = self.workflow_sources.load_definition(self.workspace_id, definition_id)
        return loaded.source, loaded.source_revision

    # Operational admission controls ---------------------------------------

    def set_agent_enabled(self, definition_id: str, *, enabled: bool, expected_head_revision: int):
        return self.agent_publication.set_enabled(
            definition_id,
            enabled=enabled,
            expected_head_revision=expected_head_revision,
        )

    def revoke_agent_version(self, version_id: str, *, reason: str, command_id: str):
        if not version_id.startswith("adev_"):
            raise ValueError("revoke requires an exact AgentDefinitionVersion ID")
        return self.agent_publication.revoke(version_id, reason=reason, command_id=command_id)

    def set_workflow_enabled(
        self, definition_id: str, *, enabled: bool, expected_head_revision: int
    ):
        return self.workflow_publication.set_enabled(
            definition_id,
            enabled=enabled,
            expected_head_revision=expected_head_revision,
        )

    def revoke_workflow_revision(self, revision_id: str, *, reason: str, command_id: str):
        if not revision_id.startswith("wrev_"):
            raise ValueError("revoke requires an exact WorkflowRevision ID")
        return self.workflow_publication.revoke(revision_id, reason=reason, command_id=command_id)

    def requires_client_message(
        self, definition_id: str, *, revision_id: str | None = None
    ) -> bool:
        """Read-only presentation fact from an exact Revision or desired source."""

        revision = (
            self.workflow_publication.journal.workflows.get_revision(self.workspace_id, revision_id)
            if revision_id is not None
            else None
        )
        if revision is not None:
            return revision.nodes[0].conversation_scope == "invoking_session"
        source, _source_revision = self._workflow_source(definition_id)
        return source.nodes[0].conversation_scope == "invoking_session"

    # Foreground execution and recovery -------------------------------------

    async def run_foreground(
        self,
        command,
        *,
        ensure_published: bool = False,
        expected_head_revision: int | None = None,
        publish_command_id: str | None = None,
    ) -> ForegroundWorkflowResult:
        if self.runtime is None:
            raise RuntimeError("foreground execution requires a composed Workflow runtime")
        published = False
        revision_id = command.workflow_revision_id
        if ensure_published:
            if expected_head_revision is None or publish_command_id is None:
                raise ValueError(
                    "ensure_published requires head revision and a distinct publish command ID"
                )
            publication = self.publish_workflow(
                command.workflow_definition_id,
                expected_head_revision=expected_head_revision,
                command_id=publish_command_id,
            )
            revision_id = publication.revision.workflow_revision_id
            published = True
            command = type(command)(
                **{
                    **command.__dict__,
                    "workflow_revision_id": revision_id,
                }
            )
        elif (
            self.runtime.start.journal.workflows.get_revision(self.workspace_id, revision_id)
            is None
        ):
            raise ApplicationError(
                ApplicationErrorCode.INVALID,
                "Workflow revision is not published; run workflow publish first or opt in to "
                "--ensure-published",
            )
        started = self.runtime.start.start(command)
        run = await self.runtime.scheduler.run(started.run.workflow_run_id)
        return ForegroundWorkflowResult(revision_id, published, run)

    async def resume(self, workflow_run_id: str) -> WorkflowRun:
        if self.runtime is None:
            raise RuntimeError("Workflow recovery requires a composed Workflow runtime")
        return await self.runtime.scheduler.recover(workflow_run_id)

    def abandon(self, workflow_run_id: str, *, expected_row_version: int) -> WorkflowRun:
        if self.runtime is None:
            raise RuntimeError("Workflow recovery requires a composed Workflow runtime")
        return self.runtime.scheduler.abandon(
            workflow_run_id, expected_row_version=expected_row_version
        )
