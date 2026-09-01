"""Idempotent Workflow Artifact publish/reuse over the existing ArtifactService.

Four fixed cases for one deterministic identity: absent publishes; matching
AVAILABLE reuses; matching STAGING finalizes verified final bytes or safely
republishes the already-known expected bytes; any identity/content mismatch or
corruption is reported without overwrite. No second store is created.
"""

from __future__ import annotations

from morrow.application.artifacts import ArtifactService
from morrow.core.artifacts import (
    ArtifactError,
    ArtifactErrorCode,
    ArtifactMetadata,
    ArtifactState,
)
from morrow.core.domain import canonical_json_bytes
from morrow.core.workflows.contracts import TaskContract, TextResult


def ensure_workflow_payload(
    artifacts: ArtifactService,
    payload: TaskContract | TextResult,
    *,
    session_id: str,
    task_run_id: str,
    artifact_id: str,
    producer_node_run_id: str | None = None,
    output_slot: str | None = None,
) -> ArtifactMetadata:
    prior = artifacts.get(artifact_id)
    if prior is not None and prior.state not in (ArtifactState.AVAILABLE, ArtifactState.STAGING):
        raise ArtifactError(
            ArtifactErrorCode.INTEGRITY,
            "Workflow Artifact is not intact and is never overwritten",
        )
    if prior is not None and prior.state is ArtifactState.STAGING:
        recovered = artifacts.finalize_staging(artifact_id)
        if recovered.state is ArtifactState.STAGING:
            recovered = artifacts.restore_staging_bytes(
                canonical_json_bytes(payload.model_dump(mode="json")),
                artifact_id=artifact_id,
            )
        if recovered.state is not ArtifactState.AVAILABLE:
            raise ArtifactError(
                ArtifactErrorCode.UNAVAILABLE,
                "Workflow Artifact staging could not be recovered",
            )
    return artifacts.publish_workflow_payload(
        payload,
        session_id=session_id,
        task_run_id=task_run_id,
        artifact_id=artifact_id,
        producer_node_run_id=producer_node_run_id,
        output_slot=output_slot,
    )
