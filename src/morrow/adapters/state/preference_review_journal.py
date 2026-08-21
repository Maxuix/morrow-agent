"""SQLite repository methods for Preference Review jobs and Evidence."""

from __future__ import annotations

from morrow.adapters.state.preference_journal_codec import (
    _EVIDENCE_COLUMNS,
    _JOB_COLUMNS,
    _evidence_from_row,
    _job_from_row,
    _missing,
    _optional_unix,
    _snapshot_json,
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
