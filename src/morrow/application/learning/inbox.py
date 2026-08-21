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
    LearningCandidateOperation,
    LearningCandidateStatus,
    LearningCandidateType,
    LearningReview,
    LearningReviewStatus,
    LearningScope,
    is_positive_explicit_user_evidence,
)
from morrow.core.learning_commands import ExpireLearningCandidatesCommand
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
    """Focused Learning facade for bounded Inbox queries and decisions."""

    def __init__(
        self,
        context: ApplicationCommandContext,
        *,
        policy: LearningPolicyService | None = None,
        config_service=None,
    ) -> None:
        self.context = context
        self.policy = policy or LearningPolicyService(context)
        self.decisions = LearningDecisionService(context)
        self.promotion = LearningPromotionService(context, config_service=config_service)

    @property
    def workspace_id(self) -> str:
        return self.context.workspace_id

    @property
    def journal(self):
        return self.context.journal

    def policy_status(self) -> LearningPolicyStatus:
        return self.policy.get_status()

    def status(self) -> LearningStatusView:
        self._expire_due_candidates()
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
        self._expire_due_candidates()
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
        self._expire_due_candidates()
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
        decision_intent: LearningCandidateDecisionKind | str | None = None,
    ) -> LearningCandidateDecisionPreview:
        self._expire_due_candidates()
        candidate = self.context._query(
            lambda: self.journal.get_learning_candidate(self.workspace_id, candidate_id)
        )
        if candidate is None:
            raise ApplicationError(ApplicationErrorCode.NOT_FOUND, "Learning Candidate is missing")
        selected_intent = self._decision_intent(decision_intent)
        is_rejection = selected_intent in {
            LearningCandidateDecisionKind.REJECT,
            LearningCandidateDecisionKind.REJECT_AND_SUPPRESS,
        }
        if is_rejection:
            if edit is not None or scope is not None or conflict_resolution is not None:
                raise ApplicationError(
                    ApplicationErrorCode.INVALID,
                    "Reject preview does not support edit, scope, or conflict resolution",
                )
            scope_was_explicit = False
            selected_scope = candidate.proposed_scope
            selected_resolution = LearningConflictResolution.NONE
            payload = candidate.proposed_payload
        else:
            scope_was_explicit = scope is not None
            selected_scope = self._scope(scope or candidate.proposed_scope)
            selected_resolution = self._conflict_resolution(conflict_resolution)
            payload = self._edited_payload(candidate, edit)
        semantic_key = self._payload_semantic_key(candidate, payload)
        after = self._preview_value(candidate, payload, selected_scope, semantic_key)

        if is_rejection:
            available = True
            reason = None
            if candidate.status is not LearningCandidateStatus.PROPOSED:
                available = False
                reason = "candidate_not_proposed"
            elif candidate.expires_at <= _now(self.context):
                available = False
                reason = "candidate_expired"
            return LearningCandidateDecisionPreview(
                candidate=LearningCandidateSummary.from_candidate(candidate),
                decision_kind=selected_intent,
                expected_row_version=candidate.row_version,
                scope=selected_scope,
                conflict_resolution=selected_resolution,
                before=None,
                after=after,
                available=available,
                reason=reason,
                conflict=None,
                configuration_preview=(),
            )

        fingerprint = self._candidate_fingerprint(candidate, payload, selected_scope, semantic_key)
        configuration_preview: tuple[str, ...] = ()
        if candidate.candidate_type in {
            LearningCandidateType.PREFERENCE,
            LearningCandidateType.PROFILE,
        }:
            (
                before,
                available,
                reason,
                conflict,
                effective_resolution,
                configuration_preview,
                after,
            ) = self._configuration_decision(
                candidate,
                payload,
                selected_scope,
                selected_resolution,
                edit is not None,
                scope_was_explicit,
            )
        else:
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
        if self._has_duplicate(candidate, fingerprint):
            available = False
            reason = "candidate_duplicate"
        elif self._is_suppressed(candidate, selected_scope, semantic_key, fingerprint):
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
            configuration_preview=configuration_preview,
        )

    def reject_candidate(self, command):
        return self.decisions.reject_candidate(command)

    def expire_candidates(self, command):
        return self.decisions.expire_candidates(command)

    def accept_candidate(self, command):
        return self.promotion.accept_candidate(command)

    def edit_and_accept_candidate(self, command):
        return self.promotion.edit_and_accept_candidate(command)

    def list_promotion_operations(self, *, state=None):
        if state is None:
            return self.promotion.configuration.list_unresolved_operations()
        return self.promotion.configuration.list_operations(state=state)

    def get_promotion_operation(self, operation_id: str):
        return self.promotion.configuration.get_operation(operation_id)

    def recover_promotion_operation(self, operation_id: str, *, action: str):
        return self.promotion.configuration.recover_operation(operation_id, action=action)

    def preview_learning_undo(self, activation_id: str):
        return self.promotion.configuration.preview_undo(activation_id)

    def undo_learning_activation(self, activation_id: str, *, command_id: str):
        return self.promotion.configuration.undo_activation(
            activation_id,
            command_id=command_id,
        )

    def get_learning_activation(self, activation_id: str):
        return self.context._query(
            lambda: self.journal.get_configuration_activation(self.workspace_id, activation_id)
        )

    def list_learning_activations(self, *, target: str | None = None, path: str | None = None):
        return self.context._query(
            lambda: self.journal.list_configuration_activations(
                self.workspace_id,
                target=target,
                path=path,
                limit=500,
            )
        )

    def _expire_due_candidates(self, *, limit: int = LEARNING_QUERY_MAX_PAGE_SIZE) -> None:
        supports_writes = getattr(self.journal, "supports_writes", None)
        if supports_writes is not None and not supports_writes():
            return
        cutoff = _now(self.context)
        candidates = self.context._query(
            lambda: self.journal.list_learning_candidates(
                self.workspace_id,
                status=LearningCandidateStatus.PROPOSED,
                expires_before=cutoff,
                limit=limit,
            )
        )
        if not candidates:
            return
        command_id = (
            "cmd_"
            + sha256_digest(
                canonical_json_bytes(
                    [
                        "lazy-inbox-expiry",
                        self.workspace_id,
                        cutoff.isoformat(),
                        [candidate.candidate_id for candidate in candidates],
                    ]
                )
            )[:48]
        )
        self.decisions.expire_candidates(
            ExpireLearningCandidatesCommand(
                workspace_id=self.workspace_id,
                cutoff=cutoff,
                limit=limit,
                command_id=command_id,
            )
        )

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
        if head.category is not payload.category:
            return (
                before,
                False,
                "knowledge_category_conflict",
                "category_mismatch",
                resolution,
            )
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

    def _configuration_decision(
        self,
        candidate: LearningCandidate,
        payload: CandidatePayload,
        scope: LearningScope,
        resolution: LearningConflictResolution,
        edit: bool,
        scope_explicit: bool,
    ):
        configuration = self.promotion.configuration
        if configuration.config_service is None:
            return (
                None,
                False,
                "configuration_promotion_deferred",
                None,
                resolution,
                (),
                self._preview_value(candidate, payload, scope, candidate.semantic_key),
            )
        evidence = self.context._query(
            lambda: self.journal.list_learning_candidate_evidence(
                self.workspace_id, candidate.candidate_id
            )
        )[:LEARNING_MAX_REFERENCE_IDS]
        if (
            len(evidence) != len(candidate.evidence_ids)
            or {item.evidence_id for item in evidence} != set(candidate.evidence_ids)
            or any(
                item.workspace_id != self.workspace_id or item.safety_rejection_code is not None
                for item in evidence
            )
        ):
            return (
                None,
                False,
                "configuration_evidence_ineligible",
                None,
                resolution,
                (),
                self._preview_value(candidate, payload, scope, candidate.semantic_key),
            )
        if not any(is_positive_explicit_user_evidence(item) for item in evidence):
            return (
                None,
                False,
                "configuration_explicit_evidence_required",
                None,
                resolution,
                (),
                self._preview_value(candidate, payload, scope, candidate.semantic_key),
            )
        try:
            prepared = configuration.preview_candidate(
                candidate,
                final_payload=payload,
                scope=scope.value if scope_explicit else None,
                edit=edit,
            )
        except ApplicationError as exc:
            reason = {
                ApplicationErrorCode.CONFLICT: "configuration_conflict",
                ApplicationErrorCode.INVALID: "configuration_invalid",
                ApplicationErrorCode.NOT_FOUND: "configuration_target_missing",
                ApplicationErrorCode.NEEDS_RECOVERY: "configuration_needs_recovery",
            }.get(exc.code, "configuration_promotion_unavailable")
            return (
                None,
                False,
                reason,
                None,
                resolution,
                (),
                self._preview_value(candidate, payload, scope, candidate.semantic_key),
            )
        before = LearningPreviewValue(
            target_kind=candidate.candidate_type.value,
            semantic_key=candidate.semantic_key,
            scope=scope,
            value=None,
            value_digest=prepared.before_digest,
        )
        after = LearningPreviewValue(
            target_kind=candidate.candidate_type.value,
            semantic_key=candidate.semantic_key,
            scope=scope,
            value=prepared.command.model_dump(mode="json"),
            value_digest=prepared.after_digest,
        )
        return before, True, None, None, resolution, prepared.preview_lines, after

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

    def _is_suppressed(
        self,
        candidate: LearningCandidate,
        scope: LearningScope,
        semantic_key: str,
        fingerprint: str,
    ) -> bool:
        now = _now(self.context)
        suppressions = self.context._query(
            lambda: self.journal.list_learning_suppressions(
                self.workspace_id,
                candidate_type=candidate.candidate_type.value,
                scope=scope,
                semantic_key=semantic_key,
                limit=LEARNING_MAX_REFERENCE_IDS,
            )
        ) + self.context._query(
            lambda: self.journal.list_learning_suppressions(
                self.workspace_id,
                candidate_type=candidate.candidate_type.value,
                scope=scope,
                fingerprint=fingerprint,
                limit=LEARNING_MAX_REFERENCE_IDS,
            )
        )
        return any(
            item.status.value == "active" and (item.expires_at is None or item.expires_at > now)
            for item in suppressions
        )

    def _has_duplicate(self, candidate: LearningCandidate, fingerprint: str) -> bool:
        candidates = self.context._query(
            lambda: self.journal.list_learning_candidates(
                self.workspace_id,
                fingerprint=fingerprint,
                limit=LEARNING_MAX_REFERENCE_IDS,
            )
        )
        return any(
            item.candidate_id != candidate.candidate_id
            and item.status
            in {
                LearningCandidateStatus.PROPOSED,
                LearningCandidateStatus.PROMOTING,
                LearningCandidateStatus.ACCEPTED,
                LearningCandidateStatus.EDITED_AND_ACCEPTED,
            }
            for item in candidates
        )

    @staticmethod
    def _candidate_fingerprint(
        candidate: LearningCandidate,
        payload: CandidatePayload,
        scope: LearningScope,
        semantic_key: str,
    ) -> str:
        return LearningCandidate.fingerprint_for(
            candidate_type=candidate.candidate_type,
            scope=scope,
            semantic_key=semantic_key,
            operation=LearningCandidateOperation(candidate.operation),
            proposed_payload=payload,
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

    @staticmethod
    def _decision_intent(
        value: LearningCandidateDecisionKind | str | None,
    ) -> LearningCandidateDecisionKind:
        if value is None:
            return LearningCandidateDecisionKind.ACCEPT
        try:
            selected = (
                value
                if isinstance(value, LearningCandidateDecisionKind)
                else LearningCandidateDecisionKind(value)
            )
        except (TypeError, ValueError) as exc:
            raise ApplicationError(
                ApplicationErrorCode.INVALID, "Learning decision intent is invalid"
            ) from exc
        if selected not in {
            LearningCandidateDecisionKind.ACCEPT,
            LearningCandidateDecisionKind.REJECT,
            LearningCandidateDecisionKind.REJECT_AND_SUPPRESS,
        }:
            raise ApplicationError(
                ApplicationErrorCode.INVALID,
                "Learning preview only supports accept, reject, or reject_and_suppress",
            )
        return selected


__all__ = ["LearningApplicationService"]
