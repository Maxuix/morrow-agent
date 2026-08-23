"""Bounded evidence extraction and Reviewer-context construction."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from morrow.core.domain import DurableTaskOutcome, TaskRunStatus, sha256_digest
from morrow.core.learning import (
    LEARNING_EVIDENCE_EXCERPT_MAX_CHARS,
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

_LEGACY_PERSISTENT_MARKERS = (
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
_LEGACY_NEGATIVE_MARKERS = (
    "不要记住",
    "不要保存",
    "不要默认",
    "不要总是",
    "不要每次",
    "不必记住",
    "不必保存",
    "do not remember",
    "don't remember",
    "do not save",
    "don't save",
    "do not always",
    "don't always",
)
_LEGACY_NON_DURABLE_MARKERS = (
    "这次",
    "本次",
    "临时",
    "暂时",
    "仅在这次",
    "for this answer",
    "just this time",
)
_LEGACY_UNTRUSTED_CONTEXT_MARKERS = (
    "示例",
    "例如",
    "文档中",
    "文档示例",
    "引用",
    "他说",
    "假设",
    "example",
    "quoted",
    "hypothetical",
    "suppose",
)

_CONTEXT_MAX_EVIDENCE_ITEMS = 8
_CONTEXT_MAX_SUPPRESSION_ITEMS = 8
_CONTEXT_OUTCOME_MAX_ITEMS = 8
_CONTEXT_OUTCOME_LINE_MAX = 256


def _utc(clock: Callable[[], datetime]) -> datetime:
    value = clock()
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class LearningEvidenceExtractor:
    """Read accepted-Task evidence for the legacy non-Preference Learning pipeline.

    Preference v2 reads its own current-user Evidence and has no keyword classifier.
    """

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
        task_turn_ids = {
            turn.turn_id
            for turn in self.journal.list_task_turns(self.workspace_id, outcome.task_run_id)
        }
        session_turns = self.journal.list_session_turns(self.workspace_id, outcome.session_id)
        records = self.journal.load_records(self.workspace_id, outcome.session_id)
        segments = self._task_segments(records, len(session_turns))
        if len(segments) != len(session_turns):
            return tuple(evidence)
        for turn, segment in zip(session_turns, segments, strict=True):
            if turn.turn_id not in task_turn_ids:
                continue
            for record in segment:
                if record.kind != "message" or record.payload.get("role") != "user":
                    continue
                content = record.payload.get("content")
                if not isinstance(content, str) or not content.strip():
                    continue
                authority, explicitness, polarity = self._classify_legacy_learning_user_text(
                    content
                )
                evidence.append(
                    self._text_evidence(
                        review,
                        task_run_id=outcome.task_run_id,
                        source_kind=LearningEvidenceSourceKind.USER_TURN,
                        source_id=turn.turn_id,
                        source_pointer=f"{record.record_id}:{review.review_id}",
                        text=content,
                        actor=LearningEvidenceActor.USER,
                        authority=authority,
                        explicitness=explicitness,
                        polarity=polarity,
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
            normalized = " ".join(text.split())
            excerpt = normalize_learning_text(
                normalized[:LEARNING_EVIDENCE_EXCERPT_MAX_CHARS].rstrip(),
                label="learning evidence",
                maximum=LEARNING_EVIDENCE_EXCERPT_MAX_CHARS,
            )
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
    def _classify_legacy_learning_user_text(
        text: str,
    ) -> tuple[
        LearningEvidenceAuthority,
        LearningEvidenceExplicitness,
        LearningEvidencePolarity,
    ]:
        lowered = text.casefold()
        if any(marker.casefold() in lowered for marker in _LEGACY_NEGATIVE_MARKERS):
            return (
                LearningEvidenceAuthority.USER_EXPLICIT_PERSISTENT,
                LearningEvidenceExplicitness.EXPLICIT,
                LearningEvidencePolarity.NEGATIVE,
            )
        if any(marker.casefold() in lowered for marker in _LEGACY_NON_DURABLE_MARKERS):
            return (
                LearningEvidenceAuthority.BEHAVIORAL_SIGNAL,
                LearningEvidenceExplicitness.BEHAVIORAL,
                LearningEvidencePolarity.NEUTRAL,
            )
        if any(marker.casefold() in lowered for marker in _LEGACY_UNTRUSTED_CONTEXT_MARKERS):
            return (
                LearningEvidenceAuthority.UNTRUSTED_EXTERNAL_CONTENT,
                LearningEvidenceExplicitness.INFERRED,
                LearningEvidencePolarity.NEUTRAL,
            )
        if any(marker.casefold() in lowered for marker in _LEGACY_PERSISTENT_MARKERS):
            return (
                LearningEvidenceAuthority.USER_EXPLICIT_PERSISTENT,
                LearningEvidenceExplicitness.EXPLICIT,
                LearningEvidencePolarity.POSITIVE,
            )
        return (
            LearningEvidenceAuthority.BEHAVIORAL_SIGNAL,
            LearningEvidenceExplicitness.BEHAVIORAL,
            LearningEvidencePolarity.NEUTRAL,
        )

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
        selected_evidence = self._prioritize_evidence(evidence, policy.max_evidence_per_review)
        evidence_limit = min(len(selected_evidence), _CONTEXT_MAX_EVIDENCE_ITEMS)
        suppression_limit = min(len(suppressions), _CONTEXT_MAX_SUPPRESSION_ITEMS)

        for line_limit in (_CONTEXT_OUTCOME_LINE_MAX, 128, 64):
            projected_outcome = self._project_outcome(outcome, line_limit=line_limit)
            for evidence_count in range(evidence_limit, -1, -1):
                for suppression_count in range(suppression_limit, -1, -1):
                    try:
                        return LearningContext(
                            workspace_id=self.workspace_id,
                            task_outcome=projected_outcome,
                            evidence=selected_evidence[:evidence_count],
                            suppressions=suppressions[:suppression_count],
                            policy=policy,
                            candidate_budget=policy.max_candidates_per_review,
                            rendered_char_budget=LEARNING_CONTEXT_MAX_RENDERED_CHARS,
                        )
                    except ValueError as exc:
                        if "rendered character budget" not in str(exc):
                            raise
        raise ValueError("learning context cannot fit the rendered character budget")

    @staticmethod
    def _prioritize_evidence(
        evidence: tuple[LearningEvidence, ...], policy_limit: int
    ) -> tuple[LearningEvidence, ...]:
        limit = min(policy_limit, _CONTEXT_MAX_EVIDENCE_ITEMS)
        ranked = sorted(
            enumerate(evidence[:policy_limit]),
            key=lambda pair: (
                0
                if pair[1].source_kind is LearningEvidenceSourceKind.TASK_OUTCOME
                else 1
                if pair[1].authority
                in {
                    LearningEvidenceAuthority.USER_EXPLICIT_PERSISTENT,
                    LearningEvidenceAuthority.USER_ACCEPTANCE,
                }
                else 2
                if pair[1].source_kind is LearningEvidenceSourceKind.TASK_TRANSITION
                else 3,
                pair[0],
            ),
        )
        return tuple(item for _index, item in sorted(ranked[:limit], key=lambda pair: pair[0]))

    @staticmethod
    def _project_outcome(outcome: DurableTaskOutcome, *, line_limit: int) -> DurableTaskOutcome:
        def bound_lines(values: tuple[str, ...]) -> tuple[str, ...]:
            return tuple(value[:line_limit] for value in values[:_CONTEXT_OUTCOME_MAX_ITEMS])

        return outcome.model_copy(
            update={
                "summary": outcome.summary[:line_limit],
                "changed_paths": outcome.changed_paths[:_CONTEXT_OUTCOME_MAX_ITEMS],
                "validation_facts": bound_lines(outcome.validation_facts),
                "side_effects": bound_lines(outcome.side_effects),
                "unresolved_items": bound_lines(outcome.unresolved_items),
                "completion_basis": bound_lines(outcome.completion_basis),
                "feedback": bound_lines(outcome.feedback),
                "evidence_refs": outcome.evidence_refs[:_CONTEXT_OUTCOME_MAX_ITEMS],
                "artifact_refs": (),
            }
        )


__all__ = ["LearningContextBuilder", "LearningEvidenceExtractor"]
