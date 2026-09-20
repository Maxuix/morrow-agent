"""Durable pause acceptance and continuation acceptance (P02, spec 3.1–3.2).

This service owns the pause control cycle for workflow execution:

1. A pause intent is *durably accepted* (``requested``) before anything
   quiesces; the acceptance is idempotent per ``command_id`` and rejects a
   reused id with a different payload (replay before any version check).
2. The lifecycle ``requested -> quiescing -> suspended -> resumed`` advances
   under row-version OCC; cancel is recorded as a separate fact and never
   rewrites the lifecycle to ``resumed``.
3. A continuation (resume/correction turn) is accepted atomically: the next
   segment, the resumed pause point and the command receipt commit together —
   a rollback leaves no half relationship, and a replay returns the same
   segment.

Nothing here starts or stops a driver; the scheduler wires these facts into
execution (P03/P04). This module only ever writes lane-A-owned storage.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from morrow.application.api_context import request_digest
from morrow.core.application import ApplicationError, ApplicationErrorCode
from morrow.core.contracts import PauseIntentFact, PauseReason
from morrow.core.domain import sha256_digest
from morrow.core.execution_pause import (
    LocalTurnPauseControl,
    PauseIntentRequest,
    PauseSafetyPoint,
    TurnPauseSignal,
    WorkflowPausePoint,
)
from morrow.core.models import utc_now
from morrow.core.workflows.segments import WorkflowNodeSegment

PAUSE_OPERATION = "workflow_execution_pause"
CONTINUATION_OPERATION = "workflow_execution_continuation"

_PAUSE_TRANSITIONS: dict[str, str] = {
    "requested": "quiescing",
    "quiescing": "suspended",
}


class ExecutionPauseService:
    """Accepts pause intents and continuation turns on the durable journal."""

    def __init__(
        self,
        journal,
        *,
        workspace_id: str,
        id_source=None,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self.journal = journal
        self.workspace_id = workspace_id
        self.id_source = id_source
        self.clock = clock

    # Pause intent -------------------------------------------------------------

    def accept_pause(
        self,
        request: PauseIntentRequest,
        *,
        node_run_id: str | None = None,
        segment_id: str | None = None,
        expected_run_row_version: int | None = None,
    ) -> WorkflowPausePoint:
        """Durably accept one pause intent; replay returns the stored point.

        A legal replay (same command, same payload) wins over version checks:
        an already-accepted command replays even when the run has since
        changed. A reused id with a different payload is a conflict.
        """

        def work(txn):
            existing = txn.workflows.execution_pause.find_pause_point_by_command(
                self.workspace_id, request
            )
            digest = request_digest(
                PAUSE_OPERATION,
                {
                    "owner": request.owner,
                    "owner_id": request.owner_id,
                    "reason": request.reason,
                },
            )
            receipt = txn.get_application_command_receipt(self.workspace_id, request.command_id)
            if existing is not None or receipt is not None:
                if existing is None or receipt is None:
                    raise ApplicationError(
                        ApplicationErrorCode.NEEDS_RECOVERY,
                        "pause acceptance evidence is incomplete",
                    )
                if receipt.operation != PAUSE_OPERATION or receipt.request_digest != digest:
                    raise ApplicationError(
                        ApplicationErrorCode.CONFLICT,
                        "pause command ID was reused with a different request",
                    )
                return existing
            if request.owner == "workflow_run":
                run = txn.workflows.get_run(self.workspace_id, request.owner_id)
                if run is None:
                    raise ApplicationError(ApplicationErrorCode.NOT_FOUND, "WorkflowRun is missing")
                if expected_run_row_version is not None and (
                    run.row_version != expected_run_row_version
                ):
                    raise ApplicationError(
                        ApplicationErrorCode.STALE,
                        "Workflow run version changed; refresh and retry",
                    )
                if run.status.terminal:
                    raise ApplicationError(
                        ApplicationErrorCode.INVALID, "a terminal Workflow cannot be paused"
                    )
            generation = txn.workflows.execution_pause.next_control_generation(
                self.workspace_id, owner=request.owner, owner_id=request.owner_id
            )
            now = self.clock()
            point = WorkflowPausePoint(
                pause_point_id=self._new_id("pause"),
                workspace_id=self.workspace_id,
                fact=PauseIntentFact(
                    control_generation=generation,
                    command_id=request.command_id,
                    owner=request.owner,
                    owner_id=request.owner_id,
                    reason=request.reason,
                    lifecycle="requested",
                    requested_at=now,
                ),
                node_run_id=node_run_id,
                segment_id=segment_id,
                created_at=now,
                updated_at=now,
            )
            point = txn.workflows.execution_pause.insert_pause_point(point)
            txn.put_application_command_receipt_in_txn(
                self.workspace_id,
                self._receipt(request.command_id, PAUSE_OPERATION, digest, point),
            )
            return point

        return self.journal.transact(work)

    def advance_lifecycle(
        self,
        *,
        workflow_run_id: str,
        control_generation: int,
        target: str,
        safety: PauseSafetyPoint | None = None,
        expected_row_version: int | None = None,
    ) -> WorkflowPausePoint:
        """Move one pause cycle one step forward under OCC.

        ``requested -> quiescing -> suspended``; ``suspended`` requires the
        bounded safety point. An already-advanced cycle replays unchanged.
        """

        def work(txn):
            point = txn.workflows.execution_pause.latest_pause_point(
                self.workspace_id,
                owner="workflow_run",
                owner_id=workflow_run_id,
            )
            if point is None or point.fact.control_generation != control_generation:
                raise ApplicationError(
                    ApplicationErrorCode.NOT_FOUND,
                    "pause control generation does not exist",
                )
            if point.fact.lifecycle == "resumed":
                return point
            if point.fact.lifecycle == target:
                return point
            if expected_row_version is not None and point.row_version != expected_row_version:
                raise ApplicationError(
                    ApplicationErrorCode.STALE,
                    "pause point version changed; refresh and retry",
                )
            expected_target = _PAUSE_TRANSITIONS.get(point.fact.lifecycle)
            if expected_target is None or expected_target != target:
                raise ApplicationError(
                    ApplicationErrorCode.CONFLICT,
                    f"pause lifecycle cannot move from {point.fact.lifecycle} to {target}",
                )
            if target == "suspended" and safety is None:
                raise ApplicationError(
                    ApplicationErrorCode.INVALID,
                    "suspending a pause cycle requires the safe point facts",
                )
            now = self.clock()
            fact = point.fact.model_copy(
                update={
                    "lifecycle": target,
                    "suspended_at": now if target == "suspended" else point.fact.suspended_at,
                }
            )
            updated = point.model_copy(
                update={
                    "fact": fact,
                    "safety": safety if safety is not None else point.safety,
                    "row_version": point.row_version + 1,
                    "updated_at": now,
                }
            )
            return txn.workflows.execution_pause.save_pause_point(
                updated, expected_row_version=point.row_version
            )

        return self.journal.transact(work)

    def record_cancel(
        self,
        *,
        workflow_run_id: str,
        control_generation: int,
        cancel_command_id: str,
        reason: str | None = None,
    ) -> WorkflowPausePoint:
        """Record the separate cancel fact; the lifecycle is never rewritten."""

        def work(txn):
            point = txn.workflows.execution_pause.latest_pause_point(
                self.workspace_id,
                owner="workflow_run",
                owner_id=workflow_run_id,
            )
            if point is None or point.fact.control_generation != control_generation:
                raise ApplicationError(
                    ApplicationErrorCode.NOT_FOUND,
                    "pause control generation does not exist",
                )
            if point.cancel_command_id is not None:
                if point.cancel_command_id != cancel_command_id:
                    raise ApplicationError(
                        ApplicationErrorCode.CONFLICT,
                        "pause cycle already carries a different cancel fact",
                    )
                return point
            now = self.clock()
            updated = point.model_copy(
                update={
                    "cancel_command_id": cancel_command_id,
                    "cancel_reason": reason,
                    "cancelled_at": now,
                    "row_version": point.row_version + 1,
                    "updated_at": now,
                }
            )
            return txn.workflows.execution_pause.save_pause_point(
                updated, expected_row_version=point.row_version
            )

        return self.journal.transact(work)

    # Segments -----------------------------------------------------------------

    def record_initial_segment(
        self,
        *,
        workflow_run_id: str,
        node_run_id: str,
        leaf_session_id: str,
        leaf_task_run_id: str,
        agent_run_id: str,
        turn_id: str | None = None,
    ) -> WorkflowNodeSegment:
        """Bind a node's first execution segment idempotently (admission time)."""

        def work(txn):
            existing = txn.workflows.first_segment(self.workspace_id, node_run_id)
            if existing is not None:
                same = (
                    existing.workflow_run_id == workflow_run_id
                    and existing.agent_run_id == agent_run_id
                    and existing.leaf_session_id == leaf_session_id
                    and existing.leaf_task_run_id == leaf_task_run_id
                )
                if not same:
                    raise ApplicationError(
                        ApplicationErrorCode.CONFLICT,
                        "node already owns a different first execution segment",
                    )
                return existing
            now = self.clock()
            segment = WorkflowNodeSegment(
                segment_id=self._new_id("seg"),
                workspace_id=self.workspace_id,
                workflow_run_id=workflow_run_id,
                node_run_id=node_run_id,
                ordinal=1,
                leaf_session_id=leaf_session_id,
                leaf_task_run_id=leaf_task_run_id,
                agent_run_id=agent_run_id,
                turn_id=turn_id,
                status="active",
                created_at=now,
                updated_at=now,
            )
            return txn.workflows.append_segment(segment)

        return self.journal.transact(work)

    def accept_continuation(
        self,
        *,
        command_id: str,
        workflow_run_id: str,
        node_run_id: str,
        leaf_session_id: str,
        leaf_task_run_id: str,
        agent_run_id: str,
        turn_id: str | None = None,
        pause_reason: PauseReason = "user_interrupt",
        expected_run_row_version: int | None = None,
        continuation_input: str | None = None,
    ) -> tuple[WorkflowNodeSegment, WorkflowPausePoint | None, object, bool]:
        """Accept a continuation turn atomically: segment + pause fact + receipt."""

        def work(txn):
            return accept_continuation_in_txn(
                txn,
                workspace_id=self.workspace_id,
                command_id=command_id,
                workflow_run_id=workflow_run_id,
                node_run_id=node_run_id,
                leaf_session_id=leaf_session_id,
                leaf_task_run_id=leaf_task_run_id,
                agent_run_id=agent_run_id,
                turn_id=turn_id,
                pause_reason=pause_reason,
                expected_run_row_version=expected_run_row_version,
                continuation_input=continuation_input,
                clock=self.clock,
                new_segment_id=lambda: self._new_id("seg"),
            )

        return self.journal.transact(work)

    def store_continuation_input(
        self,
        workflow_run_id: str,
        *,
        command_id: str,
        text: str | None = None,
    ) -> WorkflowPausePoint | None:
        """Durably bind the accepted continuation command to the pause cycle.

        Called when the resume/continuation command is accepted (text commands
        keep the raw text; button resumes pass ``None`` and the driver uses the
        deterministic continue wording). The next continuation turn consumes
        it exactly once by marking the cycle ``resumed``.
        """

        def work(txn):
            return store_continuation_input_in_txn(
                txn,
                workspace_id=self.workspace_id,
                workflow_run_id=workflow_run_id,
                command_id=command_id,
                text=text,
                clock=self.clock,
            )

        return self.journal.transact(work)

    # Chat turn owner (P03/P04 cross-lane ask from lane D) ----------------------

    def accept_chat_pause(self, session_id: str, *, command_id: str) -> WorkflowPausePoint:
        """Durably accept one chat-turn pause (owner=``chat_turn``).

        Same replay/conflict contract as the workflow pause: a legal replay
        returns the stored cycle, a reused id with a different payload is a
        conflict. The caller wakes the running chat drive through
        ``ChatTurnPauseControl.interrupt`` after this commits.
        """
        return self.accept_pause(
            PauseIntentRequest(
                command_id=command_id,
                owner="chat_turn",
                owner_id=session_id,
                reason="user_interrupt",
            )
        )

    def chat_pause_point(self, session_id: str) -> WorkflowPausePoint | None:
        return self.journal.workflows.execution_pause.latest_pause_point(
            self.workspace_id, owner="chat_turn", owner_id=session_id
        )

    def settle_chat_pause(self, session_id: str) -> WorkflowPausePoint | None:
        """Advance the open chat pause cycle to ``suspended`` (bounded note).

        Called by the chat drive wrapper once the interrupted turn has
        settled; a cycle already past ``suspended`` replays unchanged.
        """
        point = self.chat_pause_point(session_id)
        if point is None or point.fact.lifecycle not in ("requested", "quiescing"):
            return point
        now = self.clock()
        fact = point.fact.model_copy(update={"lifecycle": "suspended", "suspended_at": now})
        return self.journal.workflows.execution_pause.save_pause_point(
            point.model_copy(
                update={
                    "fact": fact,
                    "safety": PauseSafetyPoint(
                        note="chat turn suspended after the typed interrupted terminal"
                    ),
                    "row_version": point.row_version + 1,
                    "updated_at": now,
                }
            ),
            expected_row_version=point.row_version,
        )

    def resume_chat_pause(self, session_id: str, *, command_id: str | None = None):
        """Mark the chat pause cycle ``resumed``; idempotent per command."""
        point = self.chat_pause_point(session_id)
        if point is None or point.fact.lifecycle == "resumed":
            return point
        if point.cancel_command_id is not None:
            raise ApplicationError(
                ApplicationErrorCode.INVALID, "a cancelled pause cycle cannot be resumed"
            )
        now = self.clock()
        fact = point.fact.model_copy(update={"lifecycle": "resumed", "resumed_at": now})
        return self.journal.workflows.execution_pause.save_pause_point(
            point.model_copy(
                update={
                    "fact": fact,
                    "continuation_command_id": command_id or point.continuation_command_id,
                    "row_version": point.row_version + 1,
                    "updated_at": now,
                }
            ),
            expected_row_version=point.row_version,
        )

    def _receipt(self, command_id, operation, digest, target):
        from morrow.core.application import ApplicationCommandReceipt

        now = self.clock()
        return ApplicationCommandReceipt(
            command_id=command_id,
            workspace_id=self.workspace_id,
            operation=operation,
            request_digest=digest,
            result_kind="workflow_node_segment"
            if operation == CONTINUATION_OPERATION
            else "workflow_pause_point",
            result_id=(
                target.segment_id if operation == CONTINUATION_OPERATION else target.pause_point_id
            ),
            created_at=now,
        )

    def _new_id(self, prefix: str) -> str:
        if self.id_source is not None:
            return self.id_source.new_id(prefix)
        from morrow.runtime.ids import RandomIdSource

        return RandomIdSource().new_id(prefix)


def accept_continuation_in_txn(
    txn,
    *,
    workspace_id: str,
    command_id: str,
    workflow_run_id: str,
    node_run_id: str,
    leaf_session_id: str,
    leaf_task_run_id: str,
    agent_run_id: str,
    turn_id: str | None = None,
    pause_reason: PauseReason = "user_interrupt",
    expected_run_row_version: int | None = None,
    continuation_input: str | None = None,
    clock: Callable[[], datetime] = utc_now,
    new_segment_id: Callable[[], str] | None = None,
) -> tuple[WorkflowNodeSegment, WorkflowPausePoint | None, object, bool]:
    """Bind the next execution segment inside the caller's transaction.

    Runs inside the continuation Turn's admission transaction when invoked
    through the workflow leaf hooks, so the segment, the resumed pause cycle
    and the command receipt commit atomically with the new Turn/AgentRun.
    """
    digest = request_digest(
        CONTINUATION_OPERATION,
        {
            "workflow_run_id": workflow_run_id,
            "node_run_id": node_run_id,
            "leaf_session_id": leaf_session_id,
            "leaf_task_run_id": leaf_task_run_id,
            "agent_run_id": agent_run_id,
            "continuation_input": continuation_input or "",
        },
    )
    # The continuation receipt derives its own command id: the resume command
    # already owns its receipt, and identities are never reused (contract §4).
    # A parallel frontier consumes one resume command once per node, so later
    # siblings receive a deterministic node-scoped suffix while preserving the
    # historical ``<command>_seg`` id for the first sibling.
    receipt_id = command_id + "_seg"
    point = txn.workflows.execution_pause.latest_pause_point(
        workspace_id, owner="workflow_run", owner_id=workflow_run_id
    )
    receipt = txn.get_application_command_receipt(workspace_id, receipt_id)
    if (
        point is not None
        and point.continuation_segment_id is not None
        and point.node_run_id != node_run_id
        and (
            receipt is None
            or receipt.operation != CONTINUATION_OPERATION
            or receipt.request_digest != digest
        )
    ):
        receipt_id = f"{command_id[:96]}_seg_{sha256_digest(node_run_id.encode('utf-8'))[:16]}"
        receipt = txn.get_application_command_receipt(workspace_id, receipt_id)
    if receipt is not None:
        if receipt.operation != CONTINUATION_OPERATION or receipt.request_digest != digest:
            raise ApplicationError(
                ApplicationErrorCode.CONFLICT,
                "continuation command ID was reused with a different request",
            )
        segment = txn.workflows.get_segment(workspace_id, receipt.result_id or "")
        if segment is None:
            raise ApplicationError(
                ApplicationErrorCode.NEEDS_RECOVERY, "continuation result is missing"
            )
        return segment, point, receipt, True
    run = txn.workflows.get_run(workspace_id, workflow_run_id)
    if run is None:
        raise ApplicationError(ApplicationErrorCode.NOT_FOUND, "WorkflowRun is missing")
    if run.status.terminal:
        raise ApplicationError(
            ApplicationErrorCode.INVALID, "a terminal Workflow cannot accept a continuation"
        )
    if expected_run_row_version is not None and run.row_version != expected_run_row_version:
        raise ApplicationError(
            ApplicationErrorCode.STALE, "Workflow run version changed; refresh and retry"
        )
    node = txn.workflows.get_node(workspace_id, node_run_id)
    if node is None or node.workflow_run_id != workflow_run_id:
        raise ApplicationError(ApplicationErrorCode.NOT_FOUND, "NodeRun is missing")
    ownership = txn.workflows.get_leaf_ownership(workspace_id, node_run_id)
    if ownership != (leaf_session_id, leaf_task_run_id):
        raise ApplicationError(
            ApplicationErrorCode.INVALID,
            "continuation leaf references must match the node's leaf ownership",
        )
    now = clock()
    previous = txn.workflows.current_segment(node_run_id)
    if previous is not None:
        txn.workflows.close_segment(
            workspace_id,
            previous.segment_id,
            status="interrupted",
            pause_reason=pause_reason,
            expected_row_version=previous.row_version,
        )
    elif txn.workflows.segments_for_node(workspace_id, node_run_id):
        last = txn.workflows.segments_for_node(workspace_id, node_run_id)[-1]
        if last.status == "completed":
            raise ApplicationError(
                ApplicationErrorCode.INVALID,
                "a completed node does not accept continuations",
            )
        previous = last
    ordinal = previous.ordinal + 1 if previous is not None else 1
    if new_segment_id is None:
        from morrow.runtime.ids import RandomIdSource

        new_segment_id = RandomIdSource().new_id
    segment = WorkflowNodeSegment(
        segment_id=new_segment_id(),
        workspace_id=workspace_id,
        workflow_run_id=workflow_run_id,
        node_run_id=node_run_id,
        ordinal=ordinal,
        leaf_session_id=leaf_session_id,
        leaf_task_run_id=leaf_task_run_id,
        agent_run_id=agent_run_id,
        turn_id=turn_id,
        status="active",
        previous_segment_id=previous.segment_id if previous is not None else None,
        accepted_command_id=command_id,
        created_at=now,
        updated_at=now,
    )
    segment = txn.workflows.append_segment(segment)
    point = txn.workflows.execution_pause.latest_pause_point(
        workspace_id, owner="workflow_run", owner_id=workflow_run_id
    )
    if point is not None and point.fact.lifecycle != "resumed":
        if point.cancel_command_id is not None:
            raise ApplicationError(
                ApplicationErrorCode.INVALID, "a cancelled pause cycle cannot be resumed"
            )
        fact = point.fact.model_copy(update={"lifecycle": "resumed", "resumed_at": now})
        point = txn.workflows.execution_pause.save_pause_point(
            point.model_copy(
                update={
                    "fact": fact,
                    "continuation_segment_id": segment.segment_id,
                    "continuation_command_id": command_id,
                    "continuation_input": continuation_input,
                    "row_version": point.row_version + 1,
                    "updated_at": now,
                }
            ),
            expected_row_version=point.row_version,
        )
    receipt_model = _continuation_receipt(
        txn, workspace_id, receipt_id, command_id, digest, segment, now
    )
    txn.put_application_command_receipt_in_txn(workspace_id, receipt_model)
    return segment, point, receipt_model, False


def _continuation_receipt(txn, workspace_id, receipt_id, command_id, digest, segment, now):
    from morrow.core.application import ApplicationCommandReceipt

    return ApplicationCommandReceipt(
        command_id=receipt_id,
        workspace_id=workspace_id,
        operation=CONTINUATION_OPERATION,
        request_digest=digest,
        result_kind="workflow_node_segment",
        result_id=segment.segment_id,
        created_at=now,
    )


def store_continuation_input_in_txn(
    txn,
    *,
    workspace_id: str,
    workflow_run_id: str,
    command_id: str,
    text: str | None,
    clock: Callable[[], datetime] = utc_now,
) -> WorkflowPausePoint | None:
    """Bind the accepted continuation command text onto the suspended cycle."""
    point = txn.workflows.execution_pause.latest_pause_point(
        workspace_id, owner="workflow_run", owner_id=workflow_run_id
    )
    if point is None or point.fact.lifecycle != "suspended":
        return point
    if point.continuation_command_id is not None:
        if point.continuation_command_id != command_id:
            raise ApplicationError(
                ApplicationErrorCode.CONFLICT,
                "pause cycle already carries a different continuation command",
            )
        return point
    now = clock()
    return txn.workflows.execution_pause.save_pause_point(
        point.model_copy(
            update={
                "continuation_command_id": command_id,
                "continuation_input": text,
                "row_version": point.row_version + 1,
                "updated_at": now,
            }
        ),
        expected_row_version=point.row_version,
    )


def pending_continuation(
    journal, workspace_id: str, workflow_run_id: str, *, node_run_id: str | None = None
):
    """Return the pending continuation facts for one run, or None.

    Pending means: the latest pause cycle carries an accepted continuation
    command and the selected node still ends in its interrupted segment. A
    parallel frontier may consume one command once per sibling; the first
    sibling advances the shared lifecycle to ``resumed`` while later siblings
    retain their own segment-local continuation.
    """
    point = journal.workflows.execution_pause.latest_pause_point(
        workspace_id, owner="workflow_run", owner_id=workflow_run_id
    )
    if point is None or point.fact.lifecycle not in ("suspended", "resumed"):
        return None
    if point.continuation_command_id is None:
        return None
    if node_run_id is None:
        if point.fact.lifecycle != "suspended" or point.continuation_segment_id is not None:
            return None
    else:
        segments = journal.workflows.segments_for_node(workspace_id, node_run_id)
        if not segments or segments[-1].status != "interrupted":
            return None
    return point


class NodePauseControl:
    """Durable ``TurnPauseControl`` for workflow leaf drives (P04).

    ``pending_signal`` reads the run's accepted pause cycle from the journal
    (authority); the embedded ``LocalTurnPauseControl`` only carries the
    in-process wake events. The pause acceptance path calls ``wake_run`` so a
    driver waiting on the first model token wakes immediately.
    """

    def __init__(self, journal, workspace_id: str) -> None:
        self.journal = journal
        self.workspace_id = workspace_id
        self._waker = LocalTurnPauseControl()

    def pending_signal(self, session_id: str) -> TurnPauseSignal | None:
        runs = self.journal.workflows.active_runs_for_leaf_session(self.workspace_id, session_id)
        if not runs:
            return None
        run = runs[0]
        if not run.pause_requested:
            return None
        point = self.journal.workflows.execution_pause.latest_pause_point(
            self.workspace_id, owner="workflow_run", owner_id=run.workflow_run_id
        )
        if point is not None:
            if point.fact.lifecycle not in ("requested", "quiescing"):
                # The pause point is a run-level singleton: a sibling node may
                # already have carried the cycle to suspended while this
                # node's turn is still settling. While the run keeps draining,
                # the same user interrupt still owns this leaf's turn, so the
                # signal stays delivered; a resumed or settled run never
                # re-interrupts (BUG-GUI-002 multi-node barrier).
                if not (
                    point.fact.lifecycle == "suspended"
                    and point.fact.reason != "node_boundary"
                    and run.status.value == "draining"
                ):
                    return None
            elif point.fact.reason == "node_boundary":
                # An internal node_boundary pause drains without interrupting
                # the running turn (spec 3.2). Provider/process interruptions
                # must wake every sibling so each one can park its segment.
                return None
            return TurnPauseSignal(
                control_generation=point.fact.control_generation,
                command_id=point.fact.command_id,
                reason=point.fact.reason,
            )
        # User pause accepted through a path that carries no command id (the
        # run pause button): the run fact is the durable authority. A draining
        # run with live leaves interrupts them; internal boundaries always
        # create a pause-point row, so absence means a user pause.
        if run.status.value != "draining":
            return None
        return TurnPauseSignal(
            control_generation=run.row_version,
            command_id=None,
            reason="user_interrupt",
        )

    def clear(self, session_id: str) -> None:
        """Consume the delivered signal after the turn ended interrupted."""
        self._waker.clear(session_id)

    async def wait_signal(self, session_id: str) -> None:
        """Wait until the durable authority carries a pause for this session.

        A wake hint alone is never trusted: after each hint the authority is
        re-read and the hint is consumed, so a stale event can never spin.
        """
        while True:
            if self.pending_signal(session_id) is not None:
                return
            await self._waker.wait_signal(session_id)
            self._waker.clear(session_id)

    def wake_run(self, workflow_run_id: str) -> int:
        """Wake every live leaf session of one run after a durable pause."""
        point = self.journal.workflows.execution_pause.latest_pause_point(
            self.workspace_id, owner="workflow_run", owner_id=workflow_run_id
        )
        if point is None:
            return 0
        signal = TurnPauseSignal(
            control_generation=point.fact.control_generation,
            command_id=point.fact.command_id,
            reason=point.fact.reason,
        )
        woken = 0
        for node in self.journal.workflows.list_nodes(self.workspace_id, workflow_run_id):
            if node.status.value != "running" or node.conversation_session_id is None:
                continue
            self._waker.interrupt(node.conversation_session_id, signal)
            woken += 1
        return woken


class ChatTurnPauseControl:
    """Durable ``TurnPauseControl`` for a plain-chat AgentLoop (P03 seam).

    Keyed by session id; the pause authority is the ``chat_turn`` pause cycle
    in the shared pause journal. The coordinator wires this into the chat
    AgentLoop construction and calls ``interrupt`` after the pause endpoint
    accepts a ``GuiPauseRequest``.
    """

    def __init__(self, journal, workspace_id: str) -> None:
        self.journal = journal
        self.workspace_id = workspace_id
        self._waker = LocalTurnPauseControl()

    def pending_signal(self, session_id: str) -> TurnPauseSignal | None:
        point = self.journal.workflows.execution_pause.latest_pause_point(
            self.workspace_id, owner="chat_turn", owner_id=session_id
        )
        if point is None or point.fact.lifecycle not in ("requested", "quiescing"):
            return None
        if point.fact.reason != "user_interrupt":
            return None
        return TurnPauseSignal(
            control_generation=point.fact.control_generation,
            command_id=point.fact.command_id,
            reason=point.fact.reason,
        )

    async def wait_signal(self, session_id: str) -> None:
        while True:
            if self.pending_signal(session_id) is not None:
                return
            await self._waker.wait_signal(session_id)
            self._waker.clear(session_id)

    def clear(self, session_id: str) -> None:
        self._waker.clear(session_id)

    def interrupt(self, session_id: str, signal: TurnPauseSignal) -> None:
        """Wake a running chat drive after the pause acceptance committed."""
        self._waker.interrupt(session_id, signal)
