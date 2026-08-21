"""SQLite repository methods for Preference Review jobs and Evidence."""

from __future__ import annotations

from datetime import datetime

from morrow.adapters.state.preference_journal_codec import (
    _EVIDENCE_COLUMNS,
    _JOB_COLUMNS,
    _evidence_from_row,
    _job_from_row,
    _missing,
    _optional_unix,
    _snapshot_json,
    _stale,
    _unix,
    _workspace_error,
)
from morrow.core.preference_persistence_models import (
    PreferenceEvidence,
    PreferenceReviewJob,
    PreferenceReviewJobStatus,
)
from morrow.core.store import StorageError, StorageErrorCode


class PreferenceReviewJournalMixin:
    """Review/Evidence persistence surface mixed into the public journal."""

    def _assert_turn_scope(self, workspace_id: str, turn_id: str) -> None:
        row = self.backend.read_one(
            """
            SELECT s.workspace_id FROM turns t
            JOIN sessions s ON s.session_id = t.session_id
            WHERE t.turn_id = ?
            """,
            (turn_id,),
        )
        if row is None:
            raise _missing("turn")
        if str(row[0]) != workspace_id:
            raise _workspace_error("turn")

    def _assert_session_scope(
        self, workspace_id: str, session_id: str | None, turn_id: str
    ) -> None:
        if session_id is None:
            return
        row = self.backend.read_one(
            """
            SELECT s.workspace_id, t.session_id
            FROM turns t JOIN sessions s ON s.session_id = t.session_id
            WHERE t.turn_id = ?
            """,
            (turn_id,),
        )
        if row is None:
            raise _missing("turn")
        if str(row[0]) != workspace_id:
            raise _workspace_error("session")
        if str(row[1]) != session_id:
            raise StorageError(
                StorageErrorCode.UNAVAILABLE, "Preference session does not match its turn"
            )

    def get_preference_review_job(
        self, workspace_id: str, job_id: str
    ) -> PreferenceReviewJob | None:
        row = self.backend.read_one(
            f"SELECT {_JOB_COLUMNS} FROM preference_review_jobs WHERE job_id = ?", (job_id,)
        )
        if row is None:
            return None
        if str(row[1]) != workspace_id:
            raise _workspace_error("Review job")
        return _job_from_row(row)

    def get_preference_review_job_for_turn(
        self, workspace_id: str, turn_id: str, *, review_version: int = 1
    ) -> PreferenceReviewJob | None:
        """Load the one replay key owned by a terminal Turn."""

        if isinstance(review_version, bool) or not isinstance(review_version, int):
            raise StorageError(StorageErrorCode.UNAVAILABLE, "Preference Review version is invalid")
        if review_version < 1:
            raise StorageError(StorageErrorCode.UNAVAILABLE, "Preference Review version is invalid")
        row = self.backend.read_one(
            f"SELECT {_JOB_COLUMNS} FROM preference_review_jobs "
            "WHERE workspace_id = ? AND turn_id = ? AND review_version = ?",
            (workspace_id, turn_id, review_version),
        )
        return _job_from_row(row) if row is not None else None

    def list_preference_review_jobs(
        self,
        workspace_id: str,
        *,
        status: PreferenceReviewJobStatus | None = None,
        limit: int = 100,
    ) -> tuple[PreferenceReviewJob, ...]:
        if not 1 <= limit <= 500:
            raise StorageError(StorageErrorCode.UNAVAILABLE, "Preference Review page is invalid")
        sql = f"SELECT {_JOB_COLUMNS} FROM preference_review_jobs WHERE workspace_id = ?"
        parameters: list[object] = [workspace_id]
        if status is not None:
            sql += " AND status = ?"
            parameters.append(status.value)
        sql += " ORDER BY created_at_unix ASC, job_id ASC LIMIT ?"
        parameters.append(limit)
        return tuple(_job_from_row(row) for row in self.backend.read_all(sql, tuple(parameters)))

    def list_claimable_preference_review_jobs(
        self, workspace_id: str, *, limit: int = 100
    ) -> tuple[PreferenceReviewJob, ...]:
        if not 1 <= limit <= 500:
            raise StorageError(StorageErrorCode.UNAVAILABLE, "Preference Review page is invalid")
        now_unix = int(self.backend.now().timestamp())
        rows = self.backend.read_all(
            f"SELECT {_JOB_COLUMNS} FROM preference_review_jobs "
            "WHERE workspace_id = ? AND ("
            "status = 'pending' OR "
            "(status = 'running' AND lease_expires_at_unix IS NOT NULL "
            "AND lease_expires_at_unix <= ?)"
            ") ORDER BY created_at_unix ASC, job_id ASC LIMIT ?",
            (workspace_id, now_unix, limit),
        )
        return tuple(_job_from_row(row) for row in rows)

    def count_preference_review_jobs(
        self, workspace_id: str, *, status: PreferenceReviewJobStatus | None = None
    ) -> int:
        sql = "SELECT COUNT(*) FROM preference_review_jobs WHERE workspace_id = ?"
        parameters: list[object] = [workspace_id]
        if status is not None:
            sql += " AND status = ?"
            parameters.append(status.value)
        row = self.backend.read_one(sql, tuple(parameters))
        return int(row[0]) if row is not None else 0

    def put_preference_review_job(
        self, workspace_id: str, job: PreferenceReviewJob
    ) -> PreferenceReviewJob:
        if job.workspace_id != workspace_id:
            raise _workspace_error("Review job")
        self._assert_turn_scope(workspace_id, job.turn_id)
        self._assert_session_scope(workspace_id, job.session_id, job.turn_id)
        snapshot_json, snapshot_bytes = _snapshot_json(job)

        def work() -> PreferenceReviewJob:
            if self.get_preference_review_job(workspace_id, job.job_id) is not None:
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE, "Preference Review job already exists"
                )
            self.backend.executor().execute(
                f"INSERT INTO preference_review_jobs({_JOB_COLUMNS}) VALUES ({','.join('?' for _ in range(24))})",
                (
                    job.job_id,
                    job.workspace_id,
                    job.session_id,
                    job.turn_id,
                    job.review_version,
                    job.status.value,
                    job.source_global_revision,
                    job.source_workspace_revision,
                    snapshot_json,
                    job.active_snapshot_count,
                    snapshot_bytes,
                    job.active_snapshot_digest,
                    job.reviewer_provider_id,
                    job.reviewer_model_id,
                    job.reviewer_prompt_version,
                    job.reviewer_schema_version,
                    job.lease_id,
                    _optional_unix(job.lease_expires_at),
                    job.attempt_count,
                    job.row_version,
                    _unix(job.created_at),
                    _optional_unix(job.started_at),
                    _optional_unix(job.completed_at),
                    job.failure_code.value if job.failure_code is not None else None,
                ),
            )
            loaded = self.get_preference_review_job(workspace_id, job.job_id)
            if loaded is None:
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE, "Preference Review job could not be read"
                )
            return loaded

        return self.backend.transact(work)

    def save_preference_review_job(
        self,
        workspace_id: str,
        job: PreferenceReviewJob,
        *,
        expected_row_version: int,
    ) -> PreferenceReviewJob:
        if job.workspace_id != workspace_id:
            raise _workspace_error("Review job")
        _snapshot_json(job)

        def work() -> PreferenceReviewJob:
            existing = self.get_preference_review_job(workspace_id, job.job_id)
            if existing is None:
                raise _missing("Review job")
            if (
                existing.row_version != expected_row_version
                or job.row_version != expected_row_version + 1
            ):
                raise _stale("Review job")
            immutable = (
                "workspace_id",
                "session_id",
                "turn_id",
                "review_version",
                "source_global_revision",
                "source_workspace_revision",
                "active_snapshot_json",
                "active_snapshot_count",
                "active_snapshot_bytes",
                "active_snapshot_digest",
                "created_at",
            )
            if any(getattr(existing, name) != getattr(job, name) for name in immutable):
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE, "Preference Review job identity is immutable"
                )
            self.backend.executor().execute(
                """
                UPDATE preference_review_jobs
                SET status = ?, reviewer_provider_id = ?, reviewer_model_id = ?,
                    reviewer_prompt_version = ?, reviewer_schema_version = ?, lease_id = ?,
                    lease_expires_at_unix = ?, attempt_count = ?, row_version = ?,
                    started_at_unix = ?, completed_at_unix = ?, failure_code = ?
                WHERE job_id = ? AND workspace_id = ? AND row_version = ?
                """,
                (
                    job.status.value,
                    job.reviewer_provider_id,
                    job.reviewer_model_id,
                    job.reviewer_prompt_version,
                    job.reviewer_schema_version,
                    job.lease_id,
                    _optional_unix(job.lease_expires_at),
                    job.attempt_count,
                    job.row_version,
                    _optional_unix(job.started_at),
                    _optional_unix(job.completed_at),
                    job.failure_code.value if job.failure_code is not None else None,
                    job.job_id,
                    workspace_id,
                    expected_row_version,
                ),
            )
            loaded = self.get_preference_review_job(workspace_id, job.job_id)
            if loaded is None or loaded.row_version != job.row_version:
                raise _stale("Review job")
            return loaded

        return self.backend.transact(work)

    def claim_preference_review_job(
        self,
        workspace_id: str,
        job_id: str,
        *,
        expected_row_version: int,
        lease_id: str,
        lease_expires_at: datetime,
        started_at: datetime,
    ) -> PreferenceReviewJob:
        def work() -> PreferenceReviewJob:
            existing = self.get_preference_review_job(workspace_id, job_id)
            if existing is None:
                raise _missing("Review job")
            now = self.backend.now()
            if existing.row_version != expected_row_version:
                raise _stale("Review job")
            if existing.status is PreferenceReviewJobStatus.RUNNING and (
                existing.lease_expires_at is not None and existing.lease_expires_at > now
            ):
                raise StorageError(StorageErrorCode.BUSY, "Preference Review job is leased")
            if existing.status in {
                PreferenceReviewJobStatus.COMPLETED,
                PreferenceReviewJobStatus.FAILED,
                PreferenceReviewJobStatus.EXHAUSTED,
                PreferenceReviewJobStatus.CANCELLED,
                PreferenceReviewJobStatus.SUPERSEDED,
            }:
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE, "Preference Review job is not claimable"
                )
            if existing.attempt_count >= 3:
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE,
                    "Preference Review retry limit is exhausted",
                )
            if lease_expires_at <= started_at:
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE, "Preference Review lease is invalid"
                )
            claimed = existing.model_copy(
                update={
                    "status": PreferenceReviewJobStatus.RUNNING,
                    "lease_id": lease_id,
                    "lease_expires_at": lease_expires_at,
                    "attempt_count": existing.attempt_count + 1,
                    "row_version": existing.row_version + 1,
                    "started_at": started_at,
                    "completed_at": None,
                    "failure_code": None,
                }
            )
            return self._save_preference_review_job_in_txn(
                workspace_id, claimed, expected_row_version=expected_row_version
            )

        return self.backend.transact(work)

    def _save_preference_review_job_in_txn(
        self,
        workspace_id: str,
        job: PreferenceReviewJob,
        *,
        expected_row_version: int,
    ) -> PreferenceReviewJob:
        existing = self.get_preference_review_job(workspace_id, job.job_id)
        if existing is None:
            raise _missing("Review job")
        if (
            existing.row_version != expected_row_version
            or job.row_version != expected_row_version + 1
        ):
            raise _stale("Review job")
        self.backend.executor().execute(
            """
            UPDATE preference_review_jobs
            SET status = ?, reviewer_provider_id = ?, reviewer_model_id = ?,
                reviewer_prompt_version = ?, reviewer_schema_version = ?, lease_id = ?,
                lease_expires_at_unix = ?, attempt_count = ?, row_version = ?,
                started_at_unix = ?, completed_at_unix = ?, failure_code = ?
            WHERE job_id = ? AND workspace_id = ? AND row_version = ?
            """,
            (
                job.status.value,
                job.reviewer_provider_id,
                job.reviewer_model_id,
                job.reviewer_prompt_version,
                job.reviewer_schema_version,
                job.lease_id,
                _optional_unix(job.lease_expires_at),
                job.attempt_count,
                job.row_version,
                _optional_unix(job.started_at),
                _optional_unix(job.completed_at),
                job.failure_code.value if job.failure_code is not None else None,
                job.job_id,
                workspace_id,
                expected_row_version,
            ),
        )
        loaded = self.get_preference_review_job(workspace_id, job.job_id)
        if loaded is None:
            raise _stale("Review job")
        return loaded

    def put_preference_evidence(
        self, workspace_id: str, evidence: PreferenceEvidence
    ) -> PreferenceEvidence:
        if evidence.workspace_id != workspace_id:
            raise _workspace_error("Evidence")
        self._assert_turn_scope(workspace_id, evidence.turn_id)

        def work() -> PreferenceEvidence:
            job = self.get_preference_review_job(workspace_id, evidence.job_id)
            if job is None:
                raise _missing("Review job")
            if job.turn_id != evidence.turn_id:
                raise _workspace_error("Evidence turn")
            existing = self.get_preference_evidence(workspace_id, evidence.evidence_id)
            if existing is not None:
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE, "Preference Evidence already exists"
                )
            if (
                self.backend.read_one(
                    "SELECT evidence_id FROM preference_evidence WHERE job_id = ?",
                    (evidence.job_id,),
                )
                is not None
            ):
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE, "Preference Review job already has Evidence"
                )
            self.backend.executor().execute(
                f"INSERT INTO preference_evidence({_EVIDENCE_COLUMNS}) VALUES ({','.join('?' for _ in range(12))})",
                (
                    evidence.evidence_id,
                    evidence.workspace_id,
                    evidence.job_id,
                    evidence.turn_id,
                    evidence.source_kind,
                    evidence.actor,
                    evidence.excerpt_redacted,
                    evidence.excerpt_bytes,
                    evidence.content_digest,
                    evidence.safety_rejection_code.value
                    if evidence.safety_rejection_code is not None
                    else None,
                    _unix(evidence.observed_at),
                    _unix(evidence.created_at),
                ),
            )
            loaded = self.get_preference_evidence(workspace_id, evidence.evidence_id)
            if loaded is None:
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE, "Preference Evidence could not be read"
                )
            return loaded

        return self.backend.transact(work)

    def put_preference_job_with_evidence(
        self,
        workspace_id: str,
        job: PreferenceReviewJob,
        evidence: PreferenceEvidence,
    ) -> tuple[PreferenceReviewJob, PreferenceEvidence]:
        def work() -> tuple[PreferenceReviewJob, PreferenceEvidence]:
            stored_job = self.put_preference_review_job(workspace_id, job)
            stored_evidence = self.put_preference_evidence(workspace_id, evidence)
            return stored_job, stored_evidence

        return self.backend.transact(work)

    def get_preference_evidence(
        self, workspace_id: str, evidence_id: str
    ) -> PreferenceEvidence | None:
        row = self.backend.read_one(
            f"SELECT {_EVIDENCE_COLUMNS} FROM preference_evidence WHERE evidence_id = ?",
            (evidence_id,),
        )
        if row is None:
            return None
        if str(row[1]) != workspace_id:
            raise _workspace_error("Evidence")
        return _evidence_from_row(row)

    def get_preference_evidence_for_job(
        self, workspace_id: str, job_id: str
    ) -> PreferenceEvidence | None:
        row = self.backend.read_one(
            f"SELECT {_EVIDENCE_COLUMNS} FROM preference_evidence "
            "WHERE workspace_id = ? AND job_id = ?",
            (workspace_id, job_id),
        )
        return _evidence_from_row(row) if row is not None else None

    def list_preference_evidence(
        self, workspace_id: str, *, job_id: str | None = None, limit: int = 100
    ) -> tuple[PreferenceEvidence, ...]:
        if not 1 <= limit <= 500:
            raise StorageError(StorageErrorCode.UNAVAILABLE, "Preference Evidence page is invalid")
        sql = f"SELECT {_EVIDENCE_COLUMNS} FROM preference_evidence WHERE workspace_id = ?"
        parameters: list[object] = [workspace_id]
        if job_id is not None:
            sql += " AND job_id = ?"
            parameters.append(job_id)
        sql += " ORDER BY created_at_unix ASC, evidence_id ASC LIMIT ?"
        parameters.append(limit)
        return tuple(
            _evidence_from_row(row) for row in self.backend.read_all(sql, tuple(parameters))
        )


__all__ = ["PreferenceReviewJournalMixin"]
