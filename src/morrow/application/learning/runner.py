"""One-shot Review claiming, Reviewer execution, and candidate finalization."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from pydantic import ValidationError

from morrow.application.learning.candidate_pipeline import LearningCandidatePipeline
from morrow.application.learning.context import LearningContextBuilder, LearningEvidenceExtractor
from morrow.application.learning.events import LearningEventWriter
from morrow.application.learning.requests import policy_from_review
from morrow.core.application import ApplicationError, ApplicationErrorCode
from morrow.core.learning import (
    LEARNING_REVIEW_LEASE_SECONDS,
    CandidateDraftBatch,
    LearningReview,
    LearningReviewFailureCode,
    LearningReviewStatus,
)
from morrow.core.learning_ports import LearningReviewerError, LearningReviewerPort
from morrow.core.models import ModelRef, ProtocolModel
from morrow.core.ports import IdSource
from morrow.core.store import StorageError


class LearningReviewRunResult(ProtocolModel):
    """Bounded result returned by exactly one foreground Review attempt."""

    review: LearningReview
    candidate_ids: tuple[str, ...] = ()
    duplicate_count: int = 0
    suppressed_count: int = 0
    rejected_count: int = 0
    repair_used: bool = False


class DeterministicLearningReviewer:
    """Production fallback which never invents a candidate without a Reviewer."""

    async def review(
        self, context, *, model: ModelRef, timeout_seconds: float
    ) -> CandidateDraftBatch:
        del context, model, timeout_seconds
        return CandidateDraftBatch()


class LearningReviewRunner:
    """Execute one Review without holding a write transaction across an await."""

    def __init__(
        self,
        *,
        journal,
        workspace_id: str,
        id_source: IdSource,
        clock: Callable[[], datetime],
        reviewer: LearningReviewerPort | None = None,
        model: ModelRef | None = None,
        timeout_seconds: float = 15.0,
    ) -> None:
        if timeout_seconds <= 0 or timeout_seconds > 120:
            raise ValueError("learning Reviewer timeout is outside the supported range")
        self.journal = journal
        self.workspace_id = workspace_id
        self.id_source = id_source
        self.clock = clock
        self.reviewer = reviewer or DeterministicLearningReviewer()
        self.model = model or ModelRef(provider_id="deterministic", model_id="stage5-v1")
        self.timeout_seconds = timeout_seconds
        self.events = LearningEventWriter(
            workspace_id=workspace_id,
            id_source=id_source,
            clock=clock,
        )
        self.extractor = LearningEvidenceExtractor(
            journal=journal,
            workspace_id=workspace_id,
            id_source=id_source,
            clock=clock,
        )
        self.contexts = LearningContextBuilder(journal=journal, workspace_id=workspace_id)
        self.candidates = LearningCandidatePipeline(
            journal=journal,
            workspace_id=workspace_id,
            id_source=id_source,
            clock=clock,
            events=self.events,
        )

    async def run(
        self,
        review_id: str,
        *,
        expected_row_version: int | None = None,
    ) -> LearningReviewRunResult:
        claimed = self._claim(review_id, expected_row_version=expected_row_version)
        try:
            claimed = self._record_reviewer(claimed)
            outcome = self.journal.get_task_outcome(self.workspace_id, claimed.task_outcome_id)
            if outcome is None or outcome.task_run_id != claimed.task_run_id:
                raise ApplicationError(
                    ApplicationErrorCode.NEEDS_RECOVERY,
                    "Learning Review TaskOutcome is missing",
                )
            policy = policy_from_review(claimed)
            extracted = self.extractor.extract(claimed, outcome)[: policy.max_evidence_per_review]
            evidence = self.journal.transact(
                lambda txn: self.extractor.persist(
                    txn,
                    review=claimed,
                    evidence=extracted,
                )
            )
            try:
                context = self.contexts.build(
                    review=claimed,
                    outcome=outcome,
                    policy=policy,
                    evidence=evidence,
                )
            except (ValidationError, ValueError):
                return self._failed_result(claimed, LearningReviewFailureCode.INTERNAL)
            response = await asyncio.wait_for(
                self.reviewer.review(
                    context,
                    model=self.model,
                    timeout_seconds=self.timeout_seconds,
                ),
                timeout=self.timeout_seconds,
            )
            batch = CandidateDraftBatch.model_validate(response, strict=True)
        except asyncio.CancelledError:
            self._fail(claimed, LearningReviewFailureCode.CANCELLED)
            raise
        except TimeoutError:
            return self._failed_result(claimed, LearningReviewFailureCode.TIMEOUT)
        except ValidationError:
            return self._failed_result(claimed, LearningReviewFailureCode.INVALID_OUTPUT)
        except LearningReviewerError as exc:
            failure = {
                "timeout": LearningReviewFailureCode.TIMEOUT,
                "invalid_response": LearningReviewFailureCode.INVALID_OUTPUT,
            }.get(exc.code.value, LearningReviewFailureCode.PROVIDER_UNAVAILABLE)
            return self._failed_result(claimed, failure)
        except ApplicationError:
            return self._failed_result(claimed, LearningReviewFailureCode.INTERNAL)
        except StorageError:
            return self._failed_result(claimed, LearningReviewFailureCode.INTERNAL)
        except Exception:
            return self._failed_result(claimed, LearningReviewFailureCode.PROVIDER_UNAVAILABLE)
        return self._finalize(
            claimed,
            batch,
            allowed_evidence_ids=frozenset(item.evidence_id for item in context.evidence),
        )

    def _claim(self, review_id: str, *, expected_row_version: int | None) -> LearningReview:
        def work(txn):
            current = txn.get_learning_review(self.workspace_id, review_id)
            if current is None:
                raise ApplicationError(ApplicationErrorCode.NOT_FOUND, "Learning Review is missing")
            if expected_row_version is not None and current.row_version != expected_row_version:
                raise ApplicationError(ApplicationErrorCode.STALE, "Learning Review row is stale")
            now = self._now()
            claimed = txn.claim_learning_review(
                self.workspace_id,
                review_id,
                expected_row_version=current.row_version,
                lease_id=self.id_source.new_id("lease"),
                lease_expires_at=now + timedelta(seconds=LEARNING_REVIEW_LEASE_SECONDS),
                started_at=now,
            )
            self.events.put(
                txn,
                event_type="learning.review_started",
                aggregate_kind="learning_review",
                aggregate_id=claimed.review_id,
                payload={
                    "review_version": claimed.review_version,
                    "attempt_count": claimed.attempt_count,
                    "status": claimed.status.value,
                },
            )
            return claimed

        return self._translate(lambda: self.journal.transact(work))

    def _record_reviewer(self, claimed: LearningReview) -> LearningReview:
        provider_id = str(getattr(self.model, "provider_id", "deterministic"))[:128]
        model_id = str(getattr(self.model, "model_id", "stage5-v1"))[:128]
        prompt_version = str(
            getattr(self.reviewer, "prompt_version", claimed.reviewer_prompt_version)
        )[:64]
        schema_version = str(
            getattr(self.reviewer, "schema_version", claimed.reviewer_schema_version)
        )[:64]

        def work(txn):
            current = txn.get_learning_review(self.workspace_id, claimed.review_id)
            if current is None or current.lease_id != claimed.lease_id:
                raise ApplicationError(ApplicationErrorCode.STALE, "Learning Review lease is lost")
            updated = current.model_copy(
                update={
                    "reviewer_provider_id": provider_id,
                    "reviewer_model_id": model_id,
                    "reviewer_prompt_version": prompt_version,
                    "reviewer_schema_version": schema_version,
                    "row_version": current.row_version + 1,
                }
            )
            return txn.save_learning_review(
                self.workspace_id,
                updated,
                expected_row_version=current.row_version,
            )

        return self._translate(lambda: self.journal.transact(work))

    def cancel(
        self,
        review_id: str,
        *,
        expected_row_version: int | None = None,
    ) -> LearningReviewRunResult:
        """Release a currently owned foreground lease as a retryable cancellation."""

        current = self._translate(
            lambda: self.journal.get_learning_review(self.workspace_id, review_id)
        )
        if current is None:
            raise ApplicationError(ApplicationErrorCode.NOT_FOUND, "Learning Review is missing")
        if expected_row_version is not None and current.row_version != expected_row_version:
            raise ApplicationError(ApplicationErrorCode.STALE, "Learning Review row is stale")
        if current.status is not LearningReviewStatus.RUNNING:
            return LearningReviewRunResult(review=current)
        if (
            current.lease_id is None
            or current.lease_expires_at is None
            or current.lease_expires_at <= self._now()
        ):
            return LearningReviewRunResult(review=current)
        return self._failed_result(current, LearningReviewFailureCode.CANCELLED)

    def _finalize(
        self,
        claimed: LearningReview,
        batch: CandidateDraftBatch,
        *,
        allowed_evidence_ids: frozenset[str] | None = None,
    ) -> LearningReviewRunResult:
        def work(txn):
            current = txn.get_learning_review(self.workspace_id, claimed.review_id)
            if current is None:
                raise ApplicationError(ApplicationErrorCode.NOT_FOUND, "Learning Review is missing")
            self._assert_lease(current, claimed)
            outcome = txn.get_task_outcome(self.workspace_id, current.task_outcome_id)
            if outcome is None:
                raise ApplicationError(
                    ApplicationErrorCode.NEEDS_RECOVERY,
                    "Learning Review TaskOutcome is missing",
                )
            policy = policy_from_review(current)
            evidence = txn.list_learning_review_evidence(self.workspace_id, current.review_id)
            pipeline = self.candidates.persist(
                txn,
                current,
                outcome=outcome,
                policy=policy,
                evidence=evidence,
                batch=batch,
                allowed_evidence_ids=allowed_evidence_ids,
            )
            candidates = pipeline.candidates
            completed = current.model_copy(
                update={
                    "status": LearningReviewStatus.COMPLETED,
                    "lease_id": None,
                    "lease_expires_at": None,
                    "completed_at": self._now(),
                    "failure_code": None,
                    "row_version": current.row_version + 1,
                }
            )
            saved = txn.save_learning_review(
                self.workspace_id,
                completed,
                expected_row_version=current.row_version,
            )
            self.events.put(
                txn,
                event_type="learning.review_completed",
                aggregate_kind="learning_review",
                aggregate_id=saved.review_id,
                payload={
                    "review_version": saved.review_version,
                    "status": saved.status.value,
                    "candidate_count": len(candidates),
                    "duplicate_count": pipeline.duplicate_count,
                    "suppressed_count": pipeline.suppressed_count,
                    "rejected_count": pipeline.rejected_count,
                    "repair_used": self._repair_used(),
                },
            )
            return LearningReviewRunResult(
                review=saved,
                candidate_ids=tuple(candidate.candidate_id for candidate in candidates),
                duplicate_count=pipeline.duplicate_count,
                suppressed_count=pipeline.suppressed_count,
                rejected_count=pipeline.rejected_count,
                repair_used=self._repair_used(),
            )

        return self._translate(lambda: self.journal.transact(work))

    def _failed_result(
        self,
        claimed: LearningReview,
        code: LearningReviewFailureCode,
    ) -> LearningReviewRunResult:
        return LearningReviewRunResult(
            review=self._fail(claimed, code),
            repair_used=self._repair_used(),
        )

    def _fail(self, claimed: LearningReview, code: LearningReviewFailureCode) -> LearningReview:
        def work(txn):
            current = txn.get_learning_review(self.workspace_id, claimed.review_id)
            if current is None:
                raise ApplicationError(ApplicationErrorCode.NOT_FOUND, "Learning Review is missing")
            self._assert_lease(current, claimed)
            failed = current.model_copy(
                update={
                    "status": LearningReviewStatus.FAILED,
                    "lease_id": None,
                    "lease_expires_at": None,
                    "completed_at": self._now(),
                    "failure_code": code,
                    "row_version": current.row_version + 1,
                }
            )
            saved = txn.save_learning_review(
                self.workspace_id,
                failed,
                expected_row_version=current.row_version,
            )
            self.events.put(
                txn,
                event_type="learning.review_failed",
                aggregate_kind="learning_review",
                aggregate_id=saved.review_id,
                payload={
                    "review_version": saved.review_version,
                    "status": saved.status.value,
                    "failure_code": code.value,
                    "attempt_count": saved.attempt_count,
                    "repair_used": self._repair_used(),
                },
            )
            return saved

        return self._translate(lambda: self.journal.transact(work))

    def _repair_used(self) -> bool:
        return bool(getattr(self.reviewer, "last_repair_used", False))

    def _assert_lease(self, current: LearningReview, claimed: LearningReview) -> None:
        if (
            current.status is not LearningReviewStatus.RUNNING
            or current.lease_id is None
            or current.lease_id != claimed.lease_id
            or current.lease_expires_at is None
            or current.lease_expires_at <= self._now()
        ):
            raise ApplicationError(ApplicationErrorCode.STALE, "Learning Review lease is lost")

    def _now(self) -> datetime:
        value = self.clock()
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    @staticmethod
    def _translate(call):
        try:
            return call()
        except ApplicationError:
            raise
        except StorageError as exc:
            mapping = {
                "not_found": ApplicationErrorCode.NOT_FOUND,
                "busy": ApplicationErrorCode.BUSY,
            }
            code = mapping.get(exc.code.value, ApplicationErrorCode.UNAVAILABLE)
            raise ApplicationError(code, str(exc)) from exc


__all__ = [
    "DeterministicLearningReviewer",
    "LearningReviewRunResult",
    "LearningReviewRunner",
]
