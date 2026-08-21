"""Process-local asynchronous execution for durable Preference Review jobs."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from morrow.application.preferences.reviewer import (
    PreferenceReviewRunner,
    PreferenceReviewRunResult,
)
from morrow.core.models import ModelRef
from morrow.core.ports import IdSource
from morrow.core.preference_persistence_models import (
    PreferenceReviewJob,
    PreferenceReviewJobStatus,
)
from morrow.core.store import StorageError, StorageErrorCode


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
        timeout_seconds: float = 60.0,
        lease_seconds: int = 120,
    ) -> None:
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
                claimed = self.repository.claim_preference_review_job(
                    self.workspace_id,
                    candidate.job_id,
                    expected_row_version=candidate.row_version,
                    lease_id=self.id_source.new_id("lease"),
                    lease_expires_at=_utc(self.clock) + timedelta(seconds=self.lease_seconds),
                    started_at=_utc(self.clock),
                )
            except StorageError as exc:
                return ReviewWorkerResult(status="busy", error_code=exc.code.value)

            try:
                claimed = self._record_reviewer(claimed)
                result = await self.runner.run(claimed.job_id)
                completed = self._complete(claimed, result)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                current = self.repository.get_preference_review_job(
                    self.workspace_id, claimed.job_id
                )
                return ReviewWorkerResult(
                    status="deferred",
                    job=current or claimed,
                    error_code=(
                        exc.code.value if isinstance(exc, StorageError) else "review_failed"
                    ),
                )
            return ReviewWorkerResult(
                status="completed",
                job=completed,
                proposal_count=len(result.proposals),
                duplicate_count=result.pipeline.duplicate_count,
                suppressed_count=result.pipeline.suppressed_count,
            )

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
