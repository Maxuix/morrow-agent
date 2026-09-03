"""Idempotent Workflow Start command.

Start never implicitly selects, creates, abandons or resumes a root Task; the
caller uses the existing TaskService first. One final transaction rechecks the
request-digest-bound receipt plus every mutable admission fact before freezing
the clock and creating the run, its input binding and the queued NodeRuns.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from morrow.application.artifacts import ArtifactService
from morrow.application.workflows.artifacts import ensure_workflow_payload
from morrow.core.application import (
    ApplicationCommandReceipt,
    ApplicationError,
    ApplicationErrorCode,
)
from morrow.core.domain import (
    CLIENT_MESSAGE_ID_PATTERN,
    TaskRunPurpose,
    TaskRunStatus,
    canonical_json_bytes,
    session_can_start_work,
    sha256_digest,
    validate_prefixed_id,
)
from morrow.core.models import utc_now
from morrow.core.workflows.contracts import (
    ArtifactBinding,
    ContractRef,
    TaskContract,
    workflow_input_artifact_id,
)
from morrow.core.workflows.definitions import WorkflowRevision
from morrow.core.workflows.runs import NodeRun, WorkflowRun

START_OPERATION = "workflow_start"


@dataclass(frozen=True)
class StartWorkflowCommand:
    workflow_definition_id: str
    workflow_revision_id: str
    session_id: str
    root_task_run_id: str
    expected_root_row_version: int
    contract: TaskContract
    command_id: str
    client_message_id: str | None = None


@dataclass(frozen=True)
class WorkflowStartResult:
    run: WorkflowRun
    receipt: ApplicationCommandReceipt
    replayed: bool


def start_request_digest(command: StartWorkflowCommand) -> str:
    return sha256_digest(
        canonical_json_bytes(
            {
                "operation": START_OPERATION,
                "workflow_definition_id": command.workflow_definition_id,
                "workflow_revision_id": command.workflow_revision_id,
                "session_id": command.session_id,
                "root_task_run_id": command.root_task_run_id,
                "expected_root_row_version": command.expected_root_row_version,
                "client_message_id": command.client_message_id,
                "contract": sha256_digest(
                    canonical_json_bytes(command.contract.model_dump(mode="json"))
                ),
            }
        )
    )


class WorkflowStartService:
    def __init__(
        self,
        journal,
        *,
        workspace_id: str,
        artifacts: ArtifactService,
        id_source,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self.journal = journal
        self.workspace_id = validate_prefixed_id(workspace_id, "ws")
        self.artifacts = artifacts
        self.id_source = id_source
        self.clock = clock

    def start(self, command: StartWorkflowCommand) -> WorkflowStartResult:
        validate_prefixed_id(command.command_id, "cmd")
        digest = start_request_digest(command)
        replay = self._replay(self.journal, command.command_id, digest)
        if replay is not None:
            return replay

        revision = self._check_revision(command)
        self._check_direct_binding(command, revision)
        root = self._check_root(command)
        artifact_id = workflow_input_artifact_id(command.command_id)
        # The value-sensitive input projection already ran at TaskContract
        # construction; publication revalidates the same profile. A failure here
        # leaves at most an unbound immutable Artifact, never a partial Run.
        ensure_workflow_payload(
            self.artifacts,
            command.contract,
            session_id=root.session_id,
            task_run_id=root.task_run_id,
            artifact_id=artifact_id,
        )

        def work(txn) -> WorkflowStartResult:
            replayed = self._replay(txn, command.command_id, digest)
            if replayed is not None:
                return replayed
            self._check_mutable_facts(txn, command, revision)
            started_at = self.clock()
            workflow_run_id = self.id_source.new_id("wrun")
            run = WorkflowRun(
                workflow_run_id=workflow_run_id,
                workspace_id=self.workspace_id,
                workflow_revision_id=revision.workflow_revision_id,
                root_task_run_id=command.root_task_run_id,
                budget_snapshot=revision.budget,
                started_at=started_at,
                admission_deadline_at=started_at
                + timedelta(seconds=revision.budget.admission_timeout_seconds),
                input_artifacts=(
                    ArtifactBinding(
                        name="task",
                        artifact_id=artifact_id,
                        contract=ContractRef(kind="TaskContract"),
                    ),
                ),
                invoking_client_message_id=command.client_message_id,
                invoking_root_row_version=(
                    command.expected_root_row_version
                    if command.client_message_id is not None
                    else None
                ),
                lineage_budget_root_run_id=workflow_run_id,
            )
            nodes = tuple(
                NodeRun(
                    node_run_id=self.id_source.new_id("nrun"),
                    workspace_id=self.workspace_id,
                    workflow_run_id=run.workflow_run_id,
                    node_id=node.node_id,
                )
                for node in revision.nodes
            )
            txn.workflows.create_run(run, nodes)
            receipt = txn.put_application_command_receipt_in_txn(
                self.workspace_id,
                ApplicationCommandReceipt(
                    command_id=command.command_id,
                    workspace_id=self.workspace_id,
                    session_id=command.session_id,
                    operation=START_OPERATION,
                    request_digest=digest,
                    result_kind="workflow_run",
                    result_id=run.workflow_run_id,
                    created_at=started_at,
                ),
            )
            return WorkflowStartResult(run, receipt, False)

        return self.journal.transact(work)

    # Admission facts ------------------------------------------------------------

    def _replay(self, reader, command_id: str, digest: str) -> WorkflowStartResult | None:
        receipt = reader.get_application_command_receipt(self.workspace_id, command_id)
        if receipt is None:
            return None
        if receipt.request_digest != digest:
            raise ApplicationError(
                ApplicationErrorCode.CONFLICT,
                "workflow start command ID was reused with a different request",
            )
        run = reader.workflows.get_run(self.workspace_id, receipt.result_id or "")
        if run is None:
            raise ApplicationError(
                ApplicationErrorCode.NEEDS_RECOVERY, "workflow start command result is missing"
            )
        return WorkflowStartResult(run, receipt, True)

    def _check_revision(self, command: StartWorkflowCommand) -> WorkflowRevision:
        revision = self.journal.workflows.get_revision(
            self.workspace_id, command.workflow_revision_id
        )
        if revision is None:
            raise ApplicationError(
                ApplicationErrorCode.INVALID,
                "Workflow revision is not published; publish the definition first",
            )
        if revision.workflow_definition_id != command.workflow_definition_id:
            raise ApplicationError(
                ApplicationErrorCode.INVALID,
                "Workflow revision does not belong to the requested definition",
            )
        self._check_gates(self.journal, revision)
        return revision

    @staticmethod
    def _check_direct_binding(command: StartWorkflowCommand, revision: WorkflowRevision) -> None:
        direct = revision.nodes[0].conversation_scope == "invoking_session"
        if direct:
            if command.client_message_id is None:
                raise ApplicationError(
                    ApplicationErrorCode.INVALID,
                    "invoking_session Workflow requires a distinct client-message ID",
                )
            if not CLIENT_MESSAGE_ID_PATTERN.match(command.client_message_id):
                raise ApplicationError(
                    ApplicationErrorCode.INVALID,
                    "client-message ID must be a bounded opaque command field",
                )
        elif command.client_message_id is not None:
            raise ApplicationError(
                ApplicationErrorCode.INVALID,
                "client-message ID is only valid for an invoking_session Workflow",
            )

    def _check_gates(self, reader, revision: WorkflowRevision) -> None:
        """Head enable gates and revocation absence (rechecked in the final txn)."""

        head = reader.workflows.get_head(self.workspace_id, revision.workflow_definition_id)
        if head is None or not head.enabled:
            raise ApplicationError(
                ApplicationErrorCode.INVALID,
                "Workflow head is disabled; enable it before starting a run",
            )
        if (
            reader.workflows.get_revocation(self.workspace_id, revision.workflow_revision_id)
            is not None
        ):
            raise ApplicationError(
                ApplicationErrorCode.INVALID,
                "policy_revoked: the Workflow revision was revoked",
            )
        for node in revision.nodes:
            ref = node.agent_definition_ref
            version = reader.agent_definitions.get_version(self.workspace_id, ref.version_id)
            if version is None:
                raise ApplicationError(
                    ApplicationErrorCode.INVALID,
                    "referenced AgentDefinitionVersion is missing",
                )
            agent_head = reader.agent_definitions.get_head(
                self.workspace_id, version.source.definition_id
            )
            if agent_head is None or not agent_head.enabled:
                raise ApplicationError(
                    ApplicationErrorCode.INVALID,
                    f"Agent definition {version.source.definition_id} is disabled; "
                    "enable it before starting a run",
                )
            if (
                reader.agent_definitions.get_revocation(self.workspace_id, ref.version_id)
                is not None
            ):
                raise ApplicationError(
                    ApplicationErrorCode.INVALID,
                    "policy_revoked: a referenced AgentDefinitionVersion was revoked",
                )

    def _check_root(self, command: StartWorkflowCommand):
        session = self.journal.get_session(self.workspace_id, command.session_id)
        if session is None or not session_can_start_work(session.lifecycle, session.health):
            raise ApplicationError(
                ApplicationErrorCode.INVALID,
                "invoking Session is missing or cannot start work",
            )
        root = self.journal.get_task_run(self.workspace_id, command.root_task_run_id)
        if (
            root is None
            or root.session_id != command.session_id
            or root.purpose is not TaskRunPurpose.USER
        ):
            raise ApplicationError(
                ApplicationErrorCode.INVALID,
                "root TaskRun must be an explicit purpose=user task of the invoking Session",
            )
        if root.status is not TaskRunStatus.OPEN:
            raise ApplicationError(
                ApplicationErrorCode.INVALID,
                f"root TaskRun is {root.status.value}; use the explicit TaskService command first",
            )
        if root.row_version != command.expected_root_row_version:
            raise ApplicationError(ApplicationErrorCode.STALE, "root TaskRun row version is stale")
        if session.current_task_run_id != root.task_run_id:
            raise ApplicationError(
                ApplicationErrorCode.INVALID,
                "root TaskRun is not the Session's current task",
            )
        return root

    def _check_mutable_facts(
        self, txn, command: StartWorkflowCommand, revision: WorkflowRevision
    ) -> None:
        self._check_gates(txn, revision)
        session = txn.get_session(self.workspace_id, command.session_id)
        root = txn.get_task_run(self.workspace_id, command.root_task_run_id)
        if (
            session is None
            or not session_can_start_work(session.lifecycle, session.health)
            or root is None
            or root.session_id != command.session_id
            or root.purpose is not TaskRunPurpose.USER
            or root.status is not TaskRunStatus.OPEN
            or root.row_version != command.expected_root_row_version
            or session.current_task_run_id != root.task_run_id
        ):
            raise ApplicationError(
                ApplicationErrorCode.INVALID,
                "Workflow start admission facts changed; retry with current state",
            )
        if txn.workflows.active_for_root(self.workspace_id, root.task_run_id) is not None:
            raise ApplicationError(
                ApplicationErrorCode.CONFLICT,
                "root TaskRun already has an active WorkflowRun; use its foreground "
                "cancellation or recovery path",
            )
        if txn.has_open_turn_submission(
            self.workspace_id, command.session_id
        ) or txn.has_nonterminal_agent_run(self.workspace_id, command.session_id):
            raise ApplicationError(
                ApplicationErrorCode.CONFLICT,
                "invoking Session already has work in progress",
            )
