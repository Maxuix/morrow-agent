"""Append-only node execution segments (P02, spec 3.2.1).

A segment is one durable execution epoch of a node: the frozen cross-lane
identity lives in ``morrow.core.contracts.ExecutionSegmentIdentity``; this
module adds the workspace scoping and audit bookkeeping that the workflow
repository persists. ``NodeRun`` keeps its historical ``agent_run_id`` binding
readable; it always maps to the first segment, and the current executor is only
ever resolved through ``SegmentDirectoryPort.current_segment``.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import Field, model_validator

from morrow.core.contracts import ExecutionSegmentIdentity

__all__ = ["WorkflowNodeSegment"]


class WorkflowNodeSegment(ExecutionSegmentIdentity):
    """One stored segment row: workspace scope plus audit timestamps."""

    workspace_id: str = Field(min_length=1, max_length=128)
    row_version: int = Field(default=1, ge=1, strict=True)
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def segment_facts(self) -> WorkflowNodeSegment:
        if self.previous_segment_id == self.segment_id:
            raise ValueError("a segment cannot reference itself as its predecessor")
        if self.previous_segment_id is not None and self.ordinal < 2:
            raise ValueError("only the first segment of a node has no predecessor")
        if self.previous_segment_id is None and self.ordinal != 1:
            raise ValueError("a segment without a predecessor is always the first")
        if self.pause_reason is not None and self.status != "interrupted":
            raise ValueError("a pause reason belongs to an interrupted segment")
        return self
