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
from morrow.core.domain import DurableAgentRun, DurableSession, DurableTaskRun, TaskOutcome
from morrow.core.execution import (
    DurableApproval,
    DurableToolExecution,
    approval_risk_level,
    session_scope_allowed,
)
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
        "agent_generation_request_count": view.agent_generation_request_count,
    }


def pre_run_summary_wire(revision) -> dict[str, Any]:
    """§14.1 pre-run cost facts: counts, models, explicit limits and writers.

    ``None`` limits are projected as ``None`` so every client renders the same
    explicit "no cap" fact instead of guessing.
    """

    budget = revision.budget
    return {
        "node_count": len(revision.nodes),
        "models": sorted({node.resolved_model_ref.model_id for node in revision.nodes}),
        "providers": sorted({node.resolved_model_ref.provider_id for node in revision.nodes}),
        "max_agent_generation_requests": budget.max_agent_generation_requests,
        "default_node_max_agent_generation_requests": (
            budget.default_node_max_agent_generation_requests
        ),
        "admission_timeout_seconds": budget.admission_timeout_seconds,
        "max_concurrency": budget.max_concurrency,
        "writer_node_ids": sorted(
            node.node_id for node in revision.nodes if node.access_mode == "write"
        ),
    }


def model_request_observation_wire(request) -> dict[str, Any]:
    return _dump(
        request,
        (
            "model_request_id",
            "workspace_id",
            "agent_run_id",
            "attempt_ordinal",
            "purpose",
            "prompt_evidence",
            "state",
            "admitted_at",
            "settled_at",
            "estimated_request_chars",
            "request_char_budget",
            "cleared_cycle_count",
            "dropped_turn_count",
            "dropped_cycle_count",
            "dropped_record_count",
            "tool_rounds",
            "tool_calls",
            "policy_schema_version",
            "estimated_context_tokens",
            "context_window_tokens",
            "reserve_tokens",
            "keep_recent_tokens",
            "accounting_basis",
            "compaction_required",
            "finish_reason",
            "error_code",
            "usage",
            "cost",
        ),
    )


def agent_run_terminal_wire(metrics) -> dict[str, Any]:
    return _dump(
        metrics,
        (
            "agent_run_id",
            "workspace_id",
            "session_id",
            "task_run_id",
            "turn_id",
            "finish_reason",
            "stop_code",
            "stop_detail",
            "model_attempts",
            "retry_count",
            "tool_rounds",
            "tool_calls",
            "max_estimated_request_chars",
            "request_char_budget",
            "cleared_cycle_count",
            "dropped_turn_count",
            "dropped_cycle_count",
            "dropped_record_count",
            "usage",
            "cost",
            "tool_terminal_counts",
            "policy_schema_version",
            "max_context_tokens",
            "last_context_tokens",
            "context_window_tokens",
            "reserve_tokens",
            "keep_recent_tokens",
            "accounting_basis",
            "compaction_count",
            "overflow_recovery_count",
            "validation_outcome",
            "finalized_at",
        ),
    )


def agent_run_retry_wire(progress) -> dict[str, Any]:
    return _dump(
        progress,
        (
            "agent_run_id",
            "workspace_id",
            "consecutive_model_retries",
            "total_retry_count",
            "summary_retry_count",
            "updated_at",
        ),
    )


def agent_run_observation_wire(observation) -> dict[str, Any]:
    return {
        **_dump(
            observation,
            (
                "agent_run_id",
                "workspace_id",
                "session_id",
                "task_run_id",
                "turn_id",
                "resume_of_agent_run_id",
                "created_at",
            ),
        ),
        "terminal_metrics": (
            agent_run_terminal_wire(observation.terminal_metrics)
            if observation.terminal_metrics is not None
            else None
        ),
        "retry_progress": (
            agent_run_retry_wire(observation.retry_progress)
            if observation.retry_progress is not None
            else None
        ),
        "requests": [model_request_observation_wire(item) for item in observation.requests],
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
        "pre_run_summary": pre_run_summary_wire(view.revision),
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


_AFFECTED_OBJECTS_MAX = 8
_AFFECTED_VALUE_MAX = 120


def _affected_objects(execution: DurableToolExecution) -> list[str]:
    """Bounded summary of the objects the operation touches.

    Sources are the credential-stripped facts persisted at preparation time:
    redacted arguments, file-mutation evidence paths and the config-mutation
    document. This projection only truncates and counts.
    """

    intent = execution.intent
    items: list[str] = []
    for key in sorted(intent.redacted_arguments):
        text = str(intent.redacted_arguments[key])
        if len(text) > _AFFECTED_VALUE_MAX:
            text = text[: _AFFECTED_VALUE_MAX - 1] + "…"
        items.append(f"{key}: {text}")
        if len(items) >= _AFFECTED_OBJECTS_MAX:
            return items
    for evidence in intent.file_evidence:
        items.append(f"{evidence.operation} {evidence.relative_path}")
        if len(items) >= _AFFECTED_OBJECTS_MAX:
            return items
    if intent.config_evidence is not None and len(items) < _AFFECTED_OBJECTS_MAX:
        items.append(f"{intent.config_evidence.operation} {intent.config_evidence.document_kind}")
    return items


def approval_wire(
    approval: DurableApproval,
    execution: DurableToolExecution,
    agent_run: DurableAgentRun | None = None,
) -> dict[str, Any]:
    """The §8.5 approval surface: requester identity, operation type, affected
    objects, risk level and bounded redacted previews — never a bare prompt."""

    effect = execution.intent.effect_class
    workflow_ref = agent_run.workflow_ref if agent_run is not None else None
    definition_ref = agent_run.snapshot.definition_ref if agent_run is not None else None
    return {
        "approval_id": approval.approval_id,
        "tool_execution_id": approval.tool_execution_id,
        "tool_name": execution.tool_name,
        "session_id": execution.session_id,
        "task_run_id": execution.task_run_id,
        "agent_run_id": execution.agent_run_id,
        "workflow_run_id": workflow_ref.workflow_run_id if workflow_ref else None,
        "node_run_id": workflow_ref.node_run_id if workflow_ref else None,
        "node_id": workflow_ref.node_id if workflow_ref else None,
        "agent_id": definition_ref.definition_id if definition_ref else None,
        "effect_class": effect.value,
        "risk_level": approval_risk_level(effect, execution.isolation).value,
        "session_scope_allowed": session_scope_allowed(effect, execution.isolation),
        "affected_objects": _affected_objects(execution),
        "requested_scope": approval.requested_scope,
        "granted_scope": approval.granted_scope,
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
        "source_hash": view.source.content_hash if view.source is not None else None,
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


def agent_definition_version_wire(view) -> dict[str, Any]:
    return {
        "version": view.version.model_dump(mode="json"),
        "revoked": view.revoked,
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


def workflow_draft_wire(view) -> dict[str, Any]:
    return {
        "draft": view.draft.model_dump(mode="json"),
        "stale_reasons": list(view.stale_reasons),
    }
