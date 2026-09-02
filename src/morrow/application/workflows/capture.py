"""Workflow Writer capture at the durable tool handler-completion boundary.

Ordinary Direct and non-Workflow tools never receive this collaborator. Capture
publishes complete unified diffs when representable and otherwise a structural
manifest; it never scans the workspace after the fact.
"""

from __future__ import annotations

from morrow.application.artifacts import ArtifactService
from morrow.core.artifacts import (
    ArtifactError,
    ArtifactErrorCode,
    ArtifactKind,
    ArtifactProvenanceKind,
    ArtifactProvenanceRef,
    ArtifactState,
)
from morrow.core.capabilities import ValidationFact
from morrow.core.domain import (
    ArtifactReference,
    canonical_json_bytes,
    sha256_digest,
    workflow_secret_spans,
)
from morrow.core.execution import DurableToolExecution
from morrow.core.workflows.contracts import (
    CAPTURE_SCHEMA_VERSION,
    ChangeCapture,
    ContractRef,
    TestReport,
    TestReportItem,
    capture_artifact_id,
    workflow_payload_excerpt,
)
from morrow.runtime.tools import ToolExecutionOutcome
from morrow.services.files import ChangeCaptureDraft, WorkspaceMutationService

CHANGE_CAPTURE_ROLE = "change_capture"
VALIDATION_REPORT_ROLE = "validation_report"


class ChangeArtifactCapture:
    """Publish change/validation Artifacts and attach refs to the ToolExecution."""

    def __init__(
        self,
        artifacts: ArtifactService,
        mutation: WorkspaceMutationService | None = None,
    ) -> None:
        self.artifacts = artifacts
        self.mutation = mutation

    def capture(
        self,
        execution: DurableToolExecution,
        result: ToolExecutionOutcome,
        artifact_refs: list[ArtifactReference],
    ) -> tuple[ArtifactReference, ...]:
        published: list[ArtifactReference] = []
        if self.mutation is not None:
            for draft in self.mutation.take_captures(execution.agent_run_id, execution.call_id):
                published.append(self._publish_change(execution, draft))
        output_ref = next(
            (ref.artifact_id for ref in artifact_refs if ref.role == "tool_output"),
            None,
        )
        for fact in result.facts:
            if isinstance(fact, ValidationFact):
                published.append(self._publish_validation(execution, fact, output_ref))
        return tuple(published)

    def _publish_change(
        self, execution: DurableToolExecution, draft: ChangeCaptureDraft
    ) -> ArtifactReference:
        payload = _change_payload(draft)
        return self._publish(
            execution,
            payload,
            role=CHANGE_CAPTURE_ROLE,
            kind=ArtifactKind.DIFF if payload.content_complete else ArtifactKind.PATCH,
            path=draft.path,
        )

    def _publish_validation(
        self,
        execution: DurableToolExecution,
        fact: ValidationFact,
        output_ref: str | None,
    ) -> ArtifactReference:
        complete = output_ref is not None
        payload = TestReport(
            items=(
                TestReportItem(
                    validator_kind=fact.validator_kind,
                    scope=fact.scope,
                    status=fact.status,
                    exit_code=fact.exit_code,
                    evidence_summary=fact.evidence_summary,
                    output_ref=output_ref,
                    content_complete=complete,
                    omission_reason=None if complete else "command_output_unavailable",
                    tool_execution_id=execution.tool_execution_id,
                ),
            ),
            content_complete=complete,
            omission_reason=None if complete else "command_output_unavailable",
        )
        return self._publish(
            execution,
            payload,
            role=VALIDATION_REPORT_ROLE,
            kind=ArtifactKind.TEST_REPORT,
        )

    def _publish(
        self,
        execution: DurableToolExecution,
        payload: ChangeCapture | TestReport,
        *,
        role: str,
        kind: ArtifactKind,
        path: str | None = None,
    ) -> ArtifactReference:
        artifact_id = capture_artifact_id(
            execution.tool_execution_id, role, CAPTURE_SCHEMA_VERSION, path=path
        )
        prior = self.artifacts.get(artifact_id)
        content = canonical_json_bytes(payload.model_dump(mode="json"))
        if prior is not None and prior.state is ArtifactState.AVAILABLE:
            if prior.sha256 != sha256_digest(content):
                raise ArtifactError(
                    ArtifactErrorCode.CONFLICT,
                    "capture Artifact already has different content",
                )
            return ArtifactReference(artifact_id=artifact_id, role=role)
        contract_kind = "ChangeCapture" if isinstance(payload, ChangeCapture) else "TestReport"
        self.artifacts.publish_workflow_capture(
            content,
            kind=kind,
            session_id=execution.session_id,
            task_run_id=execution.task_run_id,
            artifact_id=artifact_id,
            excerpt=workflow_payload_excerpt(payload),
            contract=ContractRef(kind=contract_kind),
            provenance_refs=(
                ArtifactProvenanceRef(
                    kind=ArtifactProvenanceKind.TOOL_EXECUTION,
                    reference_id=execution.tool_execution_id,
                    role=role,
                ),
            ),
        )
        return ArtifactReference(artifact_id=artifact_id, role=role)


def _change_payload(draft: ChangeCaptureDraft) -> ChangeCapture:
    diff = draft.unified_diff
    complete = draft.content_complete
    omission = draft.omission_reason
    if diff and workflow_secret_spans(diff):
        diff = None
        complete = False
        omission = "unsafe_or_unrepresentable_content"
    try:
        return ChangeCapture(
            path=draft.path,
            operation=draft.operation,
            status=draft.status,
            before_sha256=draft.before_sha256,
            after_sha256=draft.after_sha256,
            before_size=draft.before_size,
            after_size=draft.after_size,
            unified_diff=diff,
            content_complete=complete,
            omission_reason=omission,
        )
    except ValueError:
        return ChangeCapture(
            path=draft.path,
            operation=draft.operation,
            status=draft.status,
            before_sha256=draft.before_sha256,
            after_sha256=draft.after_sha256,
            before_size=draft.before_size,
            after_size=draft.after_size,
            unified_diff=None,
            content_complete=False,
            omission_reason="unsafe_or_unrepresentable_content",
        )
