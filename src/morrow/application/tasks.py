"""Foreground TaskRun commands and deterministic TaskOutcome projection.

The service owns task transitions and command idempotency.  It never writes
ConversationLog; turns and messages remain the responsibility of
``SessionPersistence`` and ``ConversationLog``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from morrow.core.domain import (
    COMMAND_ID_PREFIX,
    TASK_OUTCOME_ID_PREFIX,
    TASK_RUN_ID_PREFIX,
    TASK_TRANSITION_ID_PREFIX,
    ArtifactReference,
    DurableTaskOutcome,
    DurableTaskRun,
    DurableTaskRunTransition,
    TaskCommandDisposition,
    TaskCommandReceipt,
    TaskOutcomeEvidenceKind,
    TaskOutcomeEvidenceRef,
    TaskOutcomeTrigger,
    TaskRunStatus,
    canonical_json_bytes,
    sha256_digest,
    validate_task_transition,
)
from morrow.core.execution import ToolExecutionDisposition, ToolExecutionState
from morrow.core.journal import TaskJournalPort
from morrow.core.ports import IdSource

TaskCommandKind = Literal["accepted", "replay", "conflict"]


class TaskCommandError(RuntimeError):
    """Stable application error for an invalid or stale foreground command."""


class TaskCommandConflict(TaskCommandError):
    """The command ID was already used with a different request."""


@dataclass(frozen=True)
class TaskCommandResult:
    kind: TaskCommandKind
    task: DurableTaskRun | None
    outcome: DurableTaskOutcome | None = None
    receipt: TaskCommandReceipt | None = None


def task_command_digest(operation: str, payload: dict[str, object]) -> str:
    return sha256_digest(canonical_json_bytes({"operation": operation, **payload}))


class TaskOutcomeAssembler:
    """Build only bounded, deterministic facts from already durable records."""

    def __init__(self, journal: TaskJournalPort, *, workspace_id: str, id_source: IdSource):
        self.journal = journal
        self.workspace_id = workspace_id
        self.id_source = id_source

    def build(
        self,
        task: DurableTaskRun,
        *,
        trigger: TaskOutcomeTrigger,
        summary: str | None = None,
        feedback: tuple[str, ...] = (),
        artifact_refs: tuple[ArtifactReference, ...] = (),
    ) -> DurableTaskOutcome:
        turns = self.journal.list_task_turns(self.workspace_id, task.task_run_id)
        executions = self.journal.list_task_executions(self.workspace_id, task.task_run_id)
        transitions = self.journal.list_task_transitions(self.workspace_id, task.task_run_id)
        outcomes = self.journal.list_task_outcomes(self.workspace_id, task.task_run_id)

        changed_paths = sorted(
            {
                evidence.relative_path
                for execution in executions
                if execution.facts is not None
                for evidence in execution.facts.files
            }
        )
        validation_facts = sorted(
            {
                f"{execution.tool_name}:{execution.disposition.value}"
                for execution in executions
                if execution.state
                in {ToolExecutionState.HANDLER_COMPLETED, ToolExecutionState.CLOSED}
            }
        )
        side_effects = sorted(
            {
                f"{execution.tool_name}:{execution.intent.effect_class.value}"
                for execution in executions
            }
        )
        unresolved_items = tuple(
            sorted(
                f"{execution.tool_name}:{execution.disposition.value}"
                for execution in executions
                if execution.state is not ToolExecutionState.CLOSED
                or execution.disposition
                in {
                    ToolExecutionDisposition.FAILED,
                    ToolExecutionDisposition.INTERRUPTED,
                    ToolExecutionDisposition.UNKNOWN,
                }
            )
        )
        completion_basis = (
            f"trigger={trigger.value}",
            f"task_status={task.status.value}",
            f"turn_count={len(turns)}",
            f"tool_execution_count={len(executions)}",
            f"prior_outcome_count={len(outcomes)}",
        )
        evidence_refs = tuple(
            [
                *(
                    TaskOutcomeEvidenceRef(
                        kind=TaskOutcomeEvidenceKind.TURN,
                        reference_id=turn.turn_id,
                        role="user_goal" if index == 0 else "task_turn",
                    )
                    for index, turn in enumerate(turns)
                ),
                *(
                    TaskOutcomeEvidenceRef(
                        kind=TaskOutcomeEvidenceKind.TOOL_EXECUTION,
                        reference_id=execution.tool_execution_id,
                        role="tool_execution",
                    )
                    for execution in executions
                ),
                *(
                    TaskOutcomeEvidenceRef(
                        kind=TaskOutcomeEvidenceKind.TASK_TRANSITION,
                        reference_id=transition.transition_id,
                        role="task_transition",
                    )
                    for transition in transitions
                ),
            ]
        )
        linked_artifacts = tuple(
            sorted(
                {
                    (reference.artifact_id, reference.role): reference
                    for execution in executions
                    for reference in execution.artifact_refs
                }.values(),
                key=lambda reference: (reference.artifact_id, reference.role),
            )
        )
        all_artifact_refs = tuple(
            sorted(
                {
                    (reference.artifact_id, reference.role): reference
                    for reference in (*linked_artifacts, *artifact_refs)
                }.values(),
                key=lambda reference: (reference.artifact_id, reference.role),
            )
        )
        return DurableTaskOutcome(
            outcome_id=self.id_source.new_id(TASK_OUTCOME_ID_PREFIX),
            workspace_id=self.workspace_id,
            session_id=task.session_id,
            task_run_id=task.task_run_id,
            version=len(outcomes) + 1,
            trigger=trigger,
            task_status=task.status,
            summary=summary or f"TaskRun {task.task_run_id} is {task.status.value}.",
            goal_reference=(
                TaskOutcomeEvidenceRef(
                    kind=TaskOutcomeEvidenceKind.TURN,
                    reference_id=turns[0].turn_id,
                    role="user_goal",
                )
                if turns
                else None
            ),
            changed_paths=tuple(changed_paths),
            validation_facts=tuple(validation_facts),
            side_effects=tuple(side_effects),
            unresolved_items=unresolved_items,
            completion_basis=completion_basis,
            feedback=feedback,
            evidence_refs=evidence_refs,
            artifact_refs=all_artifact_refs,
        )


class TaskService:
    """Application boundary for explicit TaskRun commands."""

    def __init__(
        self,
        *,
        journal: TaskJournalPort,
        workspace_id: str,
        id_source: IdSource,
    ) -> None:
        self.journal = journal
        self.workspace_id = workspace_id
        self.id_source = id_source
        self.outcomes = TaskOutcomeAssembler(
            journal, workspace_id=workspace_id, id_source=id_source
        )

    def get(self, task_run_id: str) -> DurableTaskRun | None:
        return self.journal.get_task_run(self.workspace_id, task_run_id)

    def list(self, session_id: str) -> tuple[DurableTaskRun, ...]:
        return self.journal.list_task_runs(self.workspace_id, session_id)

    def continue_after_answer(
        self,
        task: DurableTaskRun,
        *,
        turn_id: str | None = None,
        reason: str = "ordinary_follow_up",
    ) -> DurableTaskRun:
        if task.status is not TaskRunStatus.READY_FOR_ACCEPTANCE:
            if task.status is TaskRunStatus.OPEN:
                return task
            raise TaskCommandError(
                f"TaskRun {task.task_run_id} cannot accept ordinary follow-up from {task.status.value}"
            )
        return self._transition(
            task,
            TaskRunStatus.OPEN,
            reason=reason,
            turn_id=turn_id,
            command_id=None,
        )

    def new_task(
        self,
        session_id: str,
        *,
        command_id: str | None = None,
        expected_row_version: int | None = None,
    ) -> TaskCommandResult:
        operation = "task_new"
        digest = task_command_digest(
            operation,
            {"session_id": session_id, "expected_row_version": expected_row_version},
        )
        replay = self._replay(command_id, digest)
        if replay is not None:
            return replay

        def work(txn: TaskJournalPort) -> TaskCommandResult:
            replay = self._replay_in_txn(txn, command_id, digest)
            if replay is not None:
                return replay
            session = txn.get_session(self.workspace_id, session_id)
            if session is None:
                raise TaskCommandError("session is missing")
            old_task = (
                txn.get_task_run(self.workspace_id, session.current_task_run_id)
                if session.current_task_run_id is not None
                else None
            )
            old_outcome = None
            if (
                old_task is not None
                and expected_row_version is not None
                and old_task.row_version != expected_row_version
            ):
                raise TaskCommandError("TaskRun row version is stale")
            if old_task is None and expected_row_version is not None:
                raise TaskCommandError("TaskRun row version is stale")
            if old_task is not None and old_task.status in {
                TaskRunStatus.OPEN,
                TaskRunStatus.READY_FOR_ACCEPTANCE,
            }:
                old_task = self._transition_in_txn(
                    txn,
                    old_task,
                    TaskRunStatus.ABANDONED,
                    reason="explicit_task_new",
                    command_id=command_id,
                )
                old_outcome = self._outcome_in_txn(
                    txn,
                    old_task,
                    trigger=TaskOutcomeTrigger.TERMINAL_CLOSE,
                )
            new = txn.create_task_run(
                self.workspace_id,
                DurableTaskRun(
                    task_run_id=self.id_source.new_id(TASK_RUN_ID_PREFIX),
                    session_id=session_id,
                    workspace_id=self.workspace_id,
                ),
                make_current=True,
            )
            receipt = self._store_receipt(
                txn,
                command_id=command_id,
                session_id=session_id,
                task_run_id=new.task_run_id,
                operation=operation,
                request_digest=digest,
                result_task_run_id=new.task_run_id,
                outcome_id=old_outcome.outcome_id if old_outcome is not None else None,
                task=new,
            )
            return TaskCommandResult("accepted", new, old_outcome, receipt)

        return self.journal.transact(work)

    def accept(
        self,
        task_run_id: str,
        *,
        command_id: str | None = None,
        expected_row_version: int | None = None,
        summary: str | None = None,
        feedback: tuple[str, ...] = (),
    ) -> TaskCommandResult:
        return self._close(
            task_run_id,
            target=TaskRunStatus.ACCEPTED,
            trigger=TaskOutcomeTrigger.ACCEPTANCE,
            operation="task_accept",
            command_id=command_id,
            expected_row_version=expected_row_version,
            summary=summary,
            feedback=feedback,
        )

    def cancel(
        self,
        task_run_id: str,
        *,
        command_id: str | None = None,
        expected_row_version: int | None = None,
        summary: str | None = None,
    ) -> TaskCommandResult:
        return self._close(
            task_run_id,
            target=TaskRunStatus.CANCELLED,
            trigger=TaskOutcomeTrigger.TERMINAL_CLOSE,
            operation="task_cancel",
            command_id=command_id,
            expected_row_version=expected_row_version,
            summary=summary,
        )

    def abandon(
        self,
        task_run_id: str,
        *,
        command_id: str | None = None,
        expected_row_version: int | None = None,
        summary: str | None = None,
    ) -> TaskCommandResult:
        return self._close(
            task_run_id,
            target=TaskRunStatus.ABANDONED,
            trigger=TaskOutcomeTrigger.TERMINAL_CLOSE,
            operation="task_abandon",
            command_id=command_id,
            expected_row_version=expected_row_version,
            summary=summary,
        )

    def fail(
        self,
        task_run_id: str,
        *,
        command_id: str | None = None,
        expected_row_version: int | None = None,
        summary: str | None = None,
    ) -> TaskCommandResult:
        return self._close(
            task_run_id,
            target=TaskRunStatus.FAILED,
            trigger=TaskOutcomeTrigger.TERMINAL_CLOSE,
            operation="task_fail",
            command_id=command_id,
            expected_row_version=expected_row_version,
            summary=summary,
        )

    def resume(
        self,
        task_run_id: str,
        *,
        command_id: str | None = None,
        expected_row_version: int | None = None,
    ) -> TaskCommandResult:
        operation = "task_resume"
        digest = task_command_digest(
            operation,
            {"task_run_id": task_run_id, "expected_row_version": expected_row_version},
        )
        replay = self._replay(command_id, digest)
        if replay is not None:
            return replay

        def work(txn: TaskJournalPort) -> TaskCommandResult:
            replay = self._replay_in_txn(txn, command_id, digest)
            if replay is not None:
                return replay
            task = self._load(txn, task_run_id)
            self._check_version(task, expected_row_version)
            updated = self._transition_in_txn(
                txn,
                task,
                TaskRunStatus.OPEN,
                reason="explicit_task_resume",
                command_id=command_id,
            )
            receipt = self._store_receipt(
                txn,
                command_id=command_id,
                session_id=updated.session_id,
                task_run_id=updated.task_run_id,
                operation=operation,
                request_digest=digest,
                result_task_run_id=updated.task_run_id,
                task=updated,
            )
            return TaskCommandResult("accepted", updated, receipt=receipt)

        return self.journal.transact(work)

    def snapshot(
        self,
        task_run_id: str,
        *,
        command_id: str | None = None,
        expected_row_version: int | None = None,
        summary: str | None = None,
        feedback: tuple[str, ...] = (),
    ) -> TaskCommandResult:
        operation = "task_snapshot"
        digest = task_command_digest(
            operation,
            {
                "task_run_id": task_run_id,
                "expected_row_version": expected_row_version,
                "summary": summary or "",
                "feedback": feedback,
            },
        )
        replay = self._replay(command_id, digest)
        if replay is not None:
            return replay

        def work(txn: TaskJournalPort) -> TaskCommandResult:
            replay = self._replay_in_txn(txn, command_id, digest)
            if replay is not None:
                return replay
            task = self._load(txn, task_run_id)
            self._check_version(task, expected_row_version)
            outcome = self._outcome_in_txn(
                txn,
                task,
                trigger=TaskOutcomeTrigger.SNAPSHOT,
                summary=summary,
                feedback=feedback,
            )
            receipt = self._store_receipt(
                txn,
                command_id=command_id,
                session_id=task.session_id,
                task_run_id=task.task_run_id,
                operation=operation,
                request_digest=digest,
                result_task_run_id=task.task_run_id,
                outcome_id=outcome.outcome_id,
                task=task,
            )
            return TaskCommandResult("accepted", task, outcome, receipt)

        return self.journal.transact(work)

    def _close(
        self,
        task_run_id: str,
        *,
        target: TaskRunStatus,
        trigger: TaskOutcomeTrigger,
        operation: str,
        command_id: str | None,
        expected_row_version: int | None,
        summary: str | None,
        feedback: tuple[str, ...] = (),
    ) -> TaskCommandResult:
        digest = task_command_digest(
            operation,
            {
                "task_run_id": task_run_id,
                "expected_row_version": expected_row_version,
                "summary": summary or "",
                "feedback": feedback,
            },
        )
        replay = self._replay(command_id, digest)
        if replay is not None:
            return replay

        def work(txn: TaskJournalPort) -> TaskCommandResult:
            replay = self._replay_in_txn(txn, command_id, digest)
            if replay is not None:
                return replay
            task = self._load(txn, task_run_id)
            self._check_version(task, expected_row_version)
            updated = self._transition_in_txn(
                txn,
                task,
                target,
                reason=f"explicit_{operation}",
                command_id=command_id,
            )
            outcome = self._outcome_in_txn(
                txn,
                updated,
                trigger=trigger,
                summary=summary,
                feedback=feedback,
            )
            receipt = self._store_receipt(
                txn,
                command_id=command_id,
                session_id=updated.session_id,
                task_run_id=updated.task_run_id,
                operation=operation,
                request_digest=digest,
                result_task_run_id=updated.task_run_id,
                outcome_id=outcome.outcome_id,
                task=updated,
            )
            return TaskCommandResult("accepted", updated, outcome, receipt)

        return self.journal.transact(work)

    def _transition(
        self,
        task: DurableTaskRun,
        target: TaskRunStatus,
        *,
        reason: str,
        turn_id: str | None,
        command_id: str | None,
    ) -> DurableTaskRun:
        return self.journal.transact(
            lambda txn: self._transition_in_txn(
                txn,
                task,
                target,
                reason=reason,
                turn_id=turn_id,
                command_id=command_id,
            )
        )

    def _transition_in_txn(
        self,
        txn: TaskJournalPort,
        task: DurableTaskRun,
        target: TaskRunStatus,
        *,
        reason: str,
        turn_id: str | None = None,
        command_id: str | None = None,
    ) -> DurableTaskRun:
        next_attempt = task.attempt + (
            1 if task.status is TaskRunStatus.FAILED and target is TaskRunStatus.OPEN else 0
        )
        try:
            validate_task_transition(task.status, target)
        except ValueError as exc:
            raise TaskCommandError(
                f"TaskRun {task.task_run_id} cannot transition from {task.status.value}"
            ) from exc
        transition = DurableTaskRunTransition(
            transition_id=self.id_source.new_id(TASK_TRANSITION_ID_PREFIX),
            workspace_id=self.workspace_id,
            session_id=task.session_id,
            task_run_id=task.task_run_id,
            from_status=task.status,
            to_status=target,
            reason=reason,
            turn_id=turn_id,
            command_id=command_id,
            attempt=next_attempt,
        )
        return txn.transition_task_run(
            self.workspace_id,
            task.task_run_id,
            target=target,
            transition=transition,
            expected_row_version=task.row_version,
        )

    def _outcome_in_txn(
        self,
        txn: TaskJournalPort,
        task: DurableTaskRun,
        *,
        trigger: TaskOutcomeTrigger,
        summary: str | None = None,
        feedback: tuple[str, ...] = (),
    ) -> DurableTaskOutcome:
        assembler = TaskOutcomeAssembler(
            txn, workspace_id=self.workspace_id, id_source=self.id_source
        )
        outcome = assembler.build(task, trigger=trigger, summary=summary, feedback=feedback)
        return txn.put_task_outcome(self.workspace_id, outcome)

    def _load(self, txn: TaskJournalPort, task_run_id: str) -> DurableTaskRun:
        task = txn.get_task_run(self.workspace_id, task_run_id)
        if task is None:
            raise TaskCommandError("TaskRun is missing")
        return task

    @staticmethod
    def _check_version(task: DurableTaskRun, expected_row_version: int | None) -> None:
        if expected_row_version is not None and task.row_version != expected_row_version:
            raise TaskCommandError("TaskRun row version is stale")

    def _replay(self, command_id: str | None, digest: str) -> TaskCommandResult | None:
        return self._replay_from(self.journal, command_id, digest)

    def _replay_in_txn(
        self,
        txn: TaskJournalPort,
        command_id: str | None,
        digest: str,
    ) -> TaskCommandResult | None:
        return self._replay_from(txn, command_id, digest)

    def _replay_from(
        self,
        reader: TaskJournalPort,
        command_id: str | None,
        digest: str,
    ) -> TaskCommandResult | None:
        if command_id is None:
            return None
        receipt = reader.get_task_command_receipt(self.workspace_id, command_id)
        if receipt is None:
            return None
        if receipt.request_digest != digest:
            raise TaskCommandConflict("Task command ID was reused with a different request")
        task_id = receipt.result_task_run_id or receipt.task_run_id
        task = reader.get_task_run(self.workspace_id, task_id) if task_id is not None else None
        outcome = (
            reader.get_task_outcome(self.workspace_id, receipt.outcome_id)
            if receipt.outcome_id is not None
            else None
        )
        return TaskCommandResult(
            "replay",
            task,
            outcome,
            receipt.model_copy(update={"disposition": TaskCommandDisposition.REPLAY}),
        )

    def _store_receipt(
        self,
        txn: TaskJournalPort,
        *,
        command_id: str | None,
        session_id: str,
        task_run_id: str | None,
        operation: str,
        request_digest: str,
        result_task_run_id: str | None = None,
        outcome_id: str | None = None,
        task: DurableTaskRun | None = None,
    ) -> TaskCommandReceipt | None:
        if command_id is None:
            return None
        receipt = TaskCommandReceipt(
            command_id=command_id,
            workspace_id=self.workspace_id,
            session_id=session_id,
            task_run_id=task_run_id,
            operation=operation,
            request_digest=request_digest,
            result_task_run_id=result_task_run_id,
            outcome_id=outcome_id,
            task_status=task.status if task is not None else None,
            row_version=task.row_version if task is not None else None,
        )
        return txn.put_task_command_receipt(self.workspace_id, receipt)


def command_id(id_source: IdSource) -> str:
    return id_source.new_id(COMMAND_ID_PREFIX)
