"""Read-only Learning Inbox queries and candidate decision previews."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import TypeAdapter

from morrow.application.api_context import ApplicationCommandContext
from morrow.application.learning.decisions import (
    LearningDecisionService,
)
from morrow.application.learning.policy import LearningPolicyService, LearningPolicyStatus
from morrow.application.learning.promotion import LearningPromotionService
from morrow.core.application import ApplicationError, ApplicationErrorCode, QueryPage
from morrow.core.domain import canonical_json_bytes, sha256_digest
from morrow.core.learning import (
    LEARNING_MAX_REFERENCE_IDS,
    LearningCandidate,
    LearningCandidateStatus,
    LearningCandidateType,
    LearningReview,
    LearningReviewStatus,
    LearningScope,
)
from morrow.core.learning_memory import (
    LearningCandidateDecisionKind,
    LearningConflictResolution,
    ProjectKnowledgeStatus,
)
from morrow.core.learning_payloads import CandidatePayload, ProjectKnowledgeCandidatePayload
from morrow.core.learning_views import (
    LEARNING_QUERY_MAX_PAGE_SIZE,
    LearningCandidateDecisionPreview,
    LearningCandidateSummary,
    LearningCandidateView,
    LearningEvidenceSummary,
    LearningPreviewValue,
    LearningReviewView,
    LearningStatusView,
    LearningSuppressionSummary,
    LearningTargetSummary,
)

_CANDIDATE_PAYLOAD_ADAPTER = TypeAdapter(CandidatePayload)


def _now(context: ApplicationCommandContext) -> datetime:
    value = context.clock()
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _offset(cursor: str | None, limit: int) -> int:
    if (
        isinstance(limit, bool)
        or not isinstance(limit, int)
        or not 1 <= limit <= LEARNING_QUERY_MAX_PAGE_SIZE
    ):
        raise ApplicationError(ApplicationErrorCode.INVALID, "learning query page size is invalid")
    if cursor is None:
        return 0
    try:
        value = int(cursor)
    except (TypeError, ValueError) as exc:
        raise ApplicationError(
            ApplicationErrorCode.INVALID, "learning query cursor is invalid"
        ) from exc
    if value < 0:
        raise ApplicationError(ApplicationErrorCode.INVALID, "learning query cursor is invalid")
    return value


class LearningApplicationService:
    """Focused Learning facade; mutations are added by later Inbox sub-tasks."""

    def __init__(
        self,
        context: ApplicationCommandContext,
        *,
        policy: LearningPolicyService | None = None,
    ) -> None:
        self.context = context
        self.policy = policy or LearningPolicyService(context)
        self.decisions = LearningDecisionService(context)
        self.promotion = LearningPromotionService(context)

    @property
    def workspace_id(self) -> str:
        return self.context.workspace_id

    @property
    def journal(self):
        return self.context.journal

    def policy_status(self) -> LearningPolicyStatus:
        return self.policy.get_status()

    def status(self) -> LearningStatusView:
        policy = self.policy_status()
        return LearningStatusView(
            policy=policy.policy,
            persisted=policy.persisted,
            pending_reviews=self.context._query(
                lambda: self.journal.count_learning_reviews(
                    self.workspace_id, status=LearningReviewStatus.PENDING
                )
            ),
            running_reviews=self.context._query(
                lambda: self.journal.count_learning_reviews(
                    self.workspace_id, status=LearningReviewStatus.RUNNING
                )
            ),
            failed_reviews=self.context._query(
                lambda: self.journal.count_learning_reviews(
                    self.workspace_id, status=LearningReviewStatus.FAILED
                )
            ),
            proposed_candidates=self.context._query(
                lambda: self.journal.count_learning_candidates(
                    self.workspace_id, status=LearningCandidateStatus.PROPOSED
                )
            ),
        )

    def get_review(self, review_id: str) -> LearningReviewView | None:
        review = self.context._query(
            lambda: self.journal.get_learning_review(self.workspace_id, review_id)
        )
        return None if review is None else self._review_view(review)

    def list_reviews(
        self,
        *,
        status: LearningReviewStatus | str | None = None,
        task_outcome_id: str | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> QueryPage[LearningReviewView]:
        selected_status = self._review_status(status)
        offset = _offset(cursor, limit)
        reviews = self.context._query(
            lambda: self.journal.list_learning_reviews(
                self.workspace_id,
                status=selected_status,
                task_outcome_id=task_outcome_id,
                limit=min(500, offset + limit),
            )
        )
        page = tuple(self._review_view(review) for review in reviews[offset : offset + limit])
        next_cursor = str(offset + len(page)) if offset + len(page) < len(reviews) else None
        return QueryPage(page, next_cursor)

    def get_candidate(self, candidate_id: str) -> LearningCandidateView | None:
        candidate = self.context._query(
            lambda: self.journal.get_learning_candidate(self.workspace_id, candidate_id)
        )
        return None if candidate is None else self._candidate_view(candidate)

    def list_candidates(
        self,
        *,
        status: LearningCandidateStatus | str | None = None,
        candidate_type: LearningCandidateType | str | None = None,
        fingerprint: str | None = None,
        semantic_key: str | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> QueryPage[LearningCandidateSummary]:
        selected_status = self._candidate_status(status)
        selected_type = self._candidate_type(candidate_type)
        offset = _offset(cursor, limit)
        candidates = self.context._query(
            lambda: self.journal.list_learning_candidates(
                self.workspace_id,
                status=selected_status,
                candidate_type=selected_type,
                fingerprint=fingerprint,
                semantic_key=semantic_key,
                limit=min(500, offset + limit),
            )
        )
        page = tuple(
            LearningCandidateSummary.from_candidate(candidate)
            for candidate in candidates[offset : offset + limit]
        )
        next_cursor = str(offset + len(page)) if offset + len(page) < len(candidates) else None
        return QueryPage(page, next_cursor)

    def preview_candidate_decision(
        self,
        candidate_id: str,
        *,
        edit: CandidatePayload | dict[str, Any] | None = None,
        scope: LearningScope | str | None = None,
        conflict_resolution: LearningConflictResolution | str | None = None,
    ) -> LearningCandidateDecisionPreview:
        candidate = self.context._query(
            lambda: self.journal.get_learning_candidate(self.workspace_id, candidate_id)
        )
        if candidate is None:
            raise ApplicationError(ApplicationErrorCode.NOT_FOUND, "Learning Candidate is missing")
        selected_scope = self._scope(scope or candidate.proposed_scope)
        selected_resolution = self._conflict_resolution(conflict_resolution)
        payload = self._edited_payload(candidate, edit)
        semantic_key = self._payload_semantic_key(candidate, payload)
        after = self._preview_value(candidate, payload, selected_scope, semantic_key)
        before, available, reason, conflict, effective_resolution = self._target_decision(
            candidate,
            payload,
            selected_scope,
            semantic_key,
            selected_resolution,
        )
        if candidate.status is not LearningCandidateStatus.PROPOSED:
            available = False
            reason = "candidate_not_proposed"
        elif candidate.expires_at <= _now(self.context):
            available = False
            reason = "candidate_expired"
        if self._is_suppressed(candidate, semantic_key):
            available = False
            reason = "candidate_suppressed"
        return LearningCandidateDecisionPreview(
            candidate=LearningCandidateSummary.from_candidate(candidate),
            decision_kind=(
                LearningCandidateDecisionKind.EDIT_AND_ACCEPT
                if edit is not None
                else LearningCandidateDecisionKind.ACCEPT
            ),
            expected_row_version=candidate.row_version,
            scope=selected_scope,
            conflict_resolution=effective_resolution,
            before=before,
            after=after,
            available=available,
            reason=reason,
            conflict=conflict,
        )

    def reject_candidate(self, command):
        return self.decisions.reject_candidate(command)

    def expire_candidates(self, command):
        return self.decisions.expire_candidates(command)

    def accept_candidate(self, command):
        return self.promotion.accept_candidate(command)

    def edit_and_accept_candidate(self, command):
        return self.promotion.edit_and_accept_candidate(command)

    def _review_view(self, review: LearningReview) -> LearningReviewView:
        return LearningReviewView(
            review_id=review.review_id,
            task_run_id=review.task_run_id,
            task_outcome_id=review.task_outcome_id,
            review_version=review.review_version,
            trigger=review.trigger,
            status=review.status,
            attempt_count=review.attempt_count,
            row_version=review.row_version,
            created_at=review.created_at,
            started_at=review.started_at,
            completed_at=review.completed_at,
            failure_code=review.failure_code,
            candidate_count=self.context._query(
                lambda: self.journal.count_learning_candidates(
                    self.workspace_id, origin_review_id=review.review_id
                )
            ),
            evidence_count=self.context._query(
                lambda: self.journal.count_learning_evidence(
                    self.workspace_id, review_id=review.review_id
                )
            ),
        )

    def _candidate_view(self, candidate: LearningCandidate) -> LearningCandidateView:
        evidence = self.context._query(
            lambda: self.journal.list_learning_candidate_evidence(
                self.workspace_id, candidate.candidate_id
            )
        )[:LEARNING_MAX_REFERENCE_IDS]
        conflicts: list[LearningCandidateSummary] = []
        for conflict_id in candidate.conflict_refs[:LEARNING_MAX_REFERENCE_IDS]:
            conflict = self.context._query(
                lambda conflict_id=conflict_id: self.journal.get_learning_candidate(
                    self.workspace_id, conflict_id
                )
            )
            if conflict is not None:
                conflicts.append(LearningCandidateSummary.from_candidate(conflict))
        duplicate = None
        if candidate.duplicate_of_id is not None:
            duplicate_candidate = self.context._query(
                lambda: self.journal.get_learning_candidate(
                    self.workspace_id, candidate.duplicate_of_id
                )
            )
            if duplicate_candidate is not None:
                duplicate = LearningCandidateSummary.from_candidate(duplicate_candidate)
        semantic_suppressions = self.context._query(
            lambda: self.journal.list_learning_suppressions(
                self.workspace_id,
                candidate_type=candidate.candidate_type.value,
                scope=candidate.proposed_scope,
                semantic_key=candidate.semantic_key,
                limit=LEARNING_MAX_REFERENCE_IDS,
            )
        )
        exact_suppressions = self.context._query(
            lambda: self.journal.list_learning_suppressions(
                self.workspace_id,
                candidate_type=candidate.candidate_type.value,
                scope=candidate.proposed_scope,
                fingerprint=candidate.fingerprint,
                limit=LEARNING_MAX_REFERENCE_IDS,
            )
        )
        suppressions = self._merge_suppressions(semantic_suppressions, exact_suppressions)
        return LearningCandidateView(
            candidate=candidate,
            evidence=tuple(LearningEvidenceSummary.from_evidence(item) for item in evidence),
            conflicts=tuple(conflicts),
            duplicate_of=duplicate,
            suppressions=tuple(
                LearningSuppressionSummary(
                    suppression_id=item.suppression_id,
                    candidate_type=item.candidate_type,
                    scope=item.scope,
                    semantic_key=item.semantic_key,
                    fingerprint=item.fingerprint,
                    status=item.status.value,
                    expires_at=item.expires_at,
                    row_version=item.row_version,
                )
                for item in suppressions
            ),
            target=self._target_summary(candidate.semantic_key, candidate.candidate_type),
        )

    def _target_summary(
        self, semantic_key: str, candidate_type: LearningCandidateType
    ) -> LearningTargetSummary:
        if candidate_type is not LearningCandidateType.PROJECT_KNOWLEDGE:
            return LearningTargetSummary(
                target_kind=candidate_type.value,
                semantic_key=semantic_key,
                available=False,
                reason="active_target_owned_by_later_subplan",
            )
        head = self.context._query(
            lambda: self.journal.get_project_knowledge_head_by_key(self.workspace_id, semantic_key)
        )
        if head is None:
            return LearningTargetSummary(
                target_kind="project_knowledge",
                semantic_key=semantic_key,
                available=True,
                reason="no_current_head",
            )
        revision = None
        if head.current_revision_id is not None:
            revision = self.context._query(
                lambda: self.journal.get_project_knowledge_revision(
                    self.workspace_id, head.current_revision_id
                )
            )
        return LearningTargetSummary(
            target_kind="project_knowledge",
            semantic_key=semantic_key,
            available=head.status is ProjectKnowledgeStatus.ACTIVE,
            status=head.status,
            revision=revision.revision if revision is not None else None,
            revision_id=revision.knowledge_revision_id if revision is not None else None,
            statement=revision.statement if revision is not None else None,
            statement_digest=revision.statement_digest if revision is not None else None,
            reason=None if revision is not None else "current_revision_missing",
        )

    def _target_decision(
        self,
        candidate: LearningCandidate,
        payload: CandidatePayload,
        scope: LearningScope,
        semantic_key: str,
        resolution: LearningConflictResolution,
    ) -> tuple[
        LearningPreviewValue | None,
        bool,
        str | None,
        str | None,
        LearningConflictResolution,
    ]:
        after_kind = candidate.candidate_type.value
        if (
            candidate.candidate_type is LearningCandidateType.PREFERENCE
            or candidate.candidate_type is LearningCandidateType.PROFILE
        ):
            return None, False, "configuration_promotion_deferred", None, resolution
        if candidate.candidate_type is not LearningCandidateType.PROJECT_KNOWLEDGE:
            return None, True, "candidate_only_acceptance", None, resolution
        head = self.context._query(
            lambda: self.journal.get_project_knowledge_head_by_key(self.workspace_id, semantic_key)
        )
        if head is None:
            return None, True, None, None, resolution
        revision = None
        if head.current_revision_id is not None:
            revision = self.context._query(
                lambda: self.journal.get_project_knowledge_revision(
                    self.workspace_id, head.current_revision_id
                )
            )
        before = None
        if revision is not None:
            before = LearningPreviewValue(
                target_kind=after_kind,
                semantic_key=semantic_key,
                scope=scope,
                value={"statement": revision.statement, "revision": revision.revision},
                value_digest=revision.statement_digest,
            )
        if not isinstance(payload, ProjectKnowledgeCandidatePayload):
            return before, False, "project_knowledge_payload_invalid", None, resolution
        if head.status is ProjectKnowledgeStatus.ACTIVE and revision is not None:
            if revision.statement == payload.statement:
                return before, True, None, "same_statement", LearningConflictResolution.CONFIRM
            if resolution in {
                LearningConflictResolution.REPLACE,
                LearningConflictResolution.MERGE,
            }:
                return before, True, None, "different_statement", resolution
            return (
                before,
                False,
                "knowledge_conflict_requires_resolution",
                "different_statement",
                resolution,
            )
        if head.status is ProjectKnowledgeStatus.DISABLED:
            return (
                before,
                resolution is LearningConflictResolution.RE_ENABLE,
                None
                if resolution is LearningConflictResolution.RE_ENABLE
                else "knowledge_disabled",
                "disabled_head",
                resolution,
            )
        if head.status is ProjectKnowledgeStatus.DISPUTED:
            return (
                before,
                resolution is LearningConflictResolution.RESOLVE_DISPUTE,
                None
                if resolution is LearningConflictResolution.RESOLVE_DISPUTE
                else "knowledge_disputed",
                "disputed_head",
                resolution,
            )
        return before, False, "knowledge_deleted", "deleted_head", resolution

    def _preview_value(
        self,
        candidate: LearningCandidate,
        payload: CandidatePayload,
        scope: LearningScope,
        semantic_key: str,
    ) -> LearningPreviewValue:
        value = payload.model_dump(mode="json")
        return LearningPreviewValue(
            target_kind=candidate.candidate_type.value,
            semantic_key=semantic_key,
            scope=scope,
            value=value,
            value_digest=sha256_digest(canonical_json_bytes(value)),
        )

    def _edited_payload(
        self, candidate: LearningCandidate, edit: CandidatePayload | dict[str, Any] | None
    ) -> CandidatePayload:
        if edit is None:
            return candidate.proposed_payload
        try:
            payload = (
                edit
                if not isinstance(edit, dict)
                else _CANDIDATE_PAYLOAD_ADAPTER.validate_python(edit)
            )
        except (TypeError, ValueError) as exc:
            raise ApplicationError(
                ApplicationErrorCode.INVALID, "edited candidate payload is invalid"
            ) from exc
        if payload.candidate_type is not candidate.candidate_type:
            raise ApplicationError(ApplicationErrorCode.INVALID, "edited candidate type is invalid")
        return payload

    @staticmethod
    def _payload_semantic_key(candidate: LearningCandidate, payload: CandidatePayload) -> str:
        return (
            payload.semantic_key
            if isinstance(payload, ProjectKnowledgeCandidatePayload)
            else candidate.semantic_key
        )

    def _is_suppressed(self, candidate: LearningCandidate, semantic_key: str) -> bool:
        now = _now(self.context)
        suppressions = self.context._query(
            lambda: self.journal.list_learning_suppressions(
                self.workspace_id,
                candidate_type=candidate.candidate_type.value,
                scope=candidate.proposed_scope,
                semantic_key=semantic_key,
                limit=LEARNING_MAX_REFERENCE_IDS,
            )
        ) + self.context._query(
            lambda: self.journal.list_learning_suppressions(
                self.workspace_id,
                candidate_type=candidate.candidate_type.value,
                scope=candidate.proposed_scope,
                fingerprint=candidate.fingerprint,
                limit=LEARNING_MAX_REFERENCE_IDS,
            )
        )
        return any(
            item.status.value == "active" and (item.expires_at is None or item.expires_at > now)
            for item in suppressions
        )

    @staticmethod
    def _merge_suppressions(first, second):
        merged = {item.suppression_id: item for item in first}
        merged.update({item.suppression_id: item for item in second})
        return tuple(sorted(merged.values(), key=lambda item: item.suppression_id))

    @staticmethod
    def _review_status(value: LearningReviewStatus | str | None) -> LearningReviewStatus | None:
        if value is None or isinstance(value, LearningReviewStatus):
            return value
        try:
            return LearningReviewStatus(value)
        except (TypeError, ValueError) as exc:
            raise ApplicationError(
                ApplicationErrorCode.INVALID, "Review status is invalid"
            ) from exc

    @staticmethod
    def _candidate_status(
        value: LearningCandidateStatus | str | None,
    ) -> LearningCandidateStatus | None:
        if value is None or isinstance(value, LearningCandidateStatus):
            return value
        try:
            return LearningCandidateStatus(value)
        except (TypeError, ValueError) as exc:
            raise ApplicationError(
                ApplicationErrorCode.INVALID, "Candidate status is invalid"
            ) from exc

    @staticmethod
    def _candidate_type(value: LearningCandidateType | str | None) -> LearningCandidateType | None:
        if value is None or isinstance(value, LearningCandidateType):
            return value
        try:
            return LearningCandidateType(value)
        except (TypeError, ValueError) as exc:
            raise ApplicationError(
                ApplicationErrorCode.INVALID, "Candidate type is invalid"
            ) from exc

    @staticmethod
    def _scope(value: LearningScope | str) -> LearningScope:
        if isinstance(value, LearningScope):
            return value
        try:
            return LearningScope(value)
        except (TypeError, ValueError) as exc:
            raise ApplicationError(
                ApplicationErrorCode.INVALID, "Learning scope is invalid"
            ) from exc

    @staticmethod
    def _conflict_resolution(
        value: LearningConflictResolution | str | None,
    ) -> LearningConflictResolution:
        if value is None:
            return LearningConflictResolution.NONE
        if isinstance(value, LearningConflictResolution):
            return value
        try:
            return LearningConflictResolution(value)
        except (TypeError, ValueError) as exc:
            raise ApplicationError(
                ApplicationErrorCode.INVALID, "Learning conflict resolution is invalid"
            ) from exc


__all__ = ["LearningApplicationService"]
