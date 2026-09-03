"""Exact, OCC-bound running-edit requests for immutable Workflow revisions."""

from typing import Annotated, Literal

from pydantic import Field

from morrow.core.agent_definitions import OpaqueId, WorkspaceId
from morrow.core.models import ProtocolModel
from morrow.core.workflows.definitions import WorkflowDefinitionSource, WorkflowRevisionId
from morrow.core.workflows.runs import WorkflowRunId

WorkflowPatchId = Annotated[str, Field(pattern=r"^wpatch_[A-Za-z0-9_-]+$")]


class FutureGraphPatch(ProtocolModel):
    """A complete desired graph replacement bound to exact immutable parent facts.

    The source is intentionally exact rather than an imperative edit list. The pure
    compiler normalizes it, and Past/Future validation compares that normalized
    candidate with the frozen base revision before any write occurs.
    """

    workflow_patch_id: WorkflowPatchId
    workspace_id: WorkspaceId
    parent_run_id: WorkflowRunId
    base_workflow_revision_id: WorkflowRevisionId
    expected_parent_row_version: int = Field(ge=1, strict=True)
    source: WorkflowDefinitionSource
    requested_by: OpaqueId
    request_kind: Literal["user_exact", "approved_proposal"] = "user_exact"
