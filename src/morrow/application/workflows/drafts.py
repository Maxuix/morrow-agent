"""Durable pre-freeze Workflow Draft lifecycle over the existing compiler."""

from __future__ import annotations

from dataclasses import dataclass

from morrow.application.workflows.compiler import (
    CompileDiagnostic,
    DiagnosticSeverity,
    WorkflowCompilationError,
)
from morrow.core.execution import StaleRowVersionError
from morrow.core.workflows.drafts import (
    WorkflowDraft,
    WorkflowDraftDiagnostic,
    WorkflowDraftStatus,
    WorkflowDraftView,
)


@dataclass(frozen=True)
class WorkflowDraftFreeze:
    draft: WorkflowDraft
    revision: object


class WorkflowDraftService:
    """One small OCC service; validation and publication retain their existing owners."""

    def __init__(
        self,
        journal,
        *,
        workspace_id: str,
        management,
        id_source,
        model_available=None,
        skill_available=None,
    ) -> None:
        self.journal = journal
        self.workspace_id = workspace_id
        self.management = management
        self.id_source = id_source
        self.model_available = model_available or (
            lambda model: model in management.agent_publication.catalog.models
        )
        self.skill_available = skill_available or (
            lambda version_id: version_id in management.agent_publication.catalog.skill_version_ids
        )

    def create(
        self,
        source,
        *,
        expected_source_revision: int,
        draft_id: str | None = None,
        planner=None,
    ) -> WorkflowDraftView:
        document = self.management.workflow_sources.load(self.workspace_id)
        if document.revision != expected_source_revision:
            raise StaleRowVersionError("stale Workflow source document revision")
        draft_id = draft_id or self.id_source.new_id("wdraft")
        existing = self.journal.workflows.get_draft(self.workspace_id, draft_id)
        if existing is not None:
            if (
                existing.source == source
                and existing.base_source_revision == expected_source_revision
            ):
                return self._view(existing)
            raise ValueError("Workflow Draft identifier conflict")
        head = self.journal.workflows.get_head(self.workspace_id, source.workflow_definition_id)
        existing_source = next(
            (
                item
                for item in document.definitions
                if item.workflow_definition_id == source.workflow_definition_id
            ),
            None,
        )
        result, diagnostics = self._validate(source)
        stamp = self.journal.now()
        draft = WorkflowDraft(
            draft_id=draft_id,
            workspace_id=self.workspace_id,
            source=source,
            source_hash=source.content_hash,
            base_workflow_revision_id=head.workflow_revision_id if head else None,
            base_head_row_version=head.row_version if head else 0,
            base_source_revision=document.revision,
            base_definition_source_hash=(
                existing_source.content_hash if existing_source is not None else None
            ),
            status=(
                WorkflowDraftStatus.VALID
                if result.candidate is not None and not self._has_errors(diagnostics)
                else WorkflowDraftStatus.INVALID
            ),
            diagnostics=diagnostics,
            row_version=1,
            created_at=stamp,
            updated_at=stamp,
            planner=planner,
        )
        return self._view(self.journal.workflows.create_draft(draft))

    def get(self, draft_id: str) -> WorkflowDraftView | None:
        draft = self.journal.workflows.get_draft(self.workspace_id, draft_id)
        return self._view(draft) if draft is not None else None

    def list(self, *, limit: int = 100) -> tuple[WorkflowDraftView, ...]:
        if type(limit) is not int or limit < 1 or limit > 100:
            raise ValueError("Workflow Draft limit must be between 1 and 100")
        return tuple(
            self._view(value)
            for value in self.journal.workflows.list_drafts(self.workspace_id)[:limit]
        )

    def update(self, draft_id: str, source, *, expected_row_version: int) -> WorkflowDraftView:
        current = self.journal.workflows.get_draft(self.workspace_id, draft_id)
        if (
            current is not None
            and current.row_version == expected_row_version + 1
            and current.source == source
        ):
            return self._view(current)
        current = self._require_mutable(draft_id, expected_row_version)
        if source.workflow_definition_id != current.source.workflow_definition_id:
            raise ValueError("Workflow Draft definition identity cannot be changed")
        result, diagnostics = self._validate(source)
        updated = current.model_copy(
            update={
                "source": source,
                "source_hash": source.content_hash,
                "status": (
                    WorkflowDraftStatus.VALID
                    if result.candidate is not None and not self._has_errors(diagnostics)
                    else WorkflowDraftStatus.INVALID
                ),
                "diagnostics": diagnostics,
                "row_version": current.row_version + 1,
                "updated_at": self.journal.now(),
            }
        )
        return self._view(
            self.journal.workflows.save_draft(updated, expected_row_version=expected_row_version)
        )

    def revalidate(self, draft_id: str, *, expected_row_version: int) -> WorkflowDraftView:
        current = self.journal.workflows.get_draft(self.workspace_id, draft_id)
        if current is None:
            raise ValueError("Workflow Draft is missing")
        return self.update(
            draft_id,
            current.source,
            expected_row_version=expected_row_version,
        )

    def reject(self, draft_id: str, *, expected_row_version: int) -> WorkflowDraftView:
        current = self.journal.workflows.get_draft(self.workspace_id, draft_id)
        if (
            current is not None
            and current.row_version == expected_row_version + 1
            and current.status is WorkflowDraftStatus.REJECTED
        ):
            return self._view(current)
        current = self._require_mutable(draft_id, expected_row_version)
        rejected = current.model_copy(
            update={
                "status": WorkflowDraftStatus.REJECTED,
                "row_version": current.row_version + 1,
                "updated_at": self.journal.now(),
            }
        )
        return self._view(
            self.journal.workflows.save_draft(rejected, expected_row_version=expected_row_version)
        )

    def freeze(
        self,
        draft_id: str,
        *,
        expected_row_version: int,
        command_id: str,
    ) -> WorkflowDraftFreeze:
        current = self.journal.workflows.get_draft(self.workspace_id, draft_id)
        if current is None:
            raise ValueError("Workflow Draft is missing")
        if current.status is WorkflowDraftStatus.FROZEN:
            revision = self.journal.workflows.get_revision(
                self.workspace_id, current.frozen_workflow_revision_id
            )
            if revision is None:
                raise ValueError("frozen Workflow Draft Revision is missing")
            return WorkflowDraftFreeze(current, revision)
        current = self._require_mutable(draft_id, expected_row_version)
        publication_receipt = self.journal.workflows.publication(self.workspace_id, command_id)
        if publication_receipt is not None:
            revision = self.journal.workflows.get_revision(
                self.workspace_id, publication_receipt[1]
            )
            if revision is None or (
                revision.workflow_definition_id,
                revision.source_hash,
            ) != (
                current.source.workflow_definition_id,
                current.source_hash,
            ):
                raise ValueError("Workflow Draft publication receipt mismatch")
            frozen = current.model_copy(
                update={
                    "status": WorkflowDraftStatus.FROZEN,
                    "frozen_workflow_revision_id": revision.workflow_revision_id,
                    "row_version": current.row_version + 1,
                    "updated_at": self.journal.now(),
                }
            )
            stored = self.journal.workflows.save_draft(
                frozen, expected_row_version=expected_row_version
            )
            return WorkflowDraftFreeze(stored, revision)

        result, diagnostics = self._validate(current.source)
        if result.candidate is None or self._has_errors(diagnostics):
            errors = tuple(
                CompileDiagnostic(
                    DiagnosticSeverity.ERROR,
                    item.code,
                    item.message,
                    node_id=item.node_id,
                    edge_id=item.edge_id,
                )
                for item in diagnostics
                if item.severity == "error"
            )
            raise WorkflowCompilationError(errors)

        head = self.journal.workflows.get_head(
            self.workspace_id, current.source.workflow_definition_id
        )
        current_head_version = head.row_version if head else 0
        if current_head_version != current.base_head_row_version and publication_receipt is None:
            raise StaleRowVersionError("stale Workflow head row version")

        document = self.management.workflow_sources.load(self.workspace_id)
        desired = {item.workflow_definition_id: item for item in document.definitions}
        existing = desired.get(current.source.workflow_definition_id)
        existing_hash = existing.content_hash if existing is not None else None
        if existing != current.source:
            if existing_hash != current.base_definition_source_hash:
                raise StaleRowVersionError("stale Workflow definition source")
            if existing is None:
                written = self.management.create_workflow_source(
                    current.source,
                    expected_source_revision=document.revision,
                )
            else:
                written = self.management.update_workflow_source(
                    current.source,
                    expected_source_revision=document.revision,
                )
            document = document.model_copy(update={"revision": written.source_revision})

        publication = self.management.workflow_publication.publish(
            current.source,
            source_revision=document.revision,
            expected_head_revision=current.base_head_row_version,
            command_id=command_id,
            active_model=self.management.active_model,
        )
        frozen = current.model_copy(
            update={
                "status": WorkflowDraftStatus.FROZEN,
                "diagnostics": diagnostics,
                "frozen_workflow_revision_id": publication.revision.workflow_revision_id,
                "row_version": current.row_version + 1,
                "updated_at": self.journal.now(),
            }
        )
        stored = self.journal.workflows.save_draft(
            frozen, expected_row_version=expected_row_version
        )
        return WorkflowDraftFreeze(stored, publication.revision)

    def _require_mutable(self, draft_id: str, expected_row_version: int) -> WorkflowDraft:
        current = self.journal.workflows.get_draft(self.workspace_id, draft_id)
        if current is None:
            raise ValueError("Workflow Draft is missing")
        if current.row_version != expected_row_version:
            raise StaleRowVersionError("stale Workflow Draft row version")
        if current.status in {WorkflowDraftStatus.REJECTED, WorkflowDraftStatus.FROZEN}:
            raise ValueError("terminal Workflow Draft cannot be edited")
        return current

    def validate(self, source):
        """Read-only Compiler and live admission diagnostics, shared with GraphPlanner."""
        return self._validate(source)

    def _validate(self, source):
        result = self.management.workflow_publication.validate(
            source, active_model=self.management.active_model
        )
        diagnostics = [self._diagnostic(item) for item in result.diagnostics]
        for node in source.nodes:
            ref = node.agent_definition_ref
            version = self.journal.agent_definitions.get_version(self.workspace_id, ref.version_id)
            if version is None:
                continue
            if (
                self.journal.agent_definitions.get_revocation(self.workspace_id, ref.version_id)
                is not None
            ):
                diagnostics.append(
                    WorkflowDraftDiagnostic(
                        severity="error",
                        code="agent_version_revoked",
                        message=f"node {node.node_id}: referenced Agent version is revoked",
                        node_id=node.node_id,
                    )
                )
            head = self.journal.agent_definitions.get_head(self.workspace_id, ref.definition_id)
            if head is None or not head.enabled:
                diagnostics.append(
                    WorkflowDraftDiagnostic(
                        severity="error",
                        code="agent_definition_disabled",
                        message=f"node {node.node_id}: referenced Agent definition is disabled",
                        node_id=node.node_id,
                    )
                )
            model = (
                version.source.model_selection
                if version.source.model_selection != "invoking_active"
                else self.management.active_model
            )
            if model is not None and not self.model_available(model):
                diagnostics.append(
                    WorkflowDraftDiagnostic(
                        severity="error",
                        code="provider_model_disabled",
                        message=f"node {node.node_id}: selected Provider/Model is unavailable",
                        node_id=node.node_id,
                    )
                )
            for skill_version_id in version.source.skill_version_ids:
                if not self.skill_available(skill_version_id):
                    diagnostics.append(
                        WorkflowDraftDiagnostic(
                            severity="error",
                            code="skill_disabled",
                            message=(
                                f"node {node.node_id}: Skill version {skill_version_id}"
                                " is disabled or unavailable"
                            ),
                            node_id=node.node_id,
                        )
                    )
        return result, tuple(diagnostics)

    @staticmethod
    def _diagnostic(item) -> WorkflowDraftDiagnostic:
        return WorkflowDraftDiagnostic(
            severity=item.severity.value,
            code=item.code,
            message=item.message,
            node_id=item.node_id,
            edge_id=item.edge_id,
        )

    @staticmethod
    def _has_errors(diagnostics) -> bool:
        return any(item.severity == "error" for item in diagnostics)

    def _view(self, draft: WorkflowDraft) -> WorkflowDraftView:
        stale = []
        head = self.journal.workflows.get_head(
            self.workspace_id, draft.source.workflow_definition_id
        )
        if (head.row_version if head else 0) != draft.base_head_row_version:
            stale.append("workflow_head_changed")
        document = self.management.workflow_sources.load(self.workspace_id)
        desired = {item.workflow_definition_id: item for item in document.definitions}
        existing = desired.get(draft.source.workflow_definition_id)
        existing_hash = existing.content_hash if existing is not None else None
        if existing_hash != draft.base_definition_source_hash and existing != draft.source:
            stale.append("workflow_source_changed")
        for node in draft.source.nodes:
            head = self.journal.agent_definitions.get_head(
                self.workspace_id, node.agent_definition_ref.definition_id
            )
            if head is not None and head.version_id != node.agent_definition_ref.version_id:
                stale.append(f"agent_head_changed:{node.node_id}")
        return WorkflowDraftView(draft=draft, stale_reasons=tuple(sorted(set(stale))))
