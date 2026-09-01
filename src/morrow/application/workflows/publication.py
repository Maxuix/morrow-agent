"""The sole transactional Workflow publication path over the pure compiler.

Validation is a write-free projection of the same pure compile; only publish
stores an immutable WorkflowRevision and advances its SQLite head under OCC and
command-receipt idempotency. Enable/disable and one-way revision revocation are
operational head/audit writes here, never a side effect of compilation.
"""

from __future__ import annotations

from morrow.application.agent_definitions.publication import DefinitionCatalog
from morrow.application.workflows.compiler import (
    CompilationResult,
    WorkflowCompilationError,
    compile_workflow,
)
from morrow.core.domain import canonical_json_bytes, sha256_digest, validate_prefixed_id
from morrow.core.models import ModelRef
from morrow.core.workflows.definitions import (
    WorkflowDefinitionHead,
    WorkflowDefinitionSource,
    WorkflowRevision,
    WorkflowRevisionRevocation,
)


class WorkflowCompilationService:
    """Single writer for WorkflowRevision rows, published heads and revocations."""

    def __init__(self, journal, *, workspace_id, catalog: DefinitionCatalog, id_source):
        self.journal = journal
        self.workspace_id = validate_prefixed_id(workspace_id, "ws")
        self.catalog = catalog
        self.id_source = id_source

    def _resolve_versions(self, source: WorkflowDefinitionSource):
        return {
            node.agent_definition_ref.version_id: self.journal.agent_definitions.get_version(
                self.workspace_id, node.agent_definition_ref.version_id
            )
            for node in source.nodes
        }

    def validate(
        self, source: WorkflowDefinitionSource, *, active_model: ModelRef | None
    ) -> CompilationResult:
        """Read-only projection: candidate diagnostics, zero writes, zero identity allocation."""
        return compile_workflow(
            source,
            agent_versions=self._resolve_versions(source),
            catalog=self.catalog,
            active_model=active_model,
        )

    def publish(
        self,
        source: WorkflowDefinitionSource,
        *,
        source_revision,
        expected_head_revision,
        command_id,
        active_model: ModelRef | None,
        enabled=True,
    ) -> WorkflowRevision:
        if (
            type(source_revision) is not int
            or source_revision < 0
            or type(expected_head_revision) is not int
            or expected_head_revision < 0
            or type(enabled) is not bool
        ):
            raise ValueError("invalid Workflow publication metadata")
        validate_prefixed_id(command_id, "cmd")
        request_hash = sha256_digest(
            canonical_json_bytes(
                {
                    "source": source.content_hash,
                    "source_revision": source_revision,
                    "expected_head_revision": expected_head_revision,
                    "enabled": enabled,
                }
            )
        )

        def work(txn):
            repo = txn.workflows
            replay = repo.publication(self.workspace_id, command_id)
            if replay is not None:
                if replay[0] != request_hash:
                    raise ValueError("publication command conflicts with prior receipt")
                revision = repo.get_revision(self.workspace_id, replay[1])
                if revision is None:
                    raise ValueError("published Workflow revision is missing")
                self._require_unrevoked(txn, revision)
                return revision
            result = compile_workflow(
                source,
                agent_versions=self._resolve_versions(source),
                catalog=self.catalog,
                active_model=active_model,
            )
            if result.candidate is None:
                raise WorkflowCompilationError(result.diagnostics)
            candidate = result.candidate
            for version_id in {n.agent_definition_ref.version_id for n in candidate.nodes}:
                if txn.agent_definitions.get_revocation(self.workspace_id, version_id) is not None:
                    raise ValueError(
                        "referenced AgentDefinitionVersion is revoked; publish a changed"
                        " definition and reference its new exact version"
                    )
            head = repo.get_head(self.workspace_id, source.workflow_definition_id)
            if (head.row_version if head else 0) != expected_head_revision:
                raise ValueError("Workflow head revision conflict")
            old = repo.get_revision(self.workspace_id, head.workflow_revision_id) if head else None
            if old is not None and old.content_hash == result.content_hash:
                self._require_unrevoked(txn, old)
                revision = old
            else:
                revision = WorkflowRevision(
                    **candidate.model_dump(),
                    workflow_revision_id=self.id_source.new_id("wrev"),
                    workspace_id=self.workspace_id,
                    revision=old.revision + 1 if old else 1,
                    parent_workflow_revision_id=(
                        old.workflow_revision_id if old is not None else None
                    ),
                    content_hash=result.content_hash,
                    source_revision=source_revision,
                    source_hash=source.content_hash,
                    created_by=command_id,
                    created_at=txn.now(),
                )
                repo.store_compiled_revision(
                    revision,
                    WorkflowDefinitionHead(
                        workspace_id=self.workspace_id,
                        workflow_definition_id=source.workflow_definition_id,
                        workflow_revision_id=revision.workflow_revision_id,
                        source_revision=source_revision,
                        source_hash=source.content_hash,
                        enabled=head.enabled if head else enabled,
                        row_version=expected_head_revision + 1,
                    ),
                    expected_row_version=expected_head_revision,
                )
            repo.put_publication(
                self.workspace_id, command_id, request_hash, revision.workflow_revision_id
            )
            return revision

        return self.journal.transact(work)

    def _require_unrevoked(self, txn, revision: WorkflowRevision):
        if txn.workflows.get_revocation(self.workspace_id, revision.workflow_revision_id):
            raise ValueError(
                "policy_revoked: a revoked Workflow revision cannot re-enter through"
                " publication; publish a new revision to supersede"
            )

    def set_enabled(self, definition_id, *, enabled: bool, expected_head_revision):
        """Operational head toggle; never compiles and never creates a Revision."""
        return self.journal.workflows.set_enabled(
            self.workspace_id,
            definition_id,
            enabled=enabled,
            expected_row_version=expected_head_revision,
        )

    def revoke(self, revision_id, *, reason, command_id):
        """Additive one-way emergency revocation of one exact immutable Revision."""
        validate_prefixed_id(command_id, "cmd")

        def work(txn):
            repo = txn.workflows
            if repo.get_revision(self.workspace_id, revision_id) is None:
                raise ValueError("published Workflow revision is missing")
            existing = repo.get_revocation(self.workspace_id, revision_id)
            if existing is not None:
                if existing.command_id == command_id and existing.reason == reason:
                    return existing
                raise ValueError("revocation is one-way and cannot be replaced")
            return repo.put_revocation(
                WorkflowRevisionRevocation(
                    workspace_id=self.workspace_id,
                    workflow_revision_id=revision_id,
                    reason=reason,
                    command_id=command_id,
                    created_at=txn.now(),
                )
            )

        return self.journal.transact(work)
