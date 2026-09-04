"""Future-only graph patching and atomic continuation handoff."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from morrow.application.api_context import request_digest
from morrow.application.workflows.compiler import (
    CompilationResult,
    WorkflowCompilationError,
    compile_workflow,
)
from morrow.application.workflows.outputs import EffectiveOutputResolver
from morrow.application.workflows.patch_preview import (
    PatchDiffPreview,
    PatchRiskPreview,
    classify_patch_risk,
    diff_compiled,
)
from morrow.application.workflows.scheduler import stable_execution_order
from morrow.core.application import (
    ApplicationCommandReceipt,
    ApplicationError,
    ApplicationErrorCode,
)
from morrow.core.domain import canonical_json_bytes, sha256_digest, validate_prefixed_id
from morrow.core.workflows.definitions import CompiledWorkflow, WorkflowRevision
from morrow.core.workflows.patches import FutureGraphPatch
from morrow.core.workflows.runs import (
    NodeRun,
    WorkflowArtifactImport,
    WorkflowExecutionNode,
    WorkflowRun,
    WorkflowStatus,
)

RERUN_OPERATION = "workflow_rerun"
PATCH_APPLY_OPERATION = "patch_apply"


@dataclass(frozen=True)
class PatchValidation:
    compilation: CompilationResult
    past_node_ids: tuple[str, ...]
    execution_node_ids: tuple[str, ...]
    diff: PatchDiffPreview | None = None
    risk: PatchRiskPreview | None = None


@dataclass(frozen=True)
class PatchApplication:
    patch: FutureGraphPatch
    revision: WorkflowRevision
    parent: WorkflowRun
    child: WorkflowRun | None
    validation: PatchValidation


@dataclass(frozen=True)
class RerunApplication:
    parent: WorkflowRun
    child: WorkflowRun
    full: bool
    inherited_node_ids: tuple[str, ...]
    execution_node_ids: tuple[str, ...]


class PatchApplicationService:
    """The sole user/proposal path from an exact patch to a continuation child."""

    def __init__(
        self,
        journal,
        *,
        workspace_id: str,
        catalog,
        id_source,
        finalizer,
        clock,
    ) -> None:
        self.journal = journal
        self.workspace_id = workspace_id
        self.catalog = catalog
        self.id_source = id_source
        self.finalizer = finalizer
        self.clock = clock
        self.outputs = EffectiveOutputResolver(journal, workspace_id=workspace_id)

    def validate(self, patch: FutureGraphPatch, *, active_model=None) -> PatchValidation:
        parent, base = self._require_base(patch)
        result = compile_workflow(
            patch.source,
            agent_versions={
                node.agent_definition_ref.version_id: self.journal.agent_definitions.get_version(
                    self.workspace_id, node.agent_definition_ref.version_id
                )
                for node in patch.source.nodes
            },
            catalog=self.catalog,
            active_model=active_model,
        )
        if result.candidate is None:
            return PatchValidation(result, (), ())
        if result.candidate.workflow_definition_id != base.workflow_definition_id:
            raise ApplicationError(
                ApplicationErrorCode.INVALID,
                "patch definition does not match its exact base Revision",
            )
        past = self._past_node_ids(parent)
        old_nodes = {item.node_id: item for item in base.nodes}
        new_nodes = {item.node_id: item for item in result.candidate.nodes}
        for node_id in past:
            if node_id not in new_nodes or new_nodes[node_id] != old_nodes.get(node_id):
                raise ApplicationError(
                    ApplicationErrorCode.INVALID,
                    f"past_forgery: admitted node {node_id} and its contracts are immutable",
                )
        old_incoming = {
            (edge.from_node_id, edge.to_node_id) for edge in base.edges if edge.to_node_id in past
        }
        new_incoming = {
            (edge.from_node_id, edge.to_node_id)
            for edge in result.candidate.edges
            if edge.to_node_id in past
        }
        if new_incoming != old_incoming:
            raise ApplicationError(
                ApplicationErrorCode.INVALID,
                "past_forgery: Future-to-Past edges and Past provenance are immutable",
            )
        order = stable_execution_order(
            WorkflowRevision(
                **result.candidate.model_dump(),
                workflow_revision_id="wrev_validation",
                workspace_id=self.workspace_id,
                revision=-1,
                parent_workflow_revision_id=base.workflow_revision_id,
                content_hash=result.content_hash,
                source_revision=0,
                source_hash=patch.source.content_hash,
                created_by=patch.requested_by,
                created_at=self.clock(),
            )
        )
        execution = tuple(node_id for node_id in order if node_id not in past)
        candidate = result.candidate
        assert candidate is not None
        return PatchValidation(
            result,
            tuple(sorted(past)),
            execution,
            diff=diff_compiled(base, candidate),
            risk=classify_patch_risk(base, candidate),
        )

    def save(self, patch: FutureGraphPatch, *, active_model=None) -> PatchApplication:
        parent, base = self._require_base(patch)
        validation = self.validate(patch, active_model=active_model)
        if validation.compilation.candidate is None:
            raise WorkflowCompilationError(validation.compilation.diagnostics)
        candidate = validation.compilation.candidate
        revision_id = self._revision_id(patch)
        existing = self.journal.workflows.get_revision(self.workspace_id, revision_id)
        if existing is not None:
            if (
                existing.parent_workflow_revision_id != base.workflow_revision_id
                or existing.content_hash != validation.compilation.content_hash
                or existing.source_hash != patch.source.content_hash
                or existing.created_by != patch.requested_by
            ):
                raise ApplicationError(
                    ApplicationErrorCode.CONFLICT,
                    "patch identifier conflicts with a different detached Revision",
                )
            return PatchApplication(patch, existing, parent, None, validation)
        revision = WorkflowRevision(
            **candidate.model_dump(),
            workflow_revision_id=revision_id,
            workspace_id=self.workspace_id,
            revision=-1,
            parent_workflow_revision_id=base.workflow_revision_id,
            content_hash=validation.compilation.content_hash,
            source_revision=0,
            source_hash=patch.source.content_hash,
            created_by=patch.requested_by,
            created_at=self.clock(),
        )
        revision = self.journal.workflows.store_detached_revision(revision)
        return PatchApplication(patch, revision, parent, None, validation)

    def apply(
        self,
        patch: FutureGraphPatch,
        *,
        active_model=None,
        command_id: str | None = None,
    ) -> PatchApplication:
        digest = None
        if command_id is not None:
            validate_prefixed_id(command_id, "cmd")
            digest = request_digest(
                PATCH_APPLY_OPERATION,
                {"patch_digest": patch.source.content_hash, "patch_id": patch.workflow_patch_id},
            )
            replay = self._patch_replay(self.journal, command_id, digest, patch=patch)
            if replay is not None:
                return replay
        saved = self.save(patch, active_model=active_model)
        parent = self.journal.workflows.get_run(self.workspace_id, patch.parent_run_id)
        if parent is None or parent.row_version != patch.expected_parent_row_version:
            raise ApplicationError(ApplicationErrorCode.CONFLICT, "patch parent revision conflict")
        if parent.status is WorkflowStatus.BLOCKED:
            raise ApplicationError(
                ApplicationErrorCode.NEEDS_RECOVERY,
                "blocked Workflow patches may be saved, but Recovery must resolve before handoff",
            )
        if parent.status is not WorkflowStatus.PAUSED or not parent.pause_requested:
            raise ApplicationError(
                ApplicationErrorCode.INVALID,
                "patch handoff requires an OCC-current paused and fully drained parent",
            )
        execution = saved.validation.execution_node_ids
        consumed = self.journal.count_lineage_agent_requests(
            self.workspace_id, parent.effective_lineage_budget_root_run_id
        )
        workflow_cap = saved.revision.budget.max_agent_generation_requests
        if execution and workflow_cap is not None and consumed >= workflow_cap:
            raise ApplicationError(
                ApplicationErrorCode.INVALID,
                "budget_exhausted: patch saved, but the continuation cannot start",
            )
        if (
            execution
            and parent.admission_deadline_at is not None
            and self.clock() > parent.admission_deadline_at
        ):
            raise ApplicationError(
                ApplicationErrorCode.INVALID,
                "deadline_exceeded: patch saved, but the continuation cannot start",
            )
        stamp = self.clock()
        child_id = self.id_source.new_id("wrun")
        child = WorkflowRun(
            workflow_run_id=child_id,
            workspace_id=self.workspace_id,
            workflow_revision_id=saved.revision.workflow_revision_id,
            root_task_run_id=parent.root_task_run_id,
            status=WorkflowStatus.RUNNING,
            budget_snapshot=saved.revision.budget,
            started_at=stamp,
            admission_deadline_at=parent.admission_deadline_at,
            input_artifacts=parent.input_artifacts,
            invoking_client_message_id=parent.invoking_client_message_id,
            invoking_root_row_version=parent.invoking_root_row_version,
            run_relation="continuation",
            lineage_budget_root_run_id=parent.effective_lineage_budget_root_run_id,
            parent_run_id=parent.workflow_run_id,
        )
        node_rows = tuple(
            NodeRun(
                node_run_id=self.id_source.new_id("nrun"),
                workspace_id=self.workspace_id,
                workflow_run_id=child_id,
                node_id=node_id,
            )
            for node_id in execution
        )
        execution_rows = tuple(
            WorkflowExecutionNode(
                workflow_run_id=child_id,
                node_id=node_id,
                topology_ordinal=ordinal,
                inclusion_reason="retained_future",
            )
            for ordinal, node_id in enumerate(execution)
        )
        imports = self._imports(parent, child_id, saved.revision, saved.validation.past_node_ids)

        def handoff(txn):
            if command_id is not None:
                replayed = self._patch_replay(txn, command_id, digest, patch=patch)
                if replayed is not None:
                    return replayed
            txn.workflows.create_continuation_run(
                parent,
                child,
                node_rows,
                execution_rows,
                imports,
                expected_parent_row_version=patch.expected_parent_row_version,
            )
            if not execution:
                self._require_empty_outputs(child_id, saved.revision)
                committed_child = self.finalizer.finalize_success(child_id)
            else:
                committed_child = child
            if command_id is not None:
                txn.put_application_command_receipt_in_txn(
                    self.workspace_id,
                    ApplicationCommandReceipt(
                        command_id=command_id,
                        workspace_id=self.workspace_id,
                        operation=PATCH_APPLY_OPERATION,
                        request_digest=digest,
                        result_kind="workflow_run",
                        result_id=committed_child.workflow_run_id,
                    ),
                )
            return PatchApplication(
                patch, saved.revision, parent, committed_child, saved.validation
            )

        return self.journal.transact(handoff)

    def _patch_replay(
        self,
        journal,
        command_id: str,
        digest: str,
        *,
        patch: FutureGraphPatch,
    ) -> PatchApplication | None:
        receipt = journal.get_application_command_receipt(self.workspace_id, command_id)
        if receipt is None:
            return None
        if receipt.operation != PATCH_APPLY_OPERATION or receipt.request_digest != digest:
            raise ApplicationError(
                ApplicationErrorCode.CONFLICT,
                "command ID was reused with a different request",
            )
        parent = journal.workflows.get_run(self.workspace_id, patch.parent_run_id)
        child = journal.workflows.get_run(self.workspace_id, receipt.result_id or "")
        if parent is None or child is None:
            raise ApplicationError(
                ApplicationErrorCode.NEEDS_RECOVERY, "patch receipt result is missing"
            )
        revision = journal.workflows.get_revision(self.workspace_id, child.workflow_revision_id)
        if revision is None or revision.workflow_revision_id != self._revision_id(patch):
            raise ApplicationError(
                ApplicationErrorCode.NEEDS_RECOVERY, "patch receipt Revision is missing"
            )
        execution = tuple(
            item.node_id
            for item in sorted(
                journal.workflows.list_execution_nodes(self.workspace_id, child.workflow_run_id),
                key=lambda item: item.topology_ordinal,
            )
        )
        execution_set = set(execution)
        past = tuple(
            sorted(node.node_id for node in revision.nodes if node.node_id not in execution_set)
        )
        candidate = CompiledWorkflow(
            **{name: getattr(revision, name) for name in CompiledWorkflow.model_fields}
        )
        validation = PatchValidation(CompilationResult(candidate, ()), past, execution)
        return PatchApplication(patch, revision, parent, child, validation)

    def rerun(
        self, parent_run_id: str, *, full: bool, command_id: str | None = None
    ) -> RerunApplication:
        """Create an explicit new-budget rerun after the root Task was resumed.

        With ``command_id`` the creation receipt is written in the same
        transaction as the rerun child, so a client retry after any crash can
        never double-apply: the replay path rebuilds the recorded child.
        """

        digest = None
        if command_id is not None:
            validate_prefixed_id(command_id, "cmd")
            digest = sha256_digest(
                canonical_json_bytes(
                    {
                        "operation": RERUN_OPERATION,
                        "parent_run_id": parent_run_id,
                        "full": full,
                    }
                )
            )
            replay = self._rerun_replay(
                self.journal, command_id, digest, parent_run_id=parent_run_id, full=full
            )
            if replay is not None:
                return replay
        parent = self.journal.workflows.get_run(self.workspace_id, parent_run_id)
        if parent is None:
            raise ApplicationError(ApplicationErrorCode.NOT_FOUND, "rerun parent is missing")
        if not parent.status.terminal:
            raise ApplicationError(
                ApplicationErrorCode.INVALID,
                "rerun requires a terminal parent; active failed-node work uses Recovery",
            )
        revision = self.journal.workflows.get_revision(
            self.workspace_id, parent.workflow_revision_id
        )
        if revision is None:
            raise ApplicationError(
                ApplicationErrorCode.NEEDS_RECOVERY, "rerun parent Revision is missing"
            )
        parent_nodes = {
            item.node_id: item
            for item in self.journal.workflows.list_nodes(self.workspace_id, parent_run_id)
        }
        order = stable_execution_order(revision)
        inherited = (
            ()
            if full
            else tuple(
                node_id
                for node_id in order
                if parent_nodes.get(node_id) is not None
                and parent_nodes[node_id].status is WorkflowStatus.COMPLETED
            )
        )
        execution = tuple(node_id for node_id in order if node_id not in inherited)
        if not full and not any(
            item.status is WorkflowStatus.FAILED for item in parent_nodes.values()
        ):
            raise ApplicationError(
                ApplicationErrorCode.INVALID,
                "completed-node partial rerun is unsupported; request a full rerun",
            )
        stamp = self.clock()
        child_id = self.id_source.new_id("wrun")
        child = WorkflowRun(
            workflow_run_id=child_id,
            workspace_id=self.workspace_id,
            workflow_revision_id=revision.workflow_revision_id,
            root_task_run_id=parent.root_task_run_id,
            status=WorkflowStatus.RUNNING,
            budget_snapshot=revision.budget,
            started_at=stamp,
            admission_deadline_at=(
                stamp + timedelta(seconds=revision.budget.admission_timeout_seconds)
                if revision.budget.admission_timeout_seconds is not None
                else None
            ),
            input_artifacts=parent.input_artifacts,
            invoking_client_message_id=parent.invoking_client_message_id,
            invoking_root_row_version=parent.invoking_root_row_version,
            run_relation="rerun",
            lineage_budget_root_run_id=child_id,
            parent_run_id=parent.workflow_run_id,
        )
        nodes = tuple(
            NodeRun(
                node_run_id=self.id_source.new_id("nrun"),
                workspace_id=self.workspace_id,
                workflow_run_id=child_id,
                node_id=node_id,
            )
            for node_id in execution
        )
        execution_rows = tuple(
            WorkflowExecutionNode(
                workflow_run_id=child_id,
                node_id=node_id,
                topology_ordinal=ordinal,
                inclusion_reason=(
                    "initial"
                    if full
                    else "failed_retry"
                    if parent_nodes.get(node_id) is not None
                    and parent_nodes[node_id].status is WorkflowStatus.FAILED
                    else "retained_future"
                ),
            )
            for ordinal, node_id in enumerate(execution)
        )
        imports = () if full else self._imports(parent, child_id, revision, inherited)
        if command_id is None:
            self.journal.workflows.create_rerun(parent, child, nodes, execution_rows, imports)
            return RerunApplication(parent, child, full, inherited, execution)

        def work(txn) -> RerunApplication:
            replayed = self._rerun_replay(
                txn, command_id, digest, parent_run_id=parent_run_id, full=full
            )
            if replayed is not None:
                return replayed
            txn.workflows.create_rerun(parent, child, nodes, execution_rows, imports)
            txn.put_application_command_receipt_in_txn(
                self.workspace_id,
                ApplicationCommandReceipt(
                    command_id=command_id,
                    workspace_id=self.workspace_id,
                    operation=RERUN_OPERATION,
                    request_digest=digest,
                    result_kind="workflow_run",
                    result_id=child.workflow_run_id,
                ),
            )
            return RerunApplication(parent, child, full, inherited, execution)

        return self.journal.transact(work)

    def _rerun_replay(
        self, journal, command_id: str, digest: str, *, parent_run_id: str, full: bool
    ) -> RerunApplication | None:
        receipt = journal.get_application_command_receipt(self.workspace_id, command_id)
        if receipt is None:
            return None
        if receipt.operation != RERUN_OPERATION or receipt.request_digest != digest:
            raise ApplicationError(
                ApplicationErrorCode.CONFLICT,
                "command ID was reused with a different request",
            )
        parent = journal.workflows.get_run(self.workspace_id, parent_run_id)
        child = journal.workflows.get_run(self.workspace_id, receipt.result_id or "")
        if parent is None or child is None:
            raise ApplicationError(
                ApplicationErrorCode.NEEDS_RECOVERY, "rerun receipt result is missing"
            )
        revision = journal.workflows.get_revision(self.workspace_id, child.workflow_revision_id)
        if revision is None:
            raise ApplicationError(
                ApplicationErrorCode.NEEDS_RECOVERY, "rerun child Revision is missing"
            )
        execution = tuple(
            item.node_id
            for item in sorted(
                journal.workflows.list_execution_nodes(self.workspace_id, child.workflow_run_id),
                key=lambda item: item.topology_ordinal,
            )
        )
        inherited = tuple(
            node for node in stable_execution_order(revision) if node not in set(execution)
        )
        return RerunApplication(parent, child, full, inherited, execution)

    def _require_base(self, patch: FutureGraphPatch):
        if patch.workspace_id != self.workspace_id:
            raise ApplicationError(ApplicationErrorCode.INVALID, "patch workspace mismatch")
        parent = self.journal.workflows.get_run(self.workspace_id, patch.parent_run_id)
        if parent is None:
            raise ApplicationError(ApplicationErrorCode.NOT_FOUND, "patch parent run is missing")
        if (
            parent.workflow_revision_id != patch.base_workflow_revision_id
            or parent.row_version != patch.expected_parent_row_version
        ):
            raise ApplicationError(ApplicationErrorCode.CONFLICT, "patch parent revision conflict")
        base = self.journal.workflows.get_revision(
            self.workspace_id, patch.base_workflow_revision_id
        )
        if base is None:
            raise ApplicationError(ApplicationErrorCode.NOT_FOUND, "patch base Revision is missing")
        return parent, base

    def _past_node_ids(self, parent: WorkflowRun) -> set[str]:
        admitted = {
            item.node_id
            for item in self.journal.workflows.list_nodes(self.workspace_id, parent.workflow_run_id)
            if item.started_at is not None
            or item.agent_run_id is not None
            or item.status
            in {
                WorkflowStatus.RUNNING,
                WorkflowStatus.COMPLETED,
                WorkflowStatus.FAILED,
                WorkflowStatus.BLOCKED,
            }
        }
        revision = self.journal.workflows.get_revision(
            self.workspace_id, parent.workflow_revision_id
        )
        if revision is None:
            raise ApplicationError(
                ApplicationErrorCode.NEEDS_RECOVERY, "patch parent Revision is missing"
            )
        execution = {
            item.node_id
            for item in self.journal.workflows.list_execution_nodes(
                self.workspace_id, parent.workflow_run_id
            )
        }
        inherited = {item.node_id for item in revision.nodes} - execution
        imported = {
            item.source_node_id for item in self.outputs.list_imports(parent.workflow_run_id)
        }
        return admitted | inherited | imported

    def _imports(self, parent, child_id, revision, past_node_ids):
        parent_nodes = {
            item.node_id: item
            for item in self.journal.workflows.list_nodes(self.workspace_id, parent.workflow_run_id)
        }
        prior_imports = {
            (item.source_node_id, item.output_slot): item
            for item in self.outputs.list_imports(parent.workflow_run_id)
        }
        imports = []
        for node in revision.nodes:
            if node.node_id not in past_node_ids:
                continue
            for slot in node.output_contracts:
                binding = self.outputs.resolve(parent.workflow_run_id, node.node_id, slot.slot)
                if binding is None:
                    continue
                prior = prior_imports.get((node.node_id, slot.slot))
                source_node = parent_nodes.get(node.node_id)
                imports.append(
                    WorkflowArtifactImport(
                        workflow_run_id=child_id,
                        source_workflow_run_id=(
                            prior.source_workflow_run_id if prior else parent.workflow_run_id
                        ),
                        source_node_run_id=(
                            prior.source_node_run_id if prior else source_node.node_run_id
                        ),
                        source_node_id=node.node_id,
                        output_slot=slot.slot,
                        artifact_id=binding.artifact_id,
                        contract=binding.contract,
                        inherited_at=self.clock(),
                    )
                )
        return tuple(imports)

    def _require_empty_outputs(self, child_id, revision):
        missing = [
            f"{ref.node_id}.{ref.output_slot}"
            for ref in revision.required_outputs
            if self.outputs.resolve(child_id, ref.node_id, ref.output_slot) is None
        ]
        if missing:
            raise ApplicationError(
                ApplicationErrorCode.INVALID,
                "output_contract_unsatisfied: empty continuation lacks " + ", ".join(missing),
            )

    @staticmethod
    def _revision_id(patch: FutureGraphPatch) -> str:
        return "wrev_patch_" + patch.workflow_patch_id.removeprefix("wpatch_")
