"""Workflow-leaf composition hooks for the existing Turn lifecycle.

One optional, role-neutral collaborator is composed into the leaf's
TurnSubmissionCoordinator/SessionPersistence. It keeps AgentLoop unchanged:
Turn admission binds the pre-created queued NodeRun, and the terminal commit
publishes/binds declared outputs before the leaf Task transition applies.
Ordinary Direct Sessions never receive this collaborator.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from morrow.application.artifacts import ArtifactService
from morrow.application.workflows.artifacts import ensure_workflow_payload
from morrow.application.workflows.evidence import text_result_from_assistant
from morrow.core.application import ApplicationError, ApplicationErrorCode
from morrow.core.domain import (
    TASK_TRANSITION_ID_PREFIX,
    DurableTaskRunTransition,
    TaskRunStatus,
)
from morrow.core.faults import InjectedFault
from morrow.core.models import FinishReason
from morrow.core.workflows.contracts import (
    ArtifactBinding,
    ContractRef,
    node_output_artifact_id,
)
from morrow.core.workflows.definitions import AgentNode
from morrow.runtime.conversation import TurnTerminalRecord


@dataclass(frozen=True)
class WorkflowLeafContext:
    """Frozen identity of the single NodeRun one leaf Turn belongs to."""

    workflow_run_id: str
    workflow_revision_id: str
    node_run_id: str
    node: AgentNode
    leaf_session_id: str
    leaf_task_run_id: str
    effective_node_generation_request_cap: int


class WorkflowLeafHooks:
    """NodeResultCommitter seam: output commit plus leaf Task terminal mapping."""

    def __init__(
        self,
        journal,
        *,
        workspace_id: str,
        context: WorkflowLeafContext,
        artifacts: ArtifactService,
        transitions,
        id_source,
        clock: Callable[[], datetime],
    ) -> None:
        self.journal = journal
        self.workspace_id = workspace_id
        self.context = context
        self.artifacts = artifacts
        self.transitions = transitions
        self.id_source = id_source
        self.clock = clock

    # Admission --------------------------------------------------------------

    def check_turn_admission_in_txn(self, txn, prepared_spec) -> tuple[str, ...]:
        """Frozen-evidence admission: revocation gates, never the mutable head."""

        ctx = self.context
        revision = txn.workflows.get_revision(self.workspace_id, ctx.workflow_revision_id)
        if revision is None:
            raise ApplicationError(
                ApplicationErrorCode.INVALID, "Workflow revision is missing for the admitted node"
            )
        if txn.workflows.get_revocation(self.workspace_id, ctx.workflow_revision_id) is not None:
            raise ApplicationError(
                ApplicationErrorCode.INVALID,
                "policy_revoked: the frozen Workflow revision was revoked",
            )
        ref = ctx.node.agent_definition_ref
        if txn.agent_definitions.get_revocation(self.workspace_id, ref.version_id) is not None:
            raise ApplicationError(
                ApplicationErrorCode.INVALID,
                "policy_revoked: the frozen AgentDefinitionVersion was revoked",
            )
        version = txn.agent_definitions.get_version(self.workspace_id, ref.version_id)
        if version is None or (
            version.source.definition_id,
            version.content_hash,
        ) != (ref.definition_id, ref.content_hash):
            raise ApplicationError(
                ApplicationErrorCode.INVALID, "Workflow node Agent evidence is inconsistent"
            )
        spec = prepared_spec
        if (
            spec is None
            or spec.definition_ref != ref
            or spec.provider_runtime.model != ctx.node.resolved_model_ref
            or spec.conversation_session_id != ctx.leaf_session_id
            or spec.max_agent_generation_requests != ctx.effective_node_generation_request_cap
        ):
            raise ApplicationError(
                ApplicationErrorCode.INVALID,
                "Workflow leaf preparation does not match the frozen Revision node",
            )
        return version.source.skill_version_ids

    def admit_node_in_txn(self, txn, *, agent_run_id: str) -> None:
        """Atomically bind the queued NodeRun's leaf references inside Turn admission.

        The WorkflowTransitionService remains the sole writer; nested
        transactions join this Turn admission transaction.
        """

        ctx = self.context
        self.transitions.admit_node(
            ctx.node_run_id,
            conversation_session_id=ctx.leaf_session_id,
            leaf_task_run_id=ctx.leaf_task_run_id,
            agent_run_id=agent_run_id,
            effective_node_generation_request_cap=ctx.effective_node_generation_request_cap,
        )
        self.transitions.mark_run_running(ctx.workflow_run_id)

    def check_request_admission(self) -> None:
        """Deadline gate at the durable purpose=agent request-admission seam."""

        run = self.journal.workflows.get_run(self.workspace_id, self.context.workflow_run_id)
        if run is not None and self.clock() > run.admission_deadline_at:
            raise ApplicationError(
                ApplicationErrorCode.INVALID,
                "deadline_exceeded: Workflow admission deadline reached",
            )

    # Terminal commit ----------------------------------------------------------

    def prepare_terminal_outputs(self) -> tuple[ArtifactBinding, ...]:
        """Publish every required TextResult from the durable final Assistant message.

        Runs after the final Assistant commit and before the terminal transaction;
        replay-safe through the deterministic ``(node_run_id, output_slot)`` identity.
        A known Artifact failure becomes one bounded application error so the
        existing AgentLoop error path can close the leaf as failed.
        """

        try:
            return self._prepare_required_outputs()
        except ApplicationError:
            raise
        except InjectedFault:
            # A crash stays a crash; recovery replays the committer from
            # durable facts instead of manufacturing a failure mapping.
            raise
        except Exception as exc:
            raise ApplicationError(
                ApplicationErrorCode.INVALID,
                f"output_contract_unsatisfied: {exc}",
            ) from None

    def _prepare_required_outputs(self) -> tuple[ArtifactBinding, ...]:
        ctx = self.context
        records = self.journal.load_effective_records(self.workspace_id, ctx.leaf_session_id)
        final = None
        for record in records:
            if (
                record.kind == "message"
                and record.payload.get("role") == "assistant"
                and not record.payload.get("tool_calls")
            ):
                final = record
        if final is None:
            raise ApplicationError(
                ApplicationErrorCode.INVALID,
                "output_contract_unsatisfied: no committed final Assistant message",
            )
        text = str(final.payload.get("content") or "")
        bindings = []
        for contract in ctx.node.output_contracts:
            if not contract.required_for_node_completion:
                continue
            result = text_result_from_assistant(final.record_id, text)
            metadata = ensure_workflow_payload(
                self.artifacts,
                result,
                session_id=ctx.leaf_session_id,
                task_run_id=ctx.leaf_task_run_id,
                artifact_id=node_output_artifact_id(ctx.node_run_id, contract.slot),
                producer_node_run_id=ctx.node_run_id,
                output_slot=contract.slot,
            )
            bindings.append(
                ArtifactBinding(
                    name=contract.slot,
                    artifact_id=metadata.artifact_id,
                    contract=ContractRef(kind=contract.kind, version=contract.version),
                )
            )
        return tuple(bindings)

    def apply_terminal_in_txn(
        self,
        txn,
        session,
        terminal: TurnTerminalRecord,
        bindings: tuple[ArtifactBinding, ...],
        *,
        turn_id: str | None,
    ) -> bool:
        """Bind outputs and apply the leaf Task terminal inside the commit transaction."""

        ctx = self.context
        task = txn.get_task_run(self.workspace_id, ctx.leaf_task_run_id)
        if task is None:
            raise RuntimeError("Workflow leaf TaskRun is missing for the active turn")
        if terminal.finish_reason is FinishReason.STEERED:
            return False
        if terminal.finish_reason is FinishReason.STOP:
            target = TaskRunStatus.READY_FOR_ACCEPTANCE
            reason = "workflow_leaf_completed"
            declared = {
                contract.slot
                for contract in ctx.node.output_contracts
                if contract.required_for_node_completion
            }
            bound = {binding.name for binding in bindings}
            missing = declared - bound
            if missing:
                raise ApplicationError(
                    ApplicationErrorCode.INVALID,
                    "output_contract_unsatisfied: required outputs are not bound: "
                    + ",".join(sorted(missing)),
                )
            for binding in bindings:
                txn.workflows.bind_artifact(
                    self.workspace_id,
                    ctx.workflow_run_id,
                    binding,
                    node_run_id=ctx.node_run_id,
                    direction="output",
                )
        elif terminal.finish_reason is FinishReason.CANCELLED:
            target = TaskRunStatus.CANCELLED
            reason = "workflow_leaf_cancelled"
        else:
            target = TaskRunStatus.FAILED
            reason = "workflow_leaf_failed"
        txn.transition_workflow_task(
            self.workspace_id,
            ctx.workflow_run_id,
            ctx.leaf_task_run_id,
            target=target,
            transition=DurableTaskRunTransition(
                transition_id=self.id_source.new_id(TASK_TRANSITION_ID_PREFIX),
                workspace_id=self.workspace_id,
                session_id=ctx.leaf_session_id,
                task_run_id=ctx.leaf_task_run_id,
                from_status=task.status,
                to_status=target,
                reason=reason,
                turn_id=turn_id,
                attempt=task.attempt,
                created_at=self.clock(),
            ),
            expected_row_version=task.row_version,
        )
        # Leaf evidence stays in its Turn/ToolExecution records; only the root
        # TaskRun ever produces a TaskOutcome (the journal enforces this).
        return target.is_terminal
