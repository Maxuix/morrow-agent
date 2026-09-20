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
from morrow.application.agent_definitions.quick_save import AgentQuickSaveResult
from morrow.application.workflows.builtins import resolve_builtin_placeholders
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
from morrow.core.workflows.runs import WorkflowRun, WorkflowStatus


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

    def clone_workflow_source(
        self,
        definition_id: str,
        *,
        new_definition_id: str,
        expected_source_revision: int,
        name: str | None = None,
    ) -> DefinitionSourceResult:
        """Copy any visible definition into editable user-owned desired state."""

        source, _source_revision = self._workflow_source(definition_id)
        payload = source.model_dump(mode="python")
        payload.update(
            {
                "workflow_definition_id": new_definition_id,
                "name": name if name is not None else source.name,
                "origin": "user",
            }
        )
        clone = WorkflowDefinitionSource.model_validate(payload)
        return self.create_workflow_source(clone, expected_source_revision=expected_source_revision)

    def _write_agent(self, source, expected, *, create):
        if source.definition_id.startswith("builtin_"):
            raise ValueError(
                "built-in Agent sources are read-only; create a new user definition ID"
            )
        current = self.agent_sources.load(self.workspace_id)
        values = {item.definition_id: item for item in current.definitions}
        if source.derived_from_definition_id is not None:
            parent = self.agent_builtins.get(source.derived_from_definition_id)
            if parent is None:
                parent = next(
                    (
                        version.source
                        for version in self.agent_publication.journal.agent_definitions.list_versions(
                            self.workspace_id
                        )
                        if version.source.definition_id == source.derived_from_definition_id
                        and version.content_hash == source.derived_from_source_hash
                    ),
                    None,
                )
            if parent is None:
                parent = values.get(source.derived_from_definition_id)
            if parent is None or parent.content_hash != source.derived_from_source_hash:
                raise ValueError("derived Agent source provenance does not match its parent")
        if current.revision == expected + 1 and values.get(source.definition_id) == source:
            return DefinitionSourceResult(source, current.revision)
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
        if current.revision == expected + 1 and values.get(source.workflow_definition_id) == source:
            return DefinitionSourceResult(source, current.revision)
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

    def save_and_publish_agent(
        self,
        source: AgentDefinitionSource,
        *,
        expected_source_revision: int,
        expected_head_revision: int,
        command_id: str,
    ):
        """Save-and-available: validate, write YAML, publish, enable and prove.

        Each step is idempotent and OCC-guarded; a crash between media is
        healed by re-running the same command (the command receipt rebuilds
        through :meth:`restore_quick_save_agent`). The returned version passed
        the admission check, so callers can present it as actually usable.
        """
        if source.definition_id.startswith("builtin_"):
            raise ValueError("built-in Agent sources are read-only; save under a user name")
        self.agent_publication.validate(source)
        existing = self._desired_agent_source(source.definition_id)
        written = self._write_agent(source, expected_source_revision, create=existing is None)
        version = self._publish_or_reuse(
            source,
            source_revision=written.source_revision,
            expected_head_revision=expected_head_revision,
            command_id=command_id,
        )
        head = self._ensure_agent_enabled(source.definition_id)
        self.agent_publication.admit(version.version_id)
        return AgentQuickSaveResult(
            definition_id=source.definition_id,
            source=source,
            version=version,
            source_revision=written.source_revision,
            enabled=head.enabled,
            available_version_id=version.version_id,
        )

    def _publish_or_reuse(
        self,
        source: AgentDefinitionSource,
        *,
        source_revision: int,
        expected_head_revision: int,
        command_id: str,
    ):
        """Publish the exact source, or reuse the head version already at it.

        A retried save after a crash between publish and the command receipt
        re-converges on the same version identity without an OCC baseline
        conflict; a head moved by a different command still fails the OCC check
        inside ``publish``.
        """
        head = self.agent_publication.journal.agent_definitions.get_head(
            self.workspace_id, source.definition_id
        )
        if head is not None and head.source_hash == source.content_hash:
            version = self.agent_publication.journal.agent_definitions.get_version(
                self.workspace_id, head.version_id
            )
            if version is not None:
                self.agent_publication.require_unrevoked(version)
                return version
        return self.agent_publication.publish(
            source,
            source_revision=source_revision,
            expected_head_revision=expected_head_revision,
            command_id=command_id,
            enabled=True,
            origin="user",
        )

    def restore_quick_save_agent(self, source: AgentDefinitionSource, *, version_id: str):
        """Receipt-authoritative rebuild used when a save command replays.

        The receipt's result identity is the authority: never re-publish (a
        later save may own the head now) and never re-enable. The desired
        source is re-written when the YAML copy is missing or stale so the
        saved definition stays editable, and the current availability is
        reported honestly.
        """
        if source.definition_id.startswith("builtin_"):
            raise ValueError("built-in Agent sources are read-only; save under a user name")
        version = self.agent_publication.journal.agent_definitions.get_version(
            self.workspace_id, version_id
        )
        if version is None or version.source.definition_id != source.definition_id:
            raise ApplicationError(
                ApplicationErrorCode.NEEDS_RECOVERY,
                "saved Agent version is missing; restore it from backup",
            )
        if version.content_hash != source.content_hash:
            raise ValueError("saved Agent version does not match the replayed command")
        current = self.agent_sources.load(self.workspace_id)
        values = {item.definition_id: item for item in current.definitions}
        if values.get(source.definition_id) != source:
            self.agent_sources.write(
                self.workspace_id,
                AgentDefinitionDocument(
                    definitions=(*current.definitions, source)
                    if source.definition_id not in values
                    else tuple(
                        source if item.definition_id == source.definition_id else item
                        for item in current.definitions
                    ),
                ),
                expected_revision=current.revision,
            )
            source_revision = current.revision + 1
        else:
            source_revision = current.revision
        head = self.agent_publication.journal.agent_definitions.get_head(
            self.workspace_id, source.definition_id
        )
        revoked = (
            self.agent_publication.journal.agent_definitions.get_revocation(
                self.workspace_id, version_id
            )
            is not None
        )
        return AgentQuickSaveResult(
            definition_id=source.definition_id,
            source=source,
            version=version,
            source_revision=source_revision,
            enabled=bool(head.enabled if head is not None else False) and not revoked,
            available_version_id=version_id,
        )

    def _desired_agent_source(self, definition_id: str) -> AgentDefinitionSource | None:
        document = self.agent_sources.load(self.workspace_id)
        return next(
            (item for item in document.definitions if item.definition_id == definition_id), None
        )

    def _ensure_agent_enabled(self, definition_id: str):
        head = self.agent_publication.journal.agent_definitions.get_head(
            self.workspace_id, definition_id
        )
        if head is None:
            raise ValueError("Agent definition head is missing after publication")
        if not head.enabled:
            head = self.set_agent_enabled(
                definition_id, enabled=True, expected_head_revision=head.row_version
            )
        return head

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
            return resolve_builtin_placeholders(
                builtin, self.workflow_publication.journal, self.workspace_id
            ), 0
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

    def start_foreground(self, command):
        """Durably create or replay a run before any foreground model work starts."""

        if self.runtime is None:
            raise RuntimeError("foreground execution requires a composed Workflow runtime")
        revision_id = command.workflow_revision_id
        if (
            self.runtime.start.journal.workflows.get_revision(self.workspace_id, revision_id)
            is None
        ):
            raise ApplicationError(
                ApplicationErrorCode.INVALID,
                "Workflow revision is not published; run workflow publish first or opt in to "
                "--ensure-published",
            )
        return self.runtime.start.start(command)

    async def drive_foreground(self, started) -> ForegroundWorkflowResult:
        """Drive one already-durable start result and preserve its exact revision identity."""

        if self.runtime is None:
            raise RuntimeError("foreground execution requires a composed Workflow runtime")
        run = await self.runtime.scheduler.run(started.run.workflow_run_id)
        while run.status.value == "superseded":
            proposals = self.runtime.patches.journal.workflows.list_replan_proposals(
                self.workspace_id, run.workflow_run_id
            )
            child = next((p.child_run_id for p in proposals if p.auto_applied), None)
            if child is None:
                break
            run = await self.runtime.scheduler.run(child)
        return ForegroundWorkflowResult(started.run.workflow_revision_id, False, run)

    async def run_foreground(
        self,
        command,
    ) -> ForegroundWorkflowResult:
        started = self.start_foreground(command)
        return await self.drive_foreground(started)

    async def resume(
        self,
        workflow_run_id: str,
        *,
        command_id: str | None = None,
        cancelled_is_user: bool = True,
    ) -> WorkflowRun:
        if self.runtime is None:
            raise RuntimeError("Workflow recovery requires a composed Workflow runtime")
        run = self.runtime.transitions.get_run(workflow_run_id)
        if run is not None and run.status is WorkflowStatus.FAILED:
            # Restricted historical recovery first: only a vouched serial
            # Provider failure reopens the run; anything else refuses with
            # concrete reasons. CLI resume and the server resume command share
            # this entry, so every surface applies the same eligibility.
            self.recover_failed_workflow(workflow_run_id, command_id=command_id)
            run = self.runtime.transitions.get_run(workflow_run_id)
        if run is not None and run.pause_requested:
            # Bind the resume command to the suspended pause cycle before the
            # run leaves PAUSED (P04.1): the continuation Turn then admits
            # exactly once with the deterministic continue wording.
            if command_id is not None:
                from morrow.application.execution_pause import ExecutionPauseService

                ExecutionPauseService(
                    self.runtime.transitions.journal,
                    workspace_id=self.runtime.transitions.workspace_id,
                ).store_continuation_input(workflow_run_id, command_id=command_id, text=None)
            # Resume atomically clears the durable pause fact; a blocked run
            # keeps its status and stays recovery-owned either way.
            self.runtime.transitions.resume_run(workflow_run_id)
        return await self.runtime.scheduler.recover(
            workflow_run_id, cancelled_is_user=cancelled_is_user
        )

    def assess_failed_workflow(self, workflow_run_id: str):
        """Read-only historical-recovery eligibility verdict with reasons."""

        return self._historical_recovery_service().assess(workflow_run_id)

    def recover_failed_workflow(
        self, workflow_run_id: str, *, command_id: str | None = None
    ) -> WorkflowRun:
        """Restricted historical recovery: a vouched serial Provider failure
        reopens the same run identities and parks the run at its durable pause
        cycle for the ordinary resume flow."""

        return self._historical_recovery_service().recover(workflow_run_id, command_id=command_id)

    def _historical_recovery_service(self):
        if self.runtime is None:
            raise RuntimeError("Workflow recovery requires a composed Workflow runtime")
        from morrow.application.workflows.historical_recovery import (
            WorkflowHistoricalRecoveryService,
        )

        return WorkflowHistoricalRecoveryService(
            journal=self.runtime.transitions.journal,
            workspace_id=self.runtime.transitions.workspace_id,
            recovery=self.runtime.scheduler.recovery,
            id_source=self.runtime.scheduler.id_source,
            clock=self.runtime.scheduler.clock,
        )

    def pause(self, workflow_run_id: str, *, command_id: str | None = None) -> WorkflowRun:
        if self.runtime is None:
            raise RuntimeError("Workflow control requires a composed Workflow runtime")
        # The command id lets request_pause durably accept the PauseIntentFact
        # cycle in the same transaction; without it the run-pause path could
        # not wake a model wait parked before its first token.
        paused = self.runtime.transitions.request_pause(workflow_run_id, command_id=command_id)
        # Same best-effort in-process wake the planning pause entry performs
        # (lane A seam): without it a model wait parked before its first token
        # never observes the durable pause fact and the run cannot suspend.
        try:
            self.runtime.scheduler.pause_control.wake_run(workflow_run_id)
        except Exception:
            pass
        return paused

    def validate_patch(self, patch):
        if self.runtime is None:
            raise RuntimeError("Workflow patching requires a composed Workflow runtime")
        return self.runtime.patches.validate(patch, active_model=self.active_model)

    def save_patch(self, patch):
        if self.runtime is None:
            raise RuntimeError("Workflow patching requires a composed Workflow runtime")
        return self.runtime.patches.save(patch, active_model=self.active_model)

    def apply_patch(self, patch, *, command_id: str | None = None):
        if self.runtime is None:
            raise RuntimeError("Workflow patching requires a composed Workflow runtime")
        return self.runtime.patches.apply(
            patch, active_model=self.active_model, command_id=command_id
        )

    def rerun(self, workflow_run_id: str, *, full: bool, command_id: str | None = None):
        if self.runtime is None:
            raise RuntimeError("Workflow rerun requires a composed Workflow runtime")
        return self.runtime.patches.rerun(workflow_run_id, full=full, command_id=command_id)

    def abandon(self, workflow_run_id: str, *, expected_row_version: int) -> WorkflowRun:
        if self.runtime is None:
            raise RuntimeError("Workflow recovery requires a composed Workflow runtime")
        return self.runtime.scheduler.abandon(
            workflow_run_id, expected_row_version=expected_row_version
        )
