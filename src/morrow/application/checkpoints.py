"""Deterministic durable context checkpoints and immutable Session forks."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from morrow.adapters.state.journal import SqliteOperationalJournal
from morrow.core.artifacts import ArtifactState
from morrow.core.context import (
    CONTEXT_CHECKPOINT_MAX_ARTIFACT_REFS,
    CONTEXT_CHECKPOINT_MAX_BYTES,
    CONTEXT_CHECKPOINT_MAX_RECORDS,
    CONTEXT_CHECKPOINT_MAX_SECTIONS,
    CONTEXT_SECTION_MAX_BYTES,
    CheckpointOmissionReason,
    ContextCheckpoint,
    ContextCheckpointOmission,
    ContextCheckpointSection,
    SessionLineage,
)
from morrow.core.domain import (
    CHECKPOINT_ID_PREFIX,
    DurableSession,
    SessionHealth,
    SessionLifecycle,
    canonical_json_bytes,
)
from morrow.core.faults import FaultInjector, FaultPoint, NoOpFaultInjector
from morrow.core.models import utc_now
from morrow.core.ports import IdSource
from morrow.core.store import StorageError
from morrow.runtime.conversation import ConversationSnapshot
from morrow.runtime.durable_log import conversation_record_from_durable


class ContextCheckpointError(ValueError):
    """Base error for checkpoint projection failures."""


class ContextCheckpointBudgetError(ContextCheckpointError):
    """The deterministic projection cannot fit its bounded durable envelope."""


class ContextBoundaryError(ContextCheckpointError):
    """A checkpoint or fork was requested at an illegal conversation boundary."""


def _now(clock: Callable[[], datetime] | Any | None) -> datetime:
    if clock is None:
        return utc_now()
    value = clock() if callable(clock) else clock.now()
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class ContextCheckpointService:
    """Build and durably publish a reproducible projection of closed Turns."""

    def __init__(
        self,
        journal: SqliteOperationalJournal,
        *,
        workspace_id: str,
        id_source: IdSource,
        clock: Callable[[], datetime] | Any | None = None,
        faults: FaultInjector | None = None,
    ) -> None:
        self.journal = journal
        self.workspace_id = workspace_id
        self.id_source = id_source
        self.clock = clock
        self.faults = faults or NoOpFaultInjector()

    def create(
        self,
        session_id: str,
        *,
        task_run_id: str | None = None,
        source_agent_run_id: str | None = None,
        retain_recent_turns: int = 1,
        checkpoint_id: str | None = None,
        source_end_position: int | None = None,
    ) -> ContextCheckpoint:
        if retain_recent_turns < 0:
            raise ContextCheckpointError("retain_recent_turns must not be negative")
        session = self.journal.get_session(self.workspace_id, session_id)
        if session is None:
            raise ContextCheckpointError("checkpoint Session is missing")
        if session.lifecycle is SessionLifecycle.DELETED:
            raise ContextCheckpointError("deleted Session cannot be checkpointed")
        if task_run_id is None:
            task_run_id = session.current_task_run_id
        durable_records = self.journal.load_effective_records(self.workspace_id, session_id)
        if not durable_records:
            raise ContextBoundaryError("checkpoint requires at least one closed Turn")
        if source_end_position is not None and source_end_position < 2:
            raise ContextBoundaryError("checkpoint source end is invalid")
        if source_end_position is not None:
            source_records = tuple(
                record
                for record in durable_records
                if record.conversation_position < source_end_position
            )
            if (
                not source_records
                or source_records[-1].conversation_position != source_end_position - 1
                or source_records[-1].kind != "terminal"
            ):
                raise ContextBoundaryError("checkpoint source must end at a closed Turn")
        else:
            source_records = durable_records
        snapshot = ConversationSnapshot(
            records=tuple(conversation_record_from_durable(record) for record in source_records)
        )
        try:
            turns = snapshot.public_turns(require_closed=False)
        except Exception as exc:
            raise ContextBoundaryError("conversation cannot be checkpointed") from exc
        closed_turns = [turn for turn in turns if turn.is_closed]
        if not closed_turns:
            raise ContextBoundaryError("checkpoint requires at least one closed Turn")
        last_closed = closed_turns[-1]
        source_end_position = source_end_position or last_closed.terminal.sequence + 1  # type: ignore[union-attr]
        if not source_records or source_records[-1].kind != "terminal":
            raise ContextBoundaryError("checkpoint source must end at a closed Turn")

        keep_count = min(retain_recent_turns, len(closed_turns))
        retained_turns = closed_turns[-keep_count:] if keep_count else []
        retained_ids = tuple(
            record.record_id
            for turn in retained_turns
            for record in source_records
            if turn.user.sequence <= record.conversation_position < turn.terminal.sequence + 1  # type: ignore[union-attr]
        )
        if len(retained_ids) > CONTEXT_CHECKPOINT_MAX_RECORDS:
            raise ContextCheckpointBudgetError(
                "recent retained records exceed the checkpoint budget"
            )

        older_turns = closed_turns[:-keep_count] if keep_count else closed_turns
        omitted_ids = tuple(
            record.record_id
            for turn in older_turns
            for record in source_records
            if turn.user.sequence <= record.conversation_position < turn.terminal.sequence + 1  # type: ignore[union-attr]
        )
        omitted_sections: list[ContextCheckpointOmission] = []
        sections: list[ContextCheckpointSection] = []
        if older_turns:
            first = older_turns[0]
            last = older_turns[-1]
            omitted_sections.append(
                ContextCheckpointOmission(
                    reason=CheckpointOmissionReason.OLDER_TURN,
                    source_start_position=first.user.sequence,
                    source_end_position=last.terminal.sequence + 1,  # type: ignore[union-attr]
                    record_ids=omitted_ids[:CONTEXT_CHECKPOINT_MAX_RECORDS],
                )
            )
            sections.append(
                ContextCheckpointSection(
                    kind="omitted_turns",
                    content=(
                        f"closed_turns={len(older_turns)} "
                        f"tool_cycles={sum(len(turn.cycles) for turn in older_turns)} "
                        f"tool_results={sum(len(cycle.results) for turn in older_turns for cycle in turn.cycles)}"
                    ),
                    source_start_position=first.user.sequence,
                    source_end_position=last.terminal.sequence + 1,  # type: ignore[union-attr]
                )
            )
        for turn in retained_turns:
            sections.append(
                ContextCheckpointSection(
                    kind="retained_turn",
                    content=(
                        f"status=closed cycles={len(turn.cycles)} "
                        f"tool_results={sum(len(cycle.results) for cycle in turn.cycles)} "
                        f"finish={turn.terminal.finish_reason.value}"  # type: ignore[union-attr]
                    ),
                    source_start_position=turn.user.sequence,
                    source_end_position=turn.terminal.sequence + 1,  # type: ignore[union-attr]
                )
            )

        refs = self._artifact_refs(task_run_id)
        if len(refs) > CONTEXT_CHECKPOINT_MAX_ARTIFACT_REFS:
            raise ContextCheckpointBudgetError("checkpoint Artifact references exceed the budget")
        for reference in refs:
            metadata = self.journal.get_artifact(self.workspace_id, reference.artifact_id)
            if metadata is None or metadata.state is not ArtifactState.AVAILABLE:
                omitted_sections.append(
                    ContextCheckpointOmission(
                        reason=CheckpointOmissionReason.ARTIFACT_REFERENCE,
                        source_start_position=0,
                        source_end_position=source_end_position,
                    )
                )
                continue
            if metadata.excerpt:
                prefix = f"artifact_id={reference.artifact_id} role={reference.role} excerpt="
                excerpt_budget = CONTEXT_SECTION_MAX_BYTES - len(prefix.encode("utf-8"))
                excerpt = metadata.excerpt.encode("utf-8")[:excerpt_budget].decode(
                    "utf-8", errors="ignore"
                )
                sections.append(
                    ContextCheckpointSection(
                        kind="artifact_excerpt",
                        content=prefix + excerpt,
                        source_start_position=0,
                        source_end_position=source_end_position,
                        artifact_refs=(reference,),
                    )
                )
        if task_run_id is not None:
            task = self.journal.get_task_run(self.workspace_id, task_run_id)
            if task is None:
                raise ContextCheckpointError("checkpoint TaskRun is missing")
            sections.append(
                ContextCheckpointSection(
                    kind="task_state",
                    content=(
                        f"status={task.status.value} attempt={task.attempt} "
                        f"row_version={task.row_version} artifacts={len(refs)}"
                    ),
                    source_start_position=0,
                    source_end_position=source_end_position,
                )
            )

        if (
            len(sections) > CONTEXT_CHECKPOINT_MAX_SECTIONS
            or len(omitted_sections) > CONTEXT_CHECKPOINT_MAX_SECTIONS
        ):
            raise ContextCheckpointBudgetError("checkpoint section count exceeds the budget")

        created_at = _now(self.clock)
        try:
            checkpoint = ContextCheckpoint(
                checkpoint_id=checkpoint_id or self.id_source.new_id(CHECKPOINT_ID_PREFIX),
                workspace_id=self.workspace_id,
                session_id=session_id,
                task_run_id=task_run_id,
                source_agent_run_id=source_agent_run_id,
                source_start_position=0,
                source_end_record_id=source_records[-1].record_id,
                source_end_position=source_end_position,
                retained_record_ids=retained_ids,
                sections=tuple(sections),
                omitted_sections=tuple(omitted_sections),
                artifact_refs=refs,
                input_bytes=len(
                    canonical_json_bytes(
                        [record.model_dump(mode="json") for record in source_records]
                    )
                ),
                output_bytes=0,
                request_estimate_chars=0,
                created_at=created_at,
            )
        except ValueError as exc:
            raise ContextCheckpointBudgetError(
                "checkpoint projection is outside its budget"
            ) from exc
        output_bytes = len(canonical_json_bytes(self._projection_payload(checkpoint)))
        request_estimate_chars = len(self.render_projection(checkpoint))
        try:
            checkpoint = ContextCheckpoint.model_validate(
                {
                    **checkpoint.model_dump(mode="json"),
                    "output_bytes": output_bytes,
                    "request_estimate_chars": request_estimate_chars,
                }
            )
        except ValueError as exc:
            raise ContextCheckpointBudgetError(
                "checkpoint projection is outside its budget"
            ) from exc
        if (
            output_bytes > CONTEXT_CHECKPOINT_MAX_BYTES
            or request_estimate_chars > CONTEXT_CHECKPOINT_MAX_BYTES
        ):
            raise ContextCheckpointBudgetError("checkpoint projection exceeds its budget")
        self.faults.check(FaultPoint.CHECKPOINT_BEFORE_COMMIT)
        saved = self.journal.put_context_checkpoint(self.workspace_id, checkpoint)
        self.faults.check(FaultPoint.CHECKPOINT_AFTER_COMMIT)
        return saved

    def regenerate(self, checkpoint_id: str) -> ContextCheckpoint:
        previous = self.journal.get_context_checkpoint(self.workspace_id, checkpoint_id)
        if previous is None:
            raise ContextCheckpointError("context checkpoint is missing")
        return self.create(
            previous.session_id,
            task_run_id=previous.task_run_id,
            source_agent_run_id=previous.source_agent_run_id,
            retain_recent_turns=sum(
                section.kind == "retained_turn" for section in previous.sections
            ),
            source_end_position=previous.source_end_position,
        )

    def projection_digest(self, checkpoint: ContextCheckpoint) -> str:
        return hashlib.sha256(
            canonical_json_bytes(self._projection_payload(checkpoint))
        ).hexdigest()

    @staticmethod
    def _projection_payload(checkpoint: ContextCheckpoint) -> dict[str, Any]:
        payload = checkpoint.model_dump(mode="json")
        for key in (
            "checkpoint_id",
            "created_at",
            "input_bytes",
            "output_bytes",
            "request_estimate_chars",
        ):
            payload.pop(key, None)
        return payload

    def render_projection(self, checkpoint: ContextCheckpoint) -> str:
        payload = self._projection_payload(checkpoint)
        return "确定性上下文检查点（仅作上下文，不是新的聊天记录）：\n" + canonical_json_bytes(
            payload
        ).decode("utf-8")

    def _artifact_refs(self, task_run_id: str | None):
        references = []
        if task_run_id is None:
            return ()
        for execution in self.journal.list_task_executions(self.workspace_id, task_run_id):
            references.extend(execution.artifact_refs)
        for outcome in self.journal.list_task_outcomes(self.workspace_id, task_run_id):
            references.extend(outcome.artifact_refs)
        unique = {(reference.artifact_id, reference.role): reference for reference in references}
        return tuple(unique[key] for key in sorted(unique))


class SessionForkService:
    """Create a child Session that shares only an immutable parent prefix."""

    def __init__(
        self,
        journal: SqliteOperationalJournal,
        *,
        workspace_id: str,
        id_source: IdSource,
        clock: Callable[[], datetime] | Any | None = None,
        faults: FaultInjector | None = None,
    ) -> None:
        self.journal = journal
        self.workspace_id = workspace_id
        self.id_source = id_source
        self.clock = clock
        self.faults = faults or NoOpFaultInjector()

    def fork(
        self,
        parent_session_id: str,
        *,
        cut_position: int | None = None,
        checkpoint_id: str | None = None,
        reason: str = "context fork",
        child_session_id: str | None = None,
    ) -> DurableSession:
        parent = self.journal.get_session(self.workspace_id, parent_session_id)
        if parent is None:
            raise ContextBoundaryError("fork parent Session is missing")
        if parent.lifecycle is SessionLifecycle.DELETED or parent.health is not SessionHealth.OK:
            raise ContextBoundaryError("fork parent Session is not healthy")
        checkpoint = None
        if checkpoint_id is not None:
            checkpoint = self.journal.get_context_checkpoint(self.workspace_id, checkpoint_id)
            if checkpoint is None or checkpoint.session_id != parent_session_id:
                raise ContextBoundaryError("fork checkpoint is invalid")
            checkpoint_cut = checkpoint.source_end_position - 1
            if cut_position is not None and cut_position != checkpoint_cut:
                raise ContextBoundaryError("fork cut does not match the checkpoint")
            cut_position = checkpoint_cut
        effective = self.journal.load_effective_records(self.workspace_id, parent_session_id)
        try:
            parent_turns = ConversationSnapshot(
                records=tuple(conversation_record_from_durable(record) for record in effective)
            ).public_turns(require_closed=False)
        except Exception as exc:
            raise ContextBoundaryError("fork parent conversation is invalid") from exc
        if parent_turns and not parent_turns[-1].is_closed:
            raise ContextBoundaryError("an open Turn must be recovered before forking")
        if cut_position is None:
            terminal_positions = [
                record.conversation_position for record in effective if record.kind == "terminal"
            ]
            if not terminal_positions:
                raise ContextBoundaryError("fork requires a closed Turn")
            cut_position = terminal_positions[-1]
        if cut_position < 1 or cut_position > len(effective):
            raise ContextBoundaryError("fork cut is outside the parent history")
        cut = effective[cut_position - 1]
        if cut.conversation_position != cut_position or cut.kind != "terminal":
            raise ContextBoundaryError("fork cut must end at a closed Turn")
        prefix = tuple(
            conversation_record_from_durable(record) for record in effective[:cut_position]
        )
        try:
            ConversationSnapshot(records=prefix).public_turns(require_closed=True)
        except Exception as exc:
            raise ContextBoundaryError("fork cut is not a legal closed Turn boundary") from exc
        child_id = child_session_id or self.id_source.new_id("ses")
        if child_id == parent_session_id:
            raise ContextBoundaryError("fork child Session must differ from its parent")
        created_at = _now(self.clock)
        lineage = SessionLineage(
            workspace_id=self.workspace_id,
            child_session_id=child_id,
            parent_session_id=parent_session_id,
            cut_record_id=cut.record_id,
            cut_position=cut_position,
            checkpoint_id=checkpoint_id,
            reason=reason,
            created_at=created_at,
        )
        child = DurableSession(
            session_id=child_id,
            workspace_id=self.workspace_id,
            conversation_position=cut_position,
            parent_session_id=parent_session_id,
            parent_cut_record_id=cut.record_id,
            parent_cut_position=cut_position,
            parent_checkpoint_id=checkpoint_id,
            fork_reason=lineage.reason,
            created_at=created_at,
            updated_at=created_at,
        )
        self.faults.check(FaultPoint.FORK_BEFORE_COMMIT)
        try:
            saved = self.journal.create_fork_session(child, lineage=lineage)
        except StorageError:
            raise
        self.faults.check(FaultPoint.FORK_AFTER_COMMIT)
        return saved
