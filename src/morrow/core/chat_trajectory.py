"""Lane-internal trajectory types for the durable chat display index (P06).

This module is lane C private: nothing here extends the frozen cross-lane
contract in ``core/contracts.py``. It fixes the lane-side vocabulary that maps
durable source facts (the only owners of truth) onto frozen
``TimelineEntryIdentity`` rows so every source is stably locatable, never
duplicated, and never copied out of its owner.

Sources are read-only projections of existing durable tables; the index never
writes conversation records and never becomes a second ConversationLog writer.
"""

from __future__ import annotations

import json
from typing import Literal

from morrow.core.contracts import TimelineEntryKind

#: Source kinds of the durable facts the index projects. Identity strings only;
#: the source row itself always stays the owner of the body.
TrajectorySourceKind = Literal[
    "conversation_record",
    "chat_interaction",
    "control_receipt",
    "task_outcome",
    "workflow_run",
    "workflow_node",
    "planning_operation",
    "planning_draft_version",
]

CONVERSATION_RECORD = "conversation_record"
CHAT_INTERACTION = "chat_interaction"
CONTROL_RECEIPT = "control_receipt"
TASK_OUTCOME = "task_outcome"
WORKFLOW_RUN = "workflow_run"
WORKFLOW_NODE = "workflow_node"
PLANNING_OPERATION = "planning_operation"
PLANNING_DRAFT_VERSION = "planning_draft_version"

#: Every indexable source kind maps onto exactly one frozen entry kind.
SOURCE_ENTRY_KIND: dict[str, TimelineEntryKind] = {
    CONVERSATION_RECORD: "assistant_message",  # refined per record role
    CHAT_INTERACTION: "planning_input",
    CONTROL_RECEIPT: "control_input",
    TASK_OUTCOME: "result",
    WORKFLOW_RUN: "node_progress",
    WORKFLOW_NODE: "node_progress",
    PLANNING_OPERATION: "planning_input",
    PLANNING_DRAFT_VERSION: "plan_version",
}

#: Bounded window for change-detection re-sweeps of mutable source tables.
MUTABLE_SOURCE_WINDOW = 100

#: Bounded incremental step for one lineage session per reconcile pass.
RECORD_SCAN_STEP = 400


def conversation_entry_kind(role: str | None, finish_reason: str | None) -> TimelineEntryKind:
    """Map one conversation record onto its frozen display kind.

    Terminal records split by finish reason exactly like the live projection:
    a clean ``stop`` is a turn status, everything else is an interruption so a
    crashed or paused turn can never masquerade as a completed one.
    """

    if role == "user":
        return "user_message"
    if role == "assistant":
        return "assistant_message"
    if role == "tool":
        return "tool_activity"
    return "turn_status" if finish_reason == "stop" else "interruption"


def user_item_id(origin_session_id: str, client_message_id: str | None, record_id: str) -> str:
    """Stable item id for a user input: identity-keyed when the id is known."""

    if client_message_id:
        return f"input:{origin_session_id}:{client_message_id}"
    return f"input:{origin_session_id}:rec:{record_id}"


def reply_item_id(origin_session_id: str, position: int) -> str:
    return f"reply:{origin_session_id}:{position}"


def control_item_id(session_id: str, command_id: str) -> str:
    return f"control:{session_id}:{command_id}"


def outcome_item_id(session_id: str, task_run_id: str | None, outcome_id: str) -> str:
    return f"result:{session_id}:{task_run_id or 'task'}:{outcome_id}"


def run_item_id(workflow_run_id: str) -> str:
    return f"run:{workflow_run_id}"


def node_item_id(workflow_run_id: str, node_run_id: str) -> str:
    return f"node:{workflow_run_id}:{node_run_id}"


def planning_operation_item_id(operation_id: str) -> str:
    return f"planop:{operation_id}"


def draft_version_item_id(draft_id: str, version: int) -> str:
    return f"planver:{draft_id}:{version}"


def source_fingerprint(fields: dict) -> str:
    """Bounded change-detection fingerprint of a mutable source row.

    Only value-free lifecycle fields participate (status, disposition, row
    versions); bodies never enter the fingerprint, so re-sweeping updated
    sources bumps the display revision without copying content.
    """

    return json.dumps(fields, sort_keys=True, separators=(",", ":"))[:256]


def content_ref_for(workspace_id: str, session_id: str, record_id: str) -> str:
    """Controlled reference to a conversation record body (owner keeps it)."""

    return f"/v1/workspaces/{workspace_id}/sessions/{session_id}/content/{record_id}"


class LineageCutoff:
    """Root→leaf visibility of one requesting session over its fork lineage.

    The index is keyed by the lineage root so a fork inherits the ancestor
    prefix exactly once; ``max_position`` per ancestor session is the fork cut.
    Sources beyond their generation's cut are not inherited — a fork never
    gains control over history past the point it was cut from. Workflow leaf
    sessions of runs rooted inside the requesting lineage are separately
    authorized (root→run→node→segment→leaf, P06.4) via ``run_leaf_sessions``.
    """

    __slots__ = ("root_session_id", "cutoffs", "_depth", "run_leaf_sessions")

    def __init__(
        self,
        root_session_id: str,
        cutoffs: dict[str, int | None],
        depth: dict[str, int],
        run_leaf_sessions: frozenset[str] | None = None,
    ):
        self.root_session_id = root_session_id
        self.cutoffs = cutoffs
        self._depth = depth
        self.run_leaf_sessions = run_leaf_sessions or frozenset()

    def visible(self, source_session_id: str, source_position: int | None) -> bool:
        """Whether the requesting session may display one source fact."""

        if source_session_id in self.run_leaf_sessions:
            return True
        if source_session_id not in self.cutoffs:
            return False
        cutoff = self.cutoffs[source_session_id]
        if cutoff is None:
            return True
        if source_position is None:
            return True
        return source_position <= cutoff

    def depth(self, source_session_id: str) -> int:
        return self._depth.get(source_session_id, 0)
