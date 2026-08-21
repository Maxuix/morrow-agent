"""Process-local asynchronous execution for durable Preference Review jobs."""

from __future__ import annotations

import asyncio
import math
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

    @property
    def job_id(self) -> str | None:
        return self.job.job_id if self.job is not None else None


class ReviewWorker:
    """Run one workspace's durable Preference queue without becoming a scheduler."""

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
        timeout_seconds: float = PREFERENCE_REVIEW_DEFAULT_TIMEOUT_SECONDS,
        lease_seconds: int = 120,
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
        self.workspace_id = workspace_id
        self.id_source = id_source
        self.clock = clock
        self.lease_seconds = lease_seconds
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
        if task is None or task.done():
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def drain_once(self) -> ReviewWorkerResult:
        """Claim and execute at most one pending or expired Preference Review job."""

        async with self._workspace_lock:
            candidates = self.repository.list_claimable_preference_review_jobs(
                self.workspace_id, limit=1
            )
            if not candidates:
                return ReviewWorkerResult(status="idle")
            candidate = candidates[0]
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

        retryable = code in {
            PreferenceReviewFailureCode.TIMEOUT,
            PreferenceReviewFailureCode.PROVIDER_UNAVAILABLE,
            PreferenceReviewFailureCode.MALFORMED_OUTPUT,
            PreferenceReviewFailureCode.LEASE_LOST,
            PreferenceReviewFailureCode.CANCELLED,
            PreferenceReviewFailureCode.PERSISTENCE,
        }
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
                result = await self.drain_once()
                if result.status != "completed":
                    break

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


__all__ = ["ReviewWorker", "ReviewWorkerResult"]
