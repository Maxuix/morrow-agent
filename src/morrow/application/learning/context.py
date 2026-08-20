"""Bounded evidence extraction and Reviewer-context construction."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from morrow.core.domain import DurableTaskOutcome, TaskRunStatus, sha256_digest
from morrow.core.learning import (
    LearningEvidence,
    LearningEvidenceActor,
    LearningEvidenceAuthority,
    LearningEvidenceExplicitness,
    LearningEvidencePolarity,
    LearningEvidenceSourceKind,
    LearningPolicy,
    LearningReview,
    LearningScope,
    learning_safety_codes,
)
from morrow.core.learning_ports import (
    LEARNING_CONTEXT_MAX_RENDERED_CHARS,
    LearningContext,
)
from morrow.core.learning_safety import normalize_learning_text
from morrow.core.ports import IdSource

_PERSISTENT_MARKERS = (
    "以后",
    "默认",
    "总是",
    "请记住",
    "长期",
    "always",
    "default",
    "from now",
    "remember",
)


def _utc(clock: Callable[[], datetime]) -> datetime:
    value = clock()
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class LearningEvidenceExtractor:
    """Read only the accepted Task's narrow durable projections."""

    def __init__(
        self,
        *,
        journal,
        workspace_id: str,
        id_source: IdSource,
        clock: Callable[[], datetime],
    ) -> None:
        self.journal = journal
        self.workspace_id = workspace_id
        self.id_source = id_source
        self.clock = clock

    def extract(
        self, review: LearningReview, outcome: DurableTaskOutcome
    ) -> tuple[LearningEvidence, ...]:
        evidence: list[LearningEvidence] = [self._outcome_evidence(review, outcome)]
        turns = self.journal.list_task_turns(self.workspace_id, outcome.task_run_id)
        records = self.journal.load_records(self.workspace_id, outcome.session_id)
        for turn, segment in zip(
            turns[-len(turns) :], self._task_segments(records, len(turns)), strict=False
        ):
            for record in segment:
                if record.kind != "message" or record.payload.get("role") != "user":
                    continue
                content = record.payload.get("content")
                if not isinstance(content, str) or not content.strip():
                    continue
                persistent = self._looks_persistent(content)
                evidence.append(
                    self._text_evidence(
                        review,
                        task_run_id=outcome.task_run_id,
                        source_kind=LearningEvidenceSourceKind.USER_TURN,
                        source_id=turn.turn_id,
                        source_pointer=f"{record.record_id}:{review.review_id}",
                        text=content,
                        actor=LearningEvidenceActor.USER,
                        authority=(
                            LearningEvidenceAuthority.USER_EXPLICIT_PERSISTENT
                            if persistent
                            else LearningEvidenceAuthority.BEHAVIORAL_SIGNAL
                        ),
                        explicitness=(
                            LearningEvidenceExplicitness.EXPLICIT
                            if persistent
                            else LearningEvidenceExplicitness.BEHAVIORAL
                        ),
                        polarity=LearningEvidencePolarity.NEUTRAL,
                    )
                )

        for transition in self.journal.list_task_transitions(
            self.workspace_id, outcome.task_run_id
        ):
            if (
                transition.from_status is TaskRunStatus.READY_FOR_ACCEPTANCE
                and transition.to_status is TaskRunStatus.OPEN
            ):
                evidence.append(
                    self._text_evidence(
                        review,
                        task_run_id=outcome.task_run_id,
                        source_kind=LearningEvidenceSourceKind.TASK_TRANSITION,
                        source_id=transition.transition_id,
                        source_pointer=f"correction:{review.review_id}",
                        text="user continued after an answer and corrected the task",
                        actor=LearningEvidenceActor.USER,
                        authority=LearningEvidenceAuthority.USER_CORRECTION,
                        explicitness=LearningEvidenceExplicitness.EXPLICIT,
                        polarity=LearningEvidencePolarity.NEGATIVE,
                    )
                )
        return tuple(evidence)

    def persist(
        self,
        txn,
        *,
        review: LearningReview,
        evidence: tuple[LearningEvidence, ...],
    ) -> tuple[LearningEvidence, ...]:
        existing = txn.list_learning_review_evidence(self.workspace_id, review.review_id)
        by_source = {
            (item.source_kind, item.source_id, item.source_pointer): item for item in existing
        }
        for item in evidence:
            key = (item.source_kind, item.source_id, item.source_pointer)
            saved = by_source.get(key)
            if saved is None:
                saved = txn.put_learning_evidence(self.workspace_id, item)
                by_source[key] = saved
            txn.link_learning_review_evidence(
                self.workspace_id, review.review_id, saved.evidence_id
            )
        return txn.list_learning_review_evidence(self.workspace_id, review.review_id)

    def _outcome_evidence(
        self, review: LearningReview, outcome: DurableTaskOutcome
    ) -> LearningEvidence:
        summary = (
            f"accepted TaskOutcome; validation_facts={len(outcome.validation_facts)}; "
            f"changed_paths={len(outcome.changed_paths)}"
        )
        return self._text_evidence(
            review,
            task_run_id=outcome.task_run_id,
            source_kind=LearningEvidenceSourceKind.TASK_OUTCOME,
            source_id=outcome.outcome_id,
            source_pointer=f"outcome:{review.review_id}",
            text=summary,
            actor=LearningEvidenceActor.SYSTEM,
            authority=LearningEvidenceAuthority.DETERMINISTIC_TASK_FACT,
            explicitness=LearningEvidenceExplicitness.INFERRED,
            polarity=LearningEvidencePolarity.POSITIVE,
            scope_hint=LearningScope.WORKSPACE,
        )

    def _text_evidence(
        self,
        review: LearningReview,
        *,
        task_run_id: str,
        source_kind: LearningEvidenceSourceKind,
        source_id: str,
        source_pointer: str,
        text: str,
        actor: LearningEvidenceActor,
        authority: LearningEvidenceAuthority,
        explicitness: LearningEvidenceExplicitness,
        polarity: LearningEvidencePolarity,
        scope_hint: LearningScope | None = None,
    ) -> LearningEvidence:
        codes = learning_safety_codes(text)
        rejection = codes[0] if codes else None
        excerpt = None
        if rejection is None:
            excerpt = normalize_learning_text(text, label="learning evidence", maximum=512)
        return LearningEvidence(
            evidence_id=self.id_source.new_id("lev"),
            workspace_id=self.workspace_id,
            origin_review_id=review.review_id,
            task_run_id=task_run_id,
            source_kind=source_kind,
            source_id=source_id,
            source_pointer=source_pointer,
            actor=actor,
            authority=authority,
            explicitness=explicitness,
            polarity=polarity,
            scope_hint=scope_hint,
            excerpt_redacted=excerpt,
            content_digest=sha256_digest(text),
            safety_rejection_code=rejection,
            observed_at=_utc(self.clock),
            created_at=_utc(self.clock),
        )

    @staticmethod
    def _looks_persistent(text: str) -> bool:
        lowered = text.casefold()
        return any(marker.casefold() in lowered for marker in _PERSISTENT_MARKERS)

    @staticmethod
    def _task_segments(records, turn_count: int) -> tuple[tuple[object, ...], ...]:
        if turn_count <= 0:
            return ()
        segments: list[list[object]] = [[]]
        for record in records:
            if record.kind == "terminal":
                if segments[-1]:
                    segments.append([])
            else:
                segments[-1].append(record)
        non_empty = [tuple(segment) for segment in segments if segment]
        return tuple(non_empty[-turn_count:])


class LearningContextBuilder:
    """Construct a strict Reviewer context from already persisted projections."""

    def __init__(self, *, journal, workspace_id: str) -> None:
        self.journal = journal
        self.workspace_id = workspace_id

    def build(
        self,
        *,
        review: LearningReview,
        outcome: DurableTaskOutcome,
        policy: LearningPolicy,
        evidence: tuple[LearningEvidence, ...],
    ) -> LearningContext:
        suppressions = self.journal.list_learning_suppressions(
            self.workspace_id,
            limit=policy.max_evidence_per_review,
        )
        return LearningContext(
            workspace_id=self.workspace_id,
            task_outcome=outcome,
            evidence=evidence[: policy.max_evidence_per_review],
            suppressions=suppressions,
            policy=policy,
            candidate_budget=policy.max_candidates_per_review,
            rendered_char_budget=LEARNING_CONTEXT_MAX_RENDERED_CHARS,
        )


__all__ = ["LearningContextBuilder", "LearningEvidenceExtractor"]
