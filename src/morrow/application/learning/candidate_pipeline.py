"""Deterministic validation, deduplication, and persistence of Reviewer drafts."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from morrow.core.configuration_promotion import ConfigurationActivationStatus
from morrow.core.domain import DurableTaskOutcome, canonical_json_bytes
from morrow.core.learning import (
    LEARNING_MAX_REFERENCE_IDS,
    CandidateDraftBatch,
    LearningCandidate,
    LearningCandidateStatus,
    LearningCandidateType,
    LearningConfidenceBand,
    LearningEvidence,
    LearningEvidenceAuthority,
    LearningEvidenceSourceKind,
    LearningReview,
    LearningSensitivity,
    LearningSuppressionStatus,
    is_positive_explicit_user_evidence,
    scan_learning_text,
)
from morrow.core.ports import IdSource


@dataclass(frozen=True)
class CandidatePipelineResult:
    candidates: tuple[LearningCandidate, ...]
    duplicate_count: int = 0
    suppressed_count: int = 0
    rejected_count: int = 0


class LearningCandidatePipeline:
    """Keep candidate policy deterministic and independent from Reviewer I/O."""

    def __init__(
        self,
        *,
        journal,
        workspace_id: str,
        id_source: IdSource,
        clock: Callable[[], datetime],
        events,
    ) -> None:
        self.journal = journal
        self.workspace_id = workspace_id
        self.id_source = id_source
        self.clock = clock
        self.events = events

    def persist(
        self,
        txn,
        review: LearningReview,
        *,
        outcome: DurableTaskOutcome,
        policy,
        evidence: tuple[LearningEvidence, ...],
        batch: CandidateDraftBatch,
        allowed_evidence_ids: frozenset[str] | None = None,
    ) -> CandidatePipelineResult:
        by_id = {
            item.evidence_id: item
            for item in evidence
            if allowed_evidence_ids is None or item.evidence_id in allowed_evidence_ids
        }
        persisted: list[LearningCandidate] = []
        duplicate_count = 0
        suppressed_count = 0
        rejected_count = 0
        for draft in batch.drafts:
            eligible = self._eligible_draft(draft, by_id, outcome)
            if eligible is None:
                rejected_count += 1
                continue
            _candidate_type, sensitivity, confidence, basis = eligible
            fingerprint = LearningCandidate.fingerprint_for(
                candidate_type=draft.candidate_type,
                scope=draft.proposed_scope,
                semantic_key=draft.semantic_key,
                operation=draft.operation,
                proposed_payload=draft.proposed_payload,
            )
            existing = txn.list_learning_candidates(
                self.workspace_id,
                fingerprint=fingerprint,
                limit=500,
            )
            if any(item.status is LearningCandidateStatus.PROMOTING for item in existing):
                duplicate_count += 1
                continue
            active_proposed = next(
                (item for item in existing if item.status is LearningCandidateStatus.PROPOSED),
                None,
            )
            if active_proposed is not None:
                merged = tuple(dict.fromkeys((*active_proposed.evidence_ids, *draft.evidence_ids)))[
                    :LEARNING_MAX_REFERENCE_IDS
                ]
                if merged != active_proposed.evidence_ids:
                    txn.save_learning_candidate(
                        self.workspace_id,
                        active_proposed.model_copy(
                            update={
                                "evidence_ids": merged,
                                "row_version": active_proposed.row_version + 1,
                            }
                        ),
                        expected_row_version=active_proposed.row_version,
                    )
                duplicate_count += 1
                continue
            if any(
                item.status
                in {
                    LearningCandidateStatus.ACCEPTED,
                    LearningCandidateStatus.EDITED_AND_ACCEPTED,
                }
                and (
                    item.candidate_type
                    not in {
                        LearningCandidateType.PREFERENCE,
                        LearningCandidateType.PROFILE,
                    }
                    or self._has_current_configuration_activation(txn, item)
                )
                for item in existing
            ):
                duplicate_count += 1
                continue
            if any(
                item.status is LearningCandidateStatus.REJECTED
                and not self._has_new_explicit_evidence(txn, item, by_id, draft.evidence_ids)
                for item in existing
            ):
                rejected_count += 1
                continue
            suppressions = txn.list_learning_suppressions(
                self.workspace_id,
                candidate_type=draft.candidate_type.value,
                scope=draft.proposed_scope,
                limit=500,
            )
            if any(
                item.status is LearningSuppressionStatus.ACTIVE
                and (item.expires_at is None or item.expires_at > self._now())
                and (item.fingerprint == fingerprint or item.semantic_key == draft.semantic_key)
                for item in suppressions
            ):
                suppressed_count += 1
                continue
            if len(persisted) >= policy.max_candidates_per_review:
                rejected_count += 1
                continue
            conflict_refs = tuple(
                item.candidate_id
                for item in txn.list_learning_candidates(
                    self.workspace_id,
                    semantic_key=draft.semantic_key,
                    limit=500,
                )
                if item.fingerprint != fingerprint
                and item.status
                in {
                    LearningCandidateStatus.PROPOSED,
                    LearningCandidateStatus.PROMOTING,
                    LearningCandidateStatus.ACCEPTED,
                    LearningCandidateStatus.EDITED_AND_ACCEPTED,
                }
            )[:16]
            candidate = LearningCandidate.from_draft(
                candidate_id=self.id_source.new_id("lcn"),
                workspace_id=self.workspace_id,
                origin_review_id=review.review_id,
                draft=draft,
                confidence_band=confidence,
                confidence_basis=basis,
                sensitivity=sensitivity,
                expires_at=self._now() + timedelta(days=policy.candidate_ttl_days),
                now=self._now(),
            ).model_copy(update={"conflict_refs": conflict_refs})
            saved = txn.put_learning_candidate(self.workspace_id, candidate)
            persisted.append(saved)
            self.events.put(
                txn,
                event_type="learning.candidate_proposed",
                aggregate_kind="learning_candidate",
                aggregate_id=saved.candidate_id,
                payload={
                    "candidate_type": saved.candidate_type.value,
                    "confidence_band": saved.confidence_band.value,
                    "evidence_count": len(saved.evidence_ids),
                },
            )
        return CandidatePipelineResult(
            candidates=tuple(persisted),
            duplicate_count=duplicate_count,
            suppressed_count=suppressed_count,
            rejected_count=rejected_count,
        )

    def _has_current_configuration_activation(self, txn, candidate: LearningCandidate) -> bool:
        target = (
            "preferences"
            if candidate.candidate_type is LearningCandidateType.PREFERENCE
            else "profile"
        )
        path = getattr(candidate.proposed_payload, "path", None)
        if path is None:
            return True
        activations = txn.list_configuration_activations(
            self.workspace_id,
            target=target,
            path=path,
            status=ConfigurationActivationStatus.ACTIVE,
            limit=500,
        )
        return any(
            item.candidate_id == candidate.candidate_id and item.reverses_activation_id is None
            for item in activations
        )

    @staticmethod
    def _eligible_draft(draft, evidence_by_id, outcome):
        if draft.temporary_or_durable != "durable":
            return None
        selected = [evidence_by_id.get(item) for item in draft.evidence_ids]
        if any(item is None for item in selected):
            return None
        evidence = tuple(item for item in selected if item is not None)
        if any(item.safety_rejection_code is not None for item in evidence):
            return None
        payload_text = canonical_json_bytes(draft.proposed_payload.model_dump(mode="json")).decode(
            "utf-8"
        )
        if scan_learning_text(payload_text):
            return None
        authorities = {item.authority for item in evidence}
        sources = {item.source_kind for item in evidence}
        has_positive_explicit_evidence = any(
            is_positive_explicit_user_evidence(item) for item in evidence
        )
        if (
            draft.candidate_type
            in {
                LearningCandidateType.PREFERENCE,
                LearningCandidateType.PROFILE,
            }
            and not has_positive_explicit_evidence
        ):
            return None
        if draft.candidate_type is LearningCandidateType.PROJECT_KNOWLEDGE and not (
            LearningEvidenceAuthority.USER_ACCEPTANCE in authorities
            or (
                LearningEvidenceAuthority.DETERMINISTIC_TASK_FACT in authorities
                and outcome.task_status.value == "accepted"
            )
        ):
            return None
        if draft.candidate_type is LearningCandidateType.SKILL_CANDIDATE and not (
            len(evidence) >= 2
            and bool(
                sources
                & {
                    LearningEvidenceSourceKind.TOOL_EXECUTION,
                    LearningEvidenceSourceKind.TASK_TRANSITION,
                }
            )
        ):
            return None
        if draft.candidate_type in {
            LearningCandidateType.WORKFLOW_FEEDBACK,
            LearningCandidateType.ORCHESTRATION_POLICY_CANDIDATE,
        }:
            return None
        if has_positive_explicit_evidence:
            confidence = LearningConfidenceBand.HIGH
            basis = ("explicit_user_evidence",)
        elif LearningEvidenceAuthority.DETERMINISTIC_TASK_FACT in authorities:
            confidence = LearningConfidenceBand.MEDIUM
            basis = ("accepted_task_fact",)
        else:
            confidence = LearningConfidenceBand.LOW
            basis = ("behavioral_signal",)
        sensitivity = (
            LearningSensitivity.PERSONAL
            if draft.candidate_type is LearningCandidateType.PROFILE
            else LearningSensitivity.NORMAL
        )
        return draft.candidate_type, sensitivity, confidence, basis

    def _has_new_explicit_evidence(self, txn, candidate, evidence_by_id, evidence_ids) -> bool:
        old = txn.list_learning_candidate_evidence(self.workspace_id, candidate.candidate_id)
        old_digests = {item.content_digest for item in old}
        return any(
            is_positive_explicit_user_evidence(item) and item.content_digest not in old_digests
            for evidence_id in evidence_ids
            for item in (evidence_by_id[evidence_id],)
        )

    def _now(self) -> datetime:
        value = self.clock()
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


__all__ = ["CandidatePipelineResult", "LearningCandidatePipeline"]
