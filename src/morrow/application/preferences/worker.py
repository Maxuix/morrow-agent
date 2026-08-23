"""Process-local asynchronous execution for durable Preference Review jobs."""

from __future__ import annotations

import asyncio
import math
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from pydantic import ValidationError

from morrow.application.preferences.context import PreferenceReviewContextError
from morrow.application.preferences.proposals import PreferenceProposalPipelineError
from morrow.application.preferences.reviewer import (
    PreferenceReviewRunner,
    PreferenceReviewRunResult,
)
from morrow.core.learning import LearningReviewStatus
from morrow.core.models import ModelErrorCode, ModelRef
from morrow.core.ports import IdSource
from morrow.core.preference_persistence_models import (
    PreferenceReviewFailureCode,
    PreferenceReviewJob,
    PreferenceReviewJobStatus,
)
from morrow.core.preference_review import PreferenceReviewerError
from morrow.core.store import StorageError, StorageErrorCode

PREFERENCE_REVIEW_MAX_ATTEMPTS = 3
PREFERENCE_REVIEW_RETRY_BACKOFF_SECONDS = (5, 15)
PREFERENCE_REVIEW_DEFAULT_TIMEOUT_SECONDS = 60.0
PREFERENCE_REVIEW_RETRYABLE_FAILURES = frozenset(
    {
        PreferenceReviewFailureCode.TIMEOUT,
        PreferenceReviewFailureCode.PROVIDER_UNAVAILABLE,
        PreferenceReviewFailureCode.MALFORMED_OUTPUT,
        PreferenceReviewFailureCode.LEASE_LOST,
        PreferenceReviewFailureCode.CANCELLED,
        PreferenceReviewFailureCode.PERSISTENCE,
    }
)


def _utc(clock: Callable[[], datetime]) -> datetime:
    value = clock()
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


@dataclass(frozen=True)
class ReviewWorkerResult:
    """Sanitized outcome of one worker drain attempt."""

    status: str
    job: PreferenceReviewJob | None = None
    proposal_count: int = 0
    duplicate_count: int = 0
    suppressed_count: int = 0
    error_code: str | None = None
    learning_review_id: str | None = None

    @property
    def job_id(self) -> str | None:
        return self.job.job_id if self.job is not None else None


@dataclass(frozen=True)
class ReviewWorkerNotice:
    """Sanitized process-local notice safe for rendering between foreground prompts."""

    kind: str
    job_id: str
    proposal_count: int = 0
    error_code: str | None = None


class ReviewWorker:
    """Run one workspace's durable Review queues without becoming a scheduler."""

    def __init__(
        self,
        *,
        journal,
        workspace_id: str,
        id_source: IdSource,
        clock: Callable[[], datetime],
        runner: PreferenceReviewRunner | None = None,
        reviewer=None,
        model: ModelRef | None = None,
        learning_runner=None,
        timeout_seconds: float = PREFERENCE_REVIEW_DEFAULT_TIMEOUT_SECONDS,
        lease_seconds: int = 120,
        retry_scheduler: Callable[[float, Callable[[], None]], object] | None = None,
    ) -> None:
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not math.isfinite(timeout_seconds)
            or timeout_seconds <= 0
            or timeout_seconds > 120
        ):
            raise ValueError("Preference Review timeout is outside the supported range")
        if isinstance(lease_seconds, bool) or not isinstance(lease_seconds, int):
            raise ValueError("Preference Review lease is invalid")
        if lease_seconds <= 0 or lease_seconds > 3_600:
            raise ValueError("Preference Review lease is outside the supported range")
        self.journal = journal
        self.repository = getattr(journal, "preference_journal", journal)
        self.learning_repository = journal
        self.workspace_id = workspace_id
        self.id_source = id_source
        self.clock = clock
        self.lease_seconds = lease_seconds
        self.retry_scheduler = retry_scheduler
        self.learning_runner = learning_runner
        self.runner = runner or PreferenceReviewRunner(
            journal=journal,
            workspace_id=workspace_id,
            id_source=id_source,
            clock=clock,
            reviewer=reviewer,
            model=model,
            timeout_seconds=timeout_seconds,
        )
        self._workspace_lock = asyncio.Lock()
        self._wake_event = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._stopping = False
        self._notices: deque[ReviewWorkerNotice] = deque()
        self._retry_handles: dict[str, object] = {}

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self) -> None:
        """Start the process-local wake loop; SQLite remains the queue authority."""

        if self.running:
            return
        self._stopping = False
        self._task = asyncio.create_task(self._run(), name=f"morrow-review-{self.workspace_id}")
        self.wake()

    def wake(self) -> None:
        """Signal that pending SQLite work may be available."""

        if not self._stopping:
            self._wake_event.set()

    async def stop(self) -> None:
        """Cancel only the in-process loop; a running lease remains recoverable by expiry."""

        self._stopping = True
        self._wake_event.set()
        task = self._task
        self._task = None
        for handle in self._retry_handles.values():
            cancel = getattr(handle, "cancel", None)
            if callable(cancel):
                cancel()
        self._retry_handles.clear()
        if task is None or task.done():
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def drain_once(self) -> ReviewWorkerResult:
        """Claim and execute at most one pending or expired Review item."""

        async with self._workspace_lock:
            candidates = self.repository.list_claimable_preference_review_jobs(
                self.workspace_id, limit=1
            )
            if candidates:
                candidate = candidates[0]
                if candidate.attempt_count >= PREFERENCE_REVIEW_MAX_ATTEMPTS:
                    result = self._exhaust_expired(candidate)
                else:
                    result = await self._drain_preference(candidate)
                self._record_notice(result)
                return result
            if self.learning_runner is not None and hasattr(
                self.learning_repository, "list_learning_reviews"
            ):
                pending = self.learning_repository.list_learning_reviews(
                    self.workspace_id,
                    status=LearningReviewStatus.PENDING,
                    limit=1,
                )
                if pending:
                    return await self._drain_learning(pending[0].review_id)
            return ReviewWorkerResult(status="idle")

    async def run_job(self, job_id: str) -> ReviewWorkerResult:
        """Claim and execute one explicit durable Preference job through the worker lifecycle."""

        async with self._workspace_lock:
            candidate = self.repository.get_preference_review_job(self.workspace_id, job_id)
            if candidate is None:
                raise StorageError(StorageErrorCode.NOT_FOUND, "Preference Review job is missing")
            now = _utc(self.clock)
            claimable = candidate.status is PreferenceReviewJobStatus.PENDING or (
                candidate.status is PreferenceReviewJobStatus.RUNNING
                and candidate.lease_expires_at is not None
                and candidate.lease_expires_at <= now
            )
            if not claimable:
                raise ValueError("Preference Review job is not claimable")
            if candidate.attempt_count >= PREFERENCE_REVIEW_MAX_ATTEMPTS:
                result = self._exhaust_expired(candidate)
            else:
                result = await self._drain_preference(candidate)
            self._record_notice(result)
            return result

    async def run_pending(self, *, limit: int = 100) -> tuple[ReviewWorkerResult, ...]:
        """Run a bounded one-shot drain; no task is left running after this returns."""

        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
            raise ValueError("Review pending-run limit is invalid")
        results: list[ReviewWorkerResult] = []
        for _ in range(limit):
            result = await self.drain_once()
            if result.status in {"idle", "busy"}:
                break
            results.append(result)
        return tuple(results)

    def retry(self, job_id: str) -> PreferenceReviewJob:
        """Reset one retryable terminal job to pending without running a hidden background task."""

        current = self.repository.get_preference_review_job(self.workspace_id, job_id)
        if current is None:
            raise StorageError(StorageErrorCode.NOT_FOUND, "Preference Review job is missing")
        if current.status not in {
            PreferenceReviewJobStatus.FAILED,
            PreferenceReviewJobStatus.EXHAUSTED,
        }:
            raise ValueError("only a failed or exhausted Preference Review can be retried")
        if current.failure_code not in PREFERENCE_REVIEW_RETRYABLE_FAILURES:
            raise ValueError("Preference Review failure is not retryable")
        pending = current.model_copy(
            update={
                "status": PreferenceReviewJobStatus.PENDING,
                "lease_id": None,
                "lease_expires_at": None,
                "attempt_count": 0,
                "started_at": None,
                "completed_at": None,
                "failure_code": None,
                "row_version": current.row_version + 1,
            }
        )
        return self.repository.save_preference_review_job(
            self.workspace_id,
            pending,
            expected_row_version=current.row_version,
        )

    def _exhaust_expired(self, current: PreferenceReviewJob) -> ReviewWorkerResult:
        now = _utc(self.clock)
        exhausted = current.model_copy(
            update={
                "status": PreferenceReviewJobStatus.EXHAUSTED,
                "lease_id": None,
                "lease_expires_at": None,
                "completed_at": max(now, current.created_at),
                "failure_code": PreferenceReviewFailureCode.LEASE_LOST,
                "row_version": current.row_version + 1,
            }
        )
        try:
            saved = self.repository.save_preference_review_job(
                self.workspace_id,
                exhausted,
                expected_row_version=current.row_version,
            )
        except StorageError as exc:
            return ReviewWorkerResult(status="busy", error_code=exc.code.value)
        return ReviewWorkerResult(
            status="exhausted",
            job=saved,
            error_code=PreferenceReviewFailureCode.LEASE_LOST.value,
        )

    def drain_notices(self) -> tuple[ReviewWorkerNotice, ...]:
        """Return and clear notices without exposing Reviewer context or raw model output."""

        notices = tuple(self._notices)
        self._notices.clear()
        return notices

    async def _drain_preference(self, candidate: PreferenceReviewJob) -> ReviewWorkerResult:
        """Claim and execute one Preference job while holding the workspace gate."""

        try:
            started_at = _utc(self.clock)
            claimed = self.repository.claim_preference_review_job(
                self.workspace_id,
                candidate.job_id,
                expected_row_version=candidate.row_version,
                lease_id=self.id_source.new_id("lease"),
                lease_expires_at=started_at + timedelta(seconds=self.lease_seconds),
                started_at=started_at,
            )
        except StorageError as exc:
            return ReviewWorkerResult(status="busy", error_code=exc.code.value)

        try:
            claimed = self._record_reviewer(claimed)
            result = await self.runner.run(claimed.job_id)
            completed = self._complete(claimed, result)
        except asyncio.CancelledError:
            try:
                self._record_failure(claimed, PreferenceReviewFailureCode.CANCELLED)
            except Exception:
                pass
            raise
        except Exception as exc:
            failure_code = self._failure_code(exc)
            try:
                current = self._record_failure(claimed, failure_code)
            except Exception:
                current = self._current_or_claimed(claimed)
                return ReviewWorkerResult(
                    status="deferred",
                    job=current,
                    error_code=PreferenceReviewFailureCode.PERSISTENCE.value,
                )
            terminal = current.status in {
                PreferenceReviewJobStatus.FAILED,
                PreferenceReviewJobStatus.EXHAUSTED,
            }
            return ReviewWorkerResult(
                status=(
                    "exhausted"
                    if current.status is PreferenceReviewJobStatus.EXHAUSTED
                    else "failed"
                    if terminal
                    else "deferred"
                ),
                job=current,
                error_code=failure_code.value,
            )
        return ReviewWorkerResult(
            status="completed",
            job=completed,
            proposal_count=len(result.proposals),
            duplicate_count=result.pipeline.duplicate_count,
            suppressed_count=result.pipeline.suppressed_count,
        )

    async def _drain_learning(self, review_id: str) -> ReviewWorkerResult:
        """Run one legacy Learning Review; its runner owns claim and attempt mutation."""

        try:
            result = await self.learning_runner.run(review_id)
        except asyncio.CancelledError:
            raise
        except Exception:
            return ReviewWorkerResult(
                status="deferred",
                learning_review_id=review_id,
                error_code="learning_review_unavailable",
            )
        review = result.review
        if review.status is LearningReviewStatus.COMPLETED:
            return ReviewWorkerResult(
                status="completed",
                learning_review_id=review.review_id,
                proposal_count=len(result.candidate_ids),
                duplicate_count=result.duplicate_count,
                suppressed_count=result.suppressed_count,
            )
        return ReviewWorkerResult(
            status="failed" if review.status is LearningReviewStatus.FAILED else "deferred",
            learning_review_id=review.review_id,
            error_code=(review.failure_code.value if review.failure_code is not None else None),
        )

    def _current_or_claimed(self, claimed: PreferenceReviewJob) -> PreferenceReviewJob:
        try:
            current = self.repository.get_preference_review_job(self.workspace_id, claimed.job_id)
        except Exception:
            current = None
        return current or claimed

    def _record_failure(
        self,
        claimed: PreferenceReviewJob,
        code: PreferenceReviewFailureCode,
    ) -> PreferenceReviewJob:
        current = self.repository.get_preference_review_job(self.workspace_id, claimed.job_id)
        if current is None:
            raise StorageError(StorageErrorCode.NOT_FOUND, "Preference Review job is missing")
        now = _utc(self.clock)
        if (
            current.status is not PreferenceReviewJobStatus.RUNNING
            or current.lease_id != claimed.lease_id
            or current.lease_expires_at is None
            or current.lease_expires_at <= now
        ):
            return current

        retryable = code in PREFERENCE_REVIEW_RETRYABLE_FAILURES
        if retryable and current.attempt_count < PREFERENCE_REVIEW_MAX_ATTEMPTS:
            delay = self._retry_delay(current.attempt_count)
            updated = current.model_copy(
                update={
                    "lease_expires_at": now + timedelta(seconds=delay),
                    "failure_code": None,
                    "row_version": current.row_version + 1,
                }
            )
        else:
            status = (
                PreferenceReviewJobStatus.EXHAUSTED
                if retryable
                else PreferenceReviewJobStatus.FAILED
            )
            updated = current.model_copy(
                update={
                    "status": status,
                    "lease_id": None,
                    "lease_expires_at": None,
                    "completed_at": max(now, current.created_at),
                    "failure_code": code,
                    "row_version": current.row_version + 1,
                }
            )
        return self.repository.save_preference_review_job(
            self.workspace_id,
            updated,
            expected_row_version=current.row_version,
        )

    @staticmethod
    def _retry_delay(attempt_count: int) -> int:
        index = max(0, min(attempt_count - 1, len(PREFERENCE_REVIEW_RETRY_BACKOFF_SECONDS) - 1))
        return PREFERENCE_REVIEW_RETRY_BACKOFF_SECONDS[index]

    @staticmethod
    def _failure_code(exc: BaseException) -> PreferenceReviewFailureCode:
        if isinstance(exc, PreferenceReviewContextError):
            if exc.code == "context_budget":
                return PreferenceReviewFailureCode.CONTEXT_BUDGET
            if exc.code == "safety_rejected":
                return PreferenceReviewFailureCode.SAFETY_REJECTED
            return PreferenceReviewFailureCode.SNAPSHOT_INVALID
        if isinstance(exc, PreferenceReviewerError):
            if exc.category in {"request_budget", "response_budget", "request_measurement"}:
                return PreferenceReviewFailureCode.REQUEST_BUDGET
            if exc.category == "context_validation":
                return PreferenceReviewFailureCode.SNAPSHOT_INVALID
            if exc.code is ModelErrorCode.TIMEOUT:
                return PreferenceReviewFailureCode.TIMEOUT
            if exc.code is ModelErrorCode.INVALID_RESPONSE:
                return PreferenceReviewFailureCode.MALFORMED_OUTPUT
            return PreferenceReviewFailureCode.PROVIDER_UNAVAILABLE
        if isinstance(exc, PreferenceProposalPipelineError):
            if exc.code == "malformed_output":
                return PreferenceReviewFailureCode.MALFORMED_OUTPUT
            if exc.code in {"missing_reference", "source_mismatch", "evidence_invalid"}:
                return PreferenceReviewFailureCode.SNAPSHOT_INVALID
            return PreferenceReviewFailureCode.PERSISTENCE
        if isinstance(exc, ValidationError):
            return PreferenceReviewFailureCode.MALFORMED_OUTPUT
        if isinstance(exc, TimeoutError):
            return PreferenceReviewFailureCode.TIMEOUT
        if isinstance(exc, StorageError):
            if exc.code is StorageErrorCode.BUSY:
                return PreferenceReviewFailureCode.LEASE_LOST
            return PreferenceReviewFailureCode.PERSISTENCE
        if isinstance(exc, (ValueError, TypeError)):
            return PreferenceReviewFailureCode.SNAPSHOT_INVALID
        return PreferenceReviewFailureCode.PROVIDER_UNAVAILABLE

    async def _run(self) -> None:
        while not self._stopping:
            await self._wake_event.wait()
            self._wake_event.clear()
            while not self._stopping:
                try:
                    result = await self.drain_once()
                except Exception:
                    break
                if result.status == "deferred" and result.job is not None:
                    self._schedule_retry(result.job)
                if result.status in {"idle", "busy"}:
                    break

    def _schedule_retry(self, job: PreferenceReviewJob) -> None:
        lease_expires_at = job.lease_expires_at
        if lease_expires_at is None:
            return
        delay = max(0.0, (lease_expires_at - _utc(self.clock)).total_seconds())
        previous = self._retry_handles.pop(job.job_id, None)
        cancel = getattr(previous, "cancel", None)
        if callable(cancel):
            cancel()

        def wake() -> None:
            self._retry_handles.pop(job.job_id, None)
            self.wake()

        scheduler = self.retry_scheduler
        handle = (
            scheduler(delay, wake)
            if scheduler is not None
            else asyncio.get_running_loop().call_later(delay, wake)
        )
        self._retry_handles[job.job_id] = handle

    def _record_notice(self, result: ReviewWorkerResult) -> None:
        job_id = result.job_id
        if job_id is None:
            return
        if result.status == "completed" and result.proposal_count:
            self._notices.append(
                ReviewWorkerNotice(
                    kind="proposals",
                    job_id=job_id,
                    proposal_count=result.proposal_count,
                )
            )
        elif result.status == "exhausted":
            self._notices.append(
                ReviewWorkerNotice(
                    kind="exhausted",
                    job_id=job_id,
                    error_code=result.error_code,
                )
            )

    def _record_reviewer(self, claimed: PreferenceReviewJob) -> PreferenceReviewJob:
        current = self.repository.get_preference_review_job(self.workspace_id, claimed.job_id)
        self._assert_lease(current, claimed)
        assert current is not None
        reviewer = self.runner.reviewer
        model = self.runner.model
        updated = current.model_copy(
            update={
                "reviewer_provider_id": str(model.provider_id)[:128],
                "reviewer_model_id": str(model.model_id)[:128],
                "reviewer_prompt_version": str(
                    getattr(reviewer, "prompt_version", current.reviewer_prompt_version)
                )[:64],
                "reviewer_schema_version": str(
                    getattr(reviewer, "schema_version", current.reviewer_schema_version)
                )[:64],
                "row_version": current.row_version + 1,
            }
        )
        return self.repository.save_preference_review_job(
            self.workspace_id,
            updated,
            expected_row_version=current.row_version,
        )

    def _complete(
        self,
        claimed: PreferenceReviewJob,
        result: PreferenceReviewRunResult,
    ) -> PreferenceReviewJob:
        current = self.repository.get_preference_review_job(self.workspace_id, claimed.job_id)
        self._assert_lease(current, claimed)
        assert current is not None
        completed = current.model_copy(
            update={
                "status": PreferenceReviewJobStatus.COMPLETED,
                "lease_id": None,
                "lease_expires_at": None,
                "completed_at": _utc(self.clock),
                "failure_code": None,
                "row_version": current.row_version + 1,
            }
        )
        return self.repository.save_preference_review_job(
            self.workspace_id,
            completed,
            expected_row_version=current.row_version,
        )

    def _assert_lease(
        self, current: PreferenceReviewJob | None, claimed: PreferenceReviewJob
    ) -> None:
        now = _utc(self.clock)
        if (
            current is None
            or current.status is not PreferenceReviewJobStatus.RUNNING
            or current.lease_id is None
            or current.lease_id != claimed.lease_id
            or current.lease_expires_at is None
            or current.lease_expires_at <= now
        ):
            raise StorageError(StorageErrorCode.BUSY, "Preference Review lease is lost")


__all__ = [
    "PREFERENCE_REVIEW_DEFAULT_TIMEOUT_SECONDS",
    "PREFERENCE_REVIEW_MAX_ATTEMPTS",
    "PREFERENCE_REVIEW_RETRYABLE_FAILURES",
    "PREFERENCE_REVIEW_RETRY_BACKOFF_SECONDS",
    "ReviewWorker",
    "ReviewWorkerNotice",
    "ReviewWorkerResult",
]
