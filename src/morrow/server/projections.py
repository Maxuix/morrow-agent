"""Explicit field-allowlist projections from domain objects to API payloads.

Every payload that crosses the transport is assembled here, field by field, so
the redaction boundary is reviewable in one place: no credentials, no full
sensitive tool arguments, no reasoning, no SDK objects, no tracebacks. Nested
domain models that are themselves validated user-facing facts (Workflow
revisions, Artifact metadata, Task outcomes) are dumped whole; anything
adjacent to secrets (providers, approvals) is restricted to safe fields.
"""

from __future__ import annotations

from typing import Any

from morrow.core.application import ApplicationCommandReceipt, ApplicationEvent
from morrow.core.artifacts import ArtifactMetadata
from morrow.core.domain import DurableSession, DurableTaskRun, TaskOutcome
from morrow.core.execution import DurableApproval, DurableToolExecution
from morrow.core.models import ModelRef
from morrow.core.workflows.runs import NodeRun, WorkflowArtifactImport, WorkflowRun


def _dump(model: Any, fields: tuple[str, ...]) -> dict[str, Any]:
    data = model.model_dump(mode="json")
    return {name: data[name] for name in fields}


def receipt_wire(receipt: ApplicationCommandReceipt | None) -> dict[str, Any] | None:
    if receipt is None:
        return None
    return _dump(
        receipt,
        (
            "command_id",
            "operation",
            "disposition",
            "result_kind",
            "result_id",
            "event_cursor",
            "row_version",
            "created_at",
        ),
    )


def event_wire(event: ApplicationEvent) -> dict[str, Any]:
    return _dump(
        event,
        (
            "cursor",
            "event_id",
            "event_type",
            "aggregate_kind",
            "aggregate_id",
            "payload",
            "created_at",
        ),
    )


def session_wire(session: DurableSession) -> dict[str, Any]:
    return _dump(
        session,
        (
            "session_id",
            "lifecycle",
            "health",
            "created_at",
            "updated_at",
            "current_task_run_id",
            "parent_session_id",
        ),
    )


def task_wire(task: DurableTaskRun) -> dict[str, Any]:
    return _dump(
        task,
        (
            "task_run_id",
            "session_id",
            "purpose",
            "status",
            "attempt",
            "row_version",
            "created_at",
            "updated_at",
            "accepted_at",
            "closed_at",
        ),
    )


def outcome_wire(outcome: TaskOutcome) -> dict[str, Any]:
    return _dump(
        outcome,
        (
            "outcome_id",
            "task_run_id",
            "session_id",
            "version",
            "task_status",
            "summary",
            "trigger",
            "completion_basis",
            "changed_paths",
            "side_effects",
            "unresolved_items",
            "feedback",
            "artifact_refs",
            "evidence_refs",
            "created_at",
        ),
    )


def artifact_wire(metadata: ArtifactMetadata) -> dict[str, Any]:
    return _dump(
        metadata,
        (
            "artifact_id",
            "kind",
            "contract",
            "output_slot",
            "state",
            "sensitivity",
            "retention",
            "byte_size",
            "sha256",
            "excerpt",
            "session_id",
            "task_run_id",
            "producer_node_run_id",
            "created_at",
            "updated_at",
            "row_version",
        ),
    )


def run_wire(run: WorkflowRun) -> dict[str, Any]:
    return _dump(
        run,
        (
            "workflow_run_id",
            "workflow_revision_id",
            "root_task_run_id",
            "status",
            "row_version",
            "started_at",
            "completed_at",
            "budget_snapshot",
            "admission_deadline_at",
            "input_artifacts",
            "result_status",
            "pending_terminal_intent",
            "pause_requested",
            "run_relation",
            "lineage_budget_root_run_id",
            "parent_run_id",
            "superseded_reason",
        ),
    )


def node_wire(node: NodeRun) -> dict[str, Any]:
    return _dump(
        node,
        (
            "node_run_id",
            "workflow_run_id",
            "node_id",
            "status",
            "attempt",
            "row_version",
            "started_at",
            "completed_at",
            "conversation_session_id",
            "leaf_task_run_id",
            "agent_run_id",
            "effective_node_generation_request_cap",
        ),
    )


def import_wire(item: WorkflowArtifactImport) -> dict[str, Any]:
    return _dump(
        item,
        (
            "workflow_run_id",
            "source_workflow_run_id",
            "source_node_run_id",
            "source_node_id",
            "output_slot",
            "artifact_id",
            "contract",
            "inherited_at",
        ),
    )


def node_view_wire(view) -> dict[str, Any]:
    return {
        "node": node_wire(view.node),
        "output_bindings": [
            _dump(binding, ("name", "artifact_id", "contract")) for binding in view.output_bindings
        ],
        "artifacts": [artifact_wire(item) for item in view.artifacts],
        "approval_pending": view.approval_pending,
    }


def run_view_wire(view) -> dict[str, Any]:
    """The observer's full run projection, including lineage-aware outputs."""

    return {
        "run": run_wire(view.run),
        "revision": view.revision.model_dump(mode="json"),
        "nodes": [node_view_wire(item) for item in view.nodes],
        "input_artifacts": [artifact_wire(item) for item in view.input_artifacts],
        "agent_generation_request_count": view.agent_generation_request_count,
        "lineage_agent_generation_request_count": view.lineage_agent_generation_request_count,
        "inherited_artifacts": [import_wire(item) for item in view.inherited_artifacts],
        "effective_outputs": [
            {
                "node_id": item.node_id,
                "output_slot": item.output_slot,
                "binding": _dump(item.binding, ("name", "artifact_id", "contract")),
                "artifact": artifact_wire(item.artifact) if item.artifact is not None else None,
                "inherited": item.inherited,
            }
            for item in view.effective_outputs
        ],
        "usage_availability": view.usage_availability,
        "terminal_outcome": (
            outcome_wire(view.terminal_outcome) if view.terminal_outcome is not None else None
        ),
        "actionable_status": view.actionable_status,
    }


def approval_wire(approval: DurableApproval, execution: DurableToolExecution) -> dict[str, Any]:
    """The approval surface shows bounded previews, never full tool arguments."""

    return {
        "approval_id": approval.approval_id,
        "tool_execution_id": approval.tool_execution_id,
        "tool_name": execution.tool_name,
        "session_id": execution.session_id,
        "task_run_id": execution.task_run_id,
        "agent_run_id": execution.agent_run_id,
        "requested_scope": approval.requested_scope,
        "preview": list(approval.preview),
        "resolution": approval.resolution.value,
        "created_at": approval.created_at.isoformat(),
        "expires_at": approval.expires_at.isoformat(),
        "resolved_at": approval.resolved_at.isoformat() if approval.resolved_at else None,
        "row_version": approval.row_version,
    }


def provider_wire(
    provider_id: str,
    config,
    *,
    credential_configured: bool,
    active_model: ModelRef | None,
) -> dict[str, Any]:
    """Provider catalog entries never carry the credential reference value."""

    return {
        "provider_id": provider_id,
        "adapter": config.adapter,
        "base_url": config.base_url,
        "credential_configured": credential_configured,
        "models": [
            {
                "model_id": model_id,
                "api_model_id": model.api_model_id,
                "capabilities": (
                    model.capabilities.model_dump(mode="json") if model.capabilities else None
                ),
                "active": bool(
                    active_model is not None
                    and active_model.provider_id == provider_id
                    and active_model.model_id == model_id
                ),
            }
            for model_id, model in sorted(config.models.items())
        ],
    }


def skill_wire(view) -> dict[str, Any]:
    return _dump(
        view,
        (
            "skill_id",
            "name",
            "scope_id",
            "source_kind",
            "availability",
            "effective_trust",
            "requested_trust",
            "conflict_status",
            "validation_errors",
            "versions",
            "binding",
        ),
    )


def agent_definition_wire(view) -> dict[str, Any]:
    return {
        "definition_id": view.definition_id,
        "origin": view.origin,
        "source_revision": view.source_revision,
        "revoked": view.revoked,
        "desired_ahead_of_published": view.desired_ahead_of_published,
        "source": view.source.model_dump(mode="json") if view.source is not None else None,
        "head": view.head.model_dump(mode="json") if view.head is not None else None,
        "published_version": (
            view.published_version.model_dump(mode="json")
            if view.published_version is not None
            else None
        ),
    }


def workflow_definition_wire(view) -> dict[str, Any]:
    return {
        "workflow_definition_id": view.workflow_definition_id,
        "origin": view.origin,
        "source_revision": view.source_revision,
        "revoked": view.revoked,
        "desired_ahead_of_published": view.desired_ahead_of_published,
        "source": view.source.model_dump(mode="json") if view.source is not None else None,
        "head": view.head.model_dump(mode="json") if view.head is not None else None,
        "published_revision": (
            view.published_revision.model_dump(mode="json")
            if view.published_revision is not None
            else None
        ),
    }


def workflow_revision_wire(view) -> dict[str, Any]:
    return {
        "revision": view.revision.model_dump(mode="json"),
        "revoked": view.revoked,
    }
