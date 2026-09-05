"""Workflow-leaf composition hooks for the existing Turn lifecycle.

One optional, role-neutral collaborator is composed into the leaf's
TurnSubmissionCoordinator/SessionPersistence. It keeps AgentLoop unchanged:
Turn admission binds the pre-created queued NodeRun, and the terminal commit
publishes/binds declared outputs before the leaf Task transition applies.
Ordinary Direct Sessions never receive this collaborator.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from morrow.application.artifacts import ArtifactService
from morrow.application.tasks import TaskOutcomeAssembler
from morrow.application.workflows.artifacts import ensure_workflow_payload
from morrow.application.workflows.capture import CHANGE_CAPTURE_ROLE, VALIDATION_REPORT_ROLE
from morrow.application.workflows.evidence import text_result_from_assistant
from morrow.application.workflows.outputs import EffectiveOutputResolver
from morrow.application.workflows.submit import parse_submitted_payload, submission_digest
from morrow.core.application import ApplicationError, ApplicationErrorCode
from morrow.core.artifacts import ArtifactError, ArtifactErrorCode, ArtifactKind, ArtifactState
from morrow.core.domain import (
    TASK_TRANSITION_ID_PREFIX,
    DurableTaskRunTransition,
    TaskOutcomeTrigger,
    TaskRunStatus,
    canonical_json_bytes,
)
from morrow.core.faults import InjectedFault
from morrow.core.models import FinishReason
from morrow.core.workflows.contracts import (
    CAPTURE_OUTPUT_KINDS,
    CAPTURE_SCHEMA_VERSION,
    SUBMISSION_OUTPUT_KINDS,
    ArtifactBinding,
    ChangeCapture,
    ContractRef,
    ImplementationPatch,
    TestReport,
    TestReportItem,
    capture_artifact_id,
    node_output_artifact_id,
    node_submission_artifact_id,
)
from morrow.core.workflows.definitions import AgentNode
from morrow.core.workflows.replan import ReplanRequest, ReplanSignal
from morrow.core.workflows.runs import WorkflowStatus
from morrow.runtime.conversation import TurnTerminalRecord
from morrow.runtime.tools import ToolErrorCode, ToolExecutionError


@dataclass(frozen=True)
class WorkflowLeafContext:
    """Frozen identity of the single NodeRun one leaf Turn belongs to."""

    workflow_run_id: str
    workflow_revision_id: str
    node_run_id: str
    node: AgentNode
    leaf_session_id: str
    leaf_task_run_id: str
    effective_node_generation_request_cap: int | None


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
        parallel_read_digest: str | None = None,
    ) -> None:
        self.journal = journal
        self.workspace_id = workspace_id
        self.context = context
        self.artifacts = artifacts
        self.transitions = transitions
        self.id_source = id_source
        self.clock = clock
        self.outputs = EffectiveOutputResolver(journal, workspace_id=workspace_id)
        self.parallel_read_digest = parallel_read_digest
        self.read_contract_drift = False

    # Admission --------------------------------------------------------------

    def check_turn_admission_in_txn(self, txn, prepared_spec) -> tuple[str, ...]:
        """Frozen-evidence admission: revocation gates, never the mutable head."""

        ctx = self.context
        revision = txn.workflows.get_revision(self.workspace_id, ctx.workflow_revision_id)
        if revision is None:
            raise ApplicationError(
                ApplicationErrorCode.INVALID, "Workflow revision is missing for the admitted node"
            )
        run = txn.workflows.get_run(self.workspace_id, ctx.workflow_run_id)
        if run is None or run.workflow_revision_id != ctx.workflow_revision_id:
            raise ApplicationError(
                ApplicationErrorCode.INVALID, "Workflow run binding is inconsistent"
            )
        # The single authoritative admission gate (runtime contracts C2/C3):
        # Pause and queued→running have exactly one winner because both the
        # pause command and this recheck commit in authoritative transactions.
        # A pause rejection is a control outcome, never a node failure.
        if run.pause_requested or run.status not in (
            WorkflowStatus.QUEUED,
            WorkflowStatus.RUNNING,
        ):
            raise ApplicationError(
                ApplicationErrorCode.CONFLICT,
                "paused: the Workflow pause fact is set; admission is closed",
            )
        if txn.workflows.list_replan_signals(
            self.workspace_id, run.workflow_run_id, pending_only=True
        ):
            raise ApplicationError(
                ApplicationErrorCode.CONFLICT,
                "paused: unconsumed ReplanSignal closes node admission",
            )
        owner = txn.workflows.active_for_root(self.workspace_id, run.root_task_run_id)
        if owner is None or owner.workflow_run_id != run.workflow_run_id:
            raise ApplicationError(
                ApplicationErrorCode.CONFLICT,
                "Workflow root ownership moved before node admission",
            )
        execution_ids = {
            item.node_id
            for item in txn.workflows.list_execution_nodes(self.workspace_id, run.workflow_run_id)
        }
        legacy_initial = not execution_ids and run.run_relation == "initial"
        if ctx.node.node_id not in execution_ids and not legacy_initial:
            raise ApplicationError(
                ApplicationErrorCode.INVALID,
                "Workflow node is outside the immutable execution set",
            )
        if run.admission_deadline_at is not None and self.clock() > run.admission_deadline_at:
            raise ApplicationError(
                ApplicationErrorCode.INVALID,
                "deadline_exceeded: Workflow admission deadline reached",
            )
        workflow_cap = run.budget_snapshot.max_agent_generation_requests
        if workflow_cap is not None:
            remaining = workflow_cap - txn.count_lineage_agent_requests(
                self.workspace_id, run.effective_lineage_budget_root_run_id
            )
            if remaining <= 0:
                raise ApplicationError(
                    ApplicationErrorCode.INVALID,
                    "budget_exhausted: Workflow lineage budget is exhausted",
                )
        if ctx.node.conversation_scope == "invoking_session":
            root = txn.get_task_run(self.workspace_id, run.root_task_run_id)
            session = txn.get_session(self.workspace_id, ctx.leaf_session_id)
            if (
                root is None
                or session is None
                or ctx.leaf_task_run_id != run.root_task_run_id
                or root.session_id != ctx.leaf_session_id
                or session.current_task_run_id != root.task_run_id
                or root.status is not TaskRunStatus.OPEN
                or root.row_version != run.invoking_root_row_version
                or run.invoking_client_message_id is None
            ):
                raise ApplicationError(
                    ApplicationErrorCode.INVALID,
                    "Direct Workflow Turn admission facts changed; the bound root is no longer current",
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

    def bind_node_inputs_in_txn(self, txn) -> None:
        """Bind declared inputs inside the authoritative admission transaction.

        Binding inputs and admitting the node are one atomic step (runtime
        contract C2), so a Pause can never land between them. Replay of an
        identical set is a no-op through the journal's immutable-binding rule.
        """

        ctx = self.context
        node = txn.workflows.get_node(self.workspace_id, ctx.node_run_id)
        if node is None or node.status is not WorkflowStatus.QUEUED:
            return
        run = txn.workflows.get_run(self.workspace_id, ctx.workflow_run_id)
        if run is None:
            raise ApplicationError(
                ApplicationErrorCode.NEEDS_RECOVERY, "Workflow run is missing for the admitted node"
            )
        for binding in ctx.node.input_bindings:
            if binding.source == "workflow_input":
                source = run.input_artifacts[0]
                bound = ArtifactBinding(
                    name=binding.input_name,
                    artifact_id=source.artifact_id,
                    contract=source.contract,
                )
            else:
                ref = binding.node_output
                produced = self.outputs.resolve(ctx.workflow_run_id, ref.node_id, ref.output_slot)
                if produced is None:
                    raise ApplicationError(
                        ApplicationErrorCode.NEEDS_RECOVERY,
                        f"node {ctx.node.node_id} is not ready: input {binding.input_name} has "
                        "no bound producer Artifact",
                    )
                bound = ArtifactBinding(
                    name=binding.input_name,
                    artifact_id=produced.artifact_id,
                    contract=ContractRef(
                        kind=binding.accepts.kind, version=binding.accepts.version
                    ),
                )
            txn.workflows.bind_artifact(
                self.workspace_id,
                ctx.workflow_run_id,
                bound,
                node_run_id=ctx.node_run_id,
                direction="input",
            )

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
            parallel_read_digest=self.parallel_read_digest,
        )
        self.transitions.mark_run_running(ctx.workflow_run_id)

    def check_request_admission(self) -> None:
        """Deadline gate at the durable purpose=agent request-admission seam."""

        if self.read_contract_drift:
            from morrow.application.workflows.parallel import read_contract_error

            raise read_contract_error()
        run = self.journal.workflows.get_run(self.workspace_id, self.context.workflow_run_id)
        if (
            run is not None
            and run.admission_deadline_at is not None
            and self.clock() > run.admission_deadline_at
        ):
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

        if self.read_contract_drift:
            from morrow.application.workflows.parallel import read_contract_error

            raise read_contract_error()
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

    def submit_node_result(self, arguments) -> dict[str, object]:
        """Validate and publish one structured submission for this NodeRun."""

        ctx = self.context
        declared = {
            contract.slot: contract
            for contract in ctx.node.output_contracts
            if contract.kind in SUBMISSION_OUTPUT_KINDS
        }
        if arguments.schema_version != 1:
            raise ToolExecutionError(ToolErrorCode.INVALID_ARGUMENTS, "unsupported schema_version")
        parsed: dict[str, object] = {}
        for slot, raw in arguments.outputs.items():
            contract = declared.get(slot)
            if contract is None:
                raise ToolExecutionError(
                    ToolErrorCode.INVALID_ARGUMENTS,
                    f"undeclared structured slot '{slot}'",
                )
            if not isinstance(raw, dict):
                raise ToolExecutionError(
                    ToolErrorCode.INVALID_ARGUMENTS,
                    f"slot '{slot}' payload must be an object",
                )
            parsed[slot] = parse_submitted_payload(contract.kind, raw, slot=slot)
        missing = sorted(
            slot
            for slot, contract in declared.items()
            if contract.required_for_node_completion and slot not in parsed
        )
        if missing:
            raise ToolExecutionError(
                ToolErrorCode.INVALID_ARGUMENTS,
                "required structured slots are missing: " + ",".join(missing),
            )
        self._require_in_scope_evidence(arguments.evidence_refs)
        digest = submission_digest(
            parsed, arguments.summary, arguments.evidence_refs, arguments.replan
        )
        marker_id = node_submission_artifact_id(ctx.node_run_id)
        prior = self.artifacts.get(marker_id)
        if prior is not None and prior.state is ArtifactState.AVAILABLE:
            read = self.artifacts.read(marker_id, max_bytes=prior.byte_size)
            recorded = json.loads(read.content.decode("utf-8"))
            if recorded.get("digest") == digest:
                return {"submitted": True, "digest": digest, "reused": True}
            raise ToolExecutionError(
                ToolErrorCode.CONFLICT,
                "a conflicting structured submission already exists for this node",
            )
        try:
            for slot, payload in parsed.items():
                ensure_workflow_payload(
                    self.artifacts,
                    payload,
                    session_id=ctx.leaf_session_id,
                    task_run_id=ctx.leaf_task_run_id,
                    artifact_id=node_output_artifact_id(ctx.node_run_id, slot),
                    producer_node_run_id=ctx.node_run_id,
                    output_slot=slot,
                )
            self.artifacts.publish_bytes(
                canonical_json_bytes(
                    {
                        "digest": digest,
                        "slots": sorted(parsed),
                        "replan": arguments.replan.model_dump(mode="json")
                        if arguments.replan
                        else None,
                    }
                ),
                kind=ArtifactKind.TASK_SUMMARY,
                session_id=ctx.leaf_session_id,
                task_run_id=ctx.leaf_task_run_id,
                artifact_id=marker_id,
                excerpt="structured node result submission",
            )
        except ArtifactError as exc:
            if exc.code is ArtifactErrorCode.CONFLICT:
                raise ToolExecutionError(
                    ToolErrorCode.CONFLICT,
                    "a conflicting structured submission already exists for this node",
                ) from None
            raise ToolExecutionError(ToolErrorCode.PUBLISH_FAILED, exc.message) from None
        return {"submitted": True, "digest": digest, "reused": False}

    def _require_in_scope_evidence(self, refs: tuple[str, ...]) -> None:
        if not refs:
            return
        ctx = self.context
        node_executions = self._node_executions()
        executions = {item.tool_execution_id for item in node_executions}
        execution_artifacts = {
            reference.artifact_id
            for execution in node_executions
            for reference in execution.artifact_refs
        }
        for ref in refs:
            if ref.startswith("tex_"):
                if ref not in executions:
                    raise ToolExecutionError(
                        ToolErrorCode.INVALID_ARGUMENTS,
                        "evidence_refs must name ToolExecutions from this node",
                    )
                continue
            if ref.startswith("art_"):
                stored = self.artifacts.get(ref)
                if stored is None or not (
                    stored.producer_node_run_id == ctx.node_run_id or ref in execution_artifacts
                ):
                    raise ToolExecutionError(
                        ToolErrorCode.INVALID_ARGUMENTS,
                        "evidence_refs must name Artifacts from this node",
                    )
                continue
            raise ToolExecutionError(
                ToolErrorCode.INVALID_ARGUMENTS,
                "evidence_refs must be Artifact or ToolExecution identifiers",
            )

    def _node_executions(self):
        """Return only ToolExecutions owned by this NodeRun's admitted AgentRun."""

        ctx = self.context
        node_run = self.journal.workflows.get_node(self.workspace_id, ctx.node_run_id)
        if (
            node_run is None
            or node_run.workflow_run_id != ctx.workflow_run_id
            or node_run.agent_run_id is None
        ):
            raise ApplicationError(
                ApplicationErrorCode.INVALID,
                "Workflow node AgentRun binding is missing or inconsistent",
            )
        return self.journal.list_executions(self.workspace_id, agent_run_id=node_run.agent_run_id)

    def _prepare_required_outputs(self) -> tuple[ArtifactBinding, ...]:
        ctx = self.context
        bindings = []
        for contract in ctx.node.output_contracts:
            if not contract.required_for_node_completion:
                continue
            if contract.kind == "TextResult":
                bindings.append(self._commit_text_result(contract))
            elif contract.kind in CAPTURE_OUTPUT_KINDS:
                bindings.append(self._commit_capture_slot(contract))
            elif contract.kind in SUBMISSION_OUTPUT_KINDS:
                bindings.append(self._commit_submission_slot(contract))
            else:
                raise ApplicationError(
                    ApplicationErrorCode.INVALID,
                    f"output_contract_unsatisfied: unsupported kind {contract.kind}",
                )
        return tuple(bindings)

    def _commit_text_result(self, contract) -> ArtifactBinding:
        record_id, text = self._final_assistant()
        result = text_result_from_assistant(record_id, text)
        return self._bind_payload(contract, result)

    def _commit_submission_slot(self, contract) -> ArtifactBinding:
        ctx = self.context
        marker = self.artifacts.get(node_submission_artifact_id(ctx.node_run_id))
        artifact_id = node_output_artifact_id(ctx.node_run_id, contract.slot)
        stored = self.artifacts.get(artifact_id)
        if marker is None or marker.state is not ArtifactState.AVAILABLE or stored is None:
            raise ApplicationError(
                ApplicationErrorCode.INVALID,
                "output_contract_unsatisfied: required structured submission is missing",
            )
        return ArtifactBinding(
            name=contract.slot,
            artifact_id=artifact_id,
            contract=ContractRef(kind=contract.kind, version=contract.version),
        )

    def _commit_capture_slot(self, contract) -> ArtifactBinding:
        executions = self._node_executions()
        if contract.kind == "ImplementationPatch":
            payload = self._implementation_patch(executions)
        else:
            payload = self._aggregate_test_report(executions)
        return self._bind_payload(contract, payload)

    def _implementation_patch(self, executions) -> ImplementationPatch:
        refs: list[str] = []
        paths: list[str] = []
        complete = True
        omission = None
        writes_known = False
        seen: set[str] = set()

        def consume(artifact_id: str) -> None:
            nonlocal complete, omission
            if artifact_id in seen:
                return
            seen.add(artifact_id)
            refs.append(artifact_id)
            stored = self.artifacts.get(artifact_id)
            if stored is None or stored.state is not ArtifactState.AVAILABLE:
                complete = False
                omission = "change_capture_missing"
                return
            read = self.artifacts.read(artifact_id, max_bytes=stored.byte_size)
            capture = ChangeCapture.model_validate_json(read.content)
            paths.append(capture.path)
            if not capture.content_complete:
                complete = False
                omission = capture.omission_reason or "structural_manifest"

        for execution in executions:
            fact_paths: list[str] = []
            if execution.facts is not None:
                fact_paths.extend(item.relative_path for item in execution.facts.files)
            fact_paths.extend(item.relative_path for item in execution.intent.file_evidence)
            if fact_paths:
                writes_known = True
            for reference in execution.artifact_refs:
                if reference.role == CHANGE_CAPTURE_ROLE:
                    consume(reference.artifact_id)
            for path in dict.fromkeys(fact_paths):
                expected = capture_artifact_id(
                    execution.tool_execution_id,
                    CHANGE_CAPTURE_ROLE,
                    CAPTURE_SCHEMA_VERSION,
                    path=path,
                )
                stored = self.artifacts.get(expected)
                if stored is not None and stored.state is ArtifactState.AVAILABLE:
                    consume(expected)
                elif expected not in seen:
                    complete = False
                    omission = "change_capture_missing"
        if writes_known and not refs:
            complete = False
            omission = "change_capture_missing"
        changed_paths = tuple(dict.fromkeys(paths))
        change_refs = tuple(dict.fromkeys(refs))
        rationale = self._assistant_rationale()
        try:
            return ImplementationPatch(
                changed_paths=changed_paths,
                change_refs=change_refs,
                content_complete=complete,
                rationale=rationale,
                omission_reason=None if complete else omission,
            )
        except ValueError:
            return ImplementationPatch(
                changed_paths=changed_paths,
                change_refs=change_refs,
                content_complete=complete,
                rationale="",
                omission_reason=None if complete else omission,
            )

    def _aggregate_test_report(self, executions) -> TestReport:
        items: list[TestReportItem] = []
        complete = True
        omission = None
        for execution in executions:
            for reference in execution.artifact_refs:
                if reference.role != VALIDATION_REPORT_ROLE:
                    continue
                stored = self.artifacts.get(reference.artifact_id)
                if stored is None:
                    complete = False
                    omission = "validation_report_missing"
                    continue
                read = self.artifacts.read(reference.artifact_id, max_bytes=stored.byte_size)
                report = TestReport.model_validate_json(read.content)
                items.extend(report.items)
                if not report.content_complete:
                    complete = False
                    omission = report.omission_reason or "command_output_unavailable"
        return TestReport(
            items=tuple(items),
            content_complete=complete,
            omission_reason=None if complete else omission,
        )

    def _bind_payload(self, contract, payload) -> ArtifactBinding:
        ctx = self.context
        metadata = ensure_workflow_payload(
            self.artifacts,
            payload,
            session_id=ctx.leaf_session_id,
            task_run_id=ctx.leaf_task_run_id,
            artifact_id=node_output_artifact_id(ctx.node_run_id, contract.slot),
            producer_node_run_id=ctx.node_run_id,
            output_slot=contract.slot,
        )
        return ArtifactBinding(
            name=contract.slot,
            artifact_id=metadata.artifact_id,
            contract=ContractRef(kind=contract.kind, version=contract.version),
        )

    def _final_assistant(self) -> tuple[str, str]:
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
        return final.record_id, str(final.payload.get("content") or "")

    def _assistant_rationale(self) -> str:
        try:
            _record_id, text = self._final_assistant()
        except ApplicationError:
            return ""
        return text[:4096]

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
            for binding in () if self.parallel_read_digest is not None else bindings:
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
        marker = self.artifacts.get(node_submission_artifact_id(ctx.node_run_id))
        if marker is not None and marker.state is ArtifactState.AVAILABLE:
            recorded = json.loads(
                self.artifacts.read(marker.artifact_id, max_bytes=marker.byte_size).content
            )
            if recorded.get("replan") is not None:
                txn.workflows.put_replan_signal(
                    ReplanSignal(
                        signal_id="rsig_" + ctx.node_run_id.removeprefix("nrun_"),
                        workspace_id=self.workspace_id,
                        workflow_run_id=ctx.workflow_run_id,
                        node_run_id=ctx.node_run_id,
                        request=ReplanRequest.model_validate(recorded["replan"]),
                        created_at=self.clock(),
                    )
                )
        if ctx.node.conversation_scope == "invoking_session" and target in {
            TaskRunStatus.CANCELLED,
            TaskRunStatus.FAILED,
        }:
            updated = txn.get_task_run(self.workspace_id, ctx.leaf_task_run_id)
            outcome = TaskOutcomeAssembler(
                txn,
                workspace_id=self.workspace_id,
                id_source=self.id_source,
                clock=self.clock,
            ).build(
                updated,
                trigger=TaskOutcomeTrigger.TERMINAL_CLOSE,
                workflow_profile=True,
            )
            txn.put_task_outcome(self.workspace_id, outcome)
        # Isolated leaf evidence stays in its Turn/ToolExecution records; only
        # the root TaskRun ever produces a TaskOutcome. In invoking-session
        # scope this exact Task is the root, so ERROR/CANCEL uses the internal
        # Workflow safety profile above.
        return target.is_terminal
