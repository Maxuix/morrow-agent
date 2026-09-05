"""Learning candidate and suppression repository mixin for the v10 SQLite adapter."""

from __future__ import annotations

from datetime import UTC, datetime

from morrow.adapters.state.learning_journal import (
    _CANDIDATE_COLUMNS,
    _EVIDENCE_COLUMNS_QUALIFIED,
    _SUPPRESSION_COLUMNS,
    _candidate_from_row,
    _evidence_from_row,
    _missing,
    _optional_unix,
    _stale,
    _suppression_from_row,
    _unix,
    _workspace_error,
)
from morrow.adapters.state.transaction import SqliteJournalBackend
from morrow.core.domain import canonical_json_bytes
from morrow.core.learning import (
    LearningCandidate,
    LearningCandidateStatus,
    LearningCandidateType,
    LearningEvidence,
    LearningScope,
    LearningSuppression,
)
from morrow.core.store import StorageError, StorageErrorCode


class SqliteLearningCandidateMixin:
    """Persist proposed learning changes and their operator suppressions."""

    backend: SqliteJournalBackend

    @staticmethod
    def _assert_candidate_fingerprint(candidate: LearningCandidate) -> None:
        expected = LearningCandidate.fingerprint_for(
            candidate_type=candidate.candidate_type,
            scope=candidate.proposed_scope,
            semantic_key=candidate.semantic_key,
            operation=candidate.operation,
            proposed_payload=candidate.proposed_payload,
        )
        if candidate.fingerprint != expected:
            raise StorageError(
                StorageErrorCode.UNAVAILABLE,
                "learning candidate fingerprint does not match its proposal",
            )

    def get_learning_candidate(
        self, workspace_id: str, candidate_id: str
    ) -> LearningCandidate | None:
        row = self.backend.read_one(
            f"SELECT {_CANDIDATE_COLUMNS} FROM learning_candidates WHERE candidate_id = ?",
            (candidate_id,),
        )
        if row is None:
            return None
        if str(row[1]) != workspace_id:
            raise _workspace_error("candidate")
        return _candidate_from_row(row)

    def list_learning_candidates(
        self,
        workspace_id: str,
        *,
        status: LearningCandidateStatus | None = None,
        candidate_type: LearningCandidateType | None = None,
        origin_review_id: str | None = None,
        fingerprint: str | None = None,
        semantic_key: str | None = None,
        expires_before: datetime | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[LearningCandidate, ...]:
        if not 1 <= limit <= 500:
            raise StorageError(StorageErrorCode.UNAVAILABLE, "learning candidate page is invalid")
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            raise StorageError(StorageErrorCode.UNAVAILABLE, "page offset is invalid")
        sql = f"SELECT {_CANDIDATE_COLUMNS} FROM learning_candidates WHERE workspace_id = ?"
        parameters: list[object] = [workspace_id]
        if status is not None:
            sql += " AND status = ?"
            parameters.append(status.value)
        if candidate_type is not None:
            sql += " AND candidate_type = ?"
            parameters.append(candidate_type.value)
        if origin_review_id is not None:
            sql += " AND origin_review_id = ?"
            parameters.append(origin_review_id)
        if fingerprint is not None:
            sql += " AND fingerprint = ?"
            parameters.append(fingerprint)
        if semantic_key is not None:
            sql += " AND semantic_key = ?"
            parameters.append(semantic_key)
        if expires_before is not None:
            cutoff = expires_before
            if cutoff.tzinfo is None:
                cutoff = cutoff.replace(tzinfo=UTC)
            sql += " AND expires_at_unix <= ?"
            parameters.append(int(cutoff.timestamp()))
        sql += " ORDER BY created_at_unix ASC, candidate_id ASC LIMIT ? OFFSET ?"
        parameters.extend((limit, offset))
        return tuple(
            _candidate_from_row(row) for row in self.backend.read_all(sql, tuple(parameters))
        )

    def count_learning_candidates(
        self,
        workspace_id: str,
        *,
        status: LearningCandidateStatus | None = None,
        candidate_type: LearningCandidateType | None = None,
        origin_review_id: str | None = None,
        expires_after: datetime | None = None,
    ) -> int:
        sql = "SELECT COUNT(*) FROM learning_candidates WHERE workspace_id = ?"
        parameters: list[object] = [workspace_id]
        if status is not None:
            sql += " AND status = ?"
            parameters.append(status.value)
        if candidate_type is not None:
            sql += " AND candidate_type = ?"
            parameters.append(candidate_type.value)
        if origin_review_id is not None:
            sql += " AND origin_review_id = ?"
            parameters.append(origin_review_id)
        if expires_after is not None:
            sql += " AND expires_at_unix > ?"
            parameters.append(int(expires_after.timestamp()))
        row = self.backend.read_one(sql, tuple(parameters))
        if row is None:
            raise StorageError(
                StorageErrorCode.UNAVAILABLE, "learning candidate count could not be read"
            )
        return int(row[0])

    def put_learning_candidate(
        self, workspace_id: str, candidate: LearningCandidate
    ) -> LearningCandidate:
        if candidate.workspace_id != workspace_id:
            raise _workspace_error("candidate")
        self._assert_candidate_fingerprint(candidate)

        def work() -> LearningCandidate:
            if self.get_learning_candidate(workspace_id, candidate.candidate_id) is not None:
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE, "learning candidate already exists"
                )
            review = self.get_learning_review(workspace_id, candidate.origin_review_id)
            if review is None:
                raise _missing("candidate review")
            for evidence_id in candidate.evidence_ids:
                evidence = self.get_learning_evidence(workspace_id, evidence_id)
                if evidence is None:
                    raise _missing("candidate evidence")
                self._assert_candidate_evidence(workspace_id, candidate, evidence)
            self.backend.executor().execute(
                f"INSERT INTO learning_candidates({_CANDIDATE_COLUMNS}) VALUES ({','.join('?' for _ in range(25))})",
                self._candidate_values(candidate),
            )
            for evidence_id in candidate.evidence_ids:
                self.backend.executor().execute(
                    "INSERT INTO learning_candidate_evidence(workspace_id, candidate_id, evidence_id) VALUES (?, ?, ?)",
                    (workspace_id, candidate.candidate_id, evidence_id),
                )
            loaded = self.get_learning_candidate(workspace_id, candidate.candidate_id)
            if loaded is None:
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE, "learning candidate could not be read"
                )
            return loaded

        return self.backend.transact(work)

    @staticmethod
    def _candidate_values(candidate: LearningCandidate) -> tuple[object, ...]:
        payload = canonical_json_bytes(candidate.proposed_payload.model_dump(mode="json"))
        return (
            candidate.candidate_id,
            candidate.workspace_id,
            candidate.origin_review_id,
            candidate.candidate_type.value,
            candidate.operation.value,
            candidate.semantic_key,
            candidate.proposed_scope.value,
            payload.decode("utf-8"),
            len(payload),
            candidate.fingerprint,
            candidate.status.value,
            canonical_json_bytes(list(candidate.evidence_ids)).decode("utf-8"),
            candidate.confidence_band.value,
            canonical_json_bytes(list(candidate.confidence_basis)).decode("utf-8"),
            candidate.sensitivity.value,
            candidate.expected_target_revision,
            candidate.duplicate_of_id,
            candidate.supersedes_id,
            canonical_json_bytes(list(candidate.conflict_refs)).decode("utf-8"),
            _unix(candidate.expires_at),
            candidate.row_version,
            _unix(candidate.created_at),
            _optional_unix(candidate.resolved_at),
            candidate.resolved_by.value if candidate.resolved_by is not None else None,
            candidate.rejection_reason,
        )

    def save_learning_candidate(
        self,
        workspace_id: str,
        candidate: LearningCandidate,
        *,
        expected_row_version: int,
    ) -> LearningCandidate:
        if candidate.workspace_id != workspace_id:
            raise _workspace_error("candidate")
        self._assert_candidate_fingerprint(candidate)

        def work() -> LearningCandidate:
            existing = self.get_learning_candidate(workspace_id, candidate.candidate_id)
            if existing is None:
                raise _missing("candidate")
            if (
                existing.row_version != expected_row_version
                or candidate.row_version != expected_row_version + 1
            ):
                raise _stale("candidate")
            immutable = (
                "workspace_id",
                "origin_review_id",
                "candidate_type",
                "operation",
                "semantic_key",
                "proposed_scope",
                "proposed_payload",
                "fingerprint",
                "created_at",
            )
            if any(getattr(existing, name) != getattr(candidate, name) for name in immutable):
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE, "learning candidate identity is immutable"
                )
            if not set(existing.evidence_ids).issubset(candidate.evidence_ids):
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE, "learning candidate evidence is immutable"
                )
            for evidence_id in set(candidate.evidence_ids) - set(existing.evidence_ids):
                evidence = self.get_learning_evidence(workspace_id, evidence_id)
                if evidence is None:
                    raise _missing("candidate evidence")
                self._assert_candidate_evidence(workspace_id, candidate, evidence)
            values = self._candidate_values(candidate)
            self.backend.executor().execute(
                """
                UPDATE learning_candidates
                SET status = ?, evidence_ids_json = ?, confidence_band = ?,
                    confidence_basis_json = ?, sensitivity = ?, expected_target_revision = ?,
                    duplicate_of_id = ?, supersedes_id = ?, conflict_refs_json = ?,
                    expires_at_unix = ?, row_version = ?, resolved_at_unix = ?,
                    resolved_by = ?, rejection_reason = ?
                WHERE candidate_id = ? AND workspace_id = ? AND row_version = ?
                """,
                (
                    values[10],
                    values[11],
                    values[12],
                    values[13],
                    values[14],
                    values[15],
                    values[16],
                    values[17],
                    values[18],
                    values[19],
                    values[20],
                    values[22],
                    values[23],
                    values[24],
                    candidate.candidate_id,
                    workspace_id,
                    expected_row_version,
                ),
            )
            for evidence_id in set(candidate.evidence_ids) - set(existing.evidence_ids):
                self.backend.executor().execute(
                    "INSERT INTO learning_candidate_evidence(workspace_id, candidate_id, evidence_id) VALUES (?, ?, ?)",
                    (workspace_id, candidate.candidate_id, evidence_id),
                )
            loaded = self.get_learning_candidate(workspace_id, candidate.candidate_id)
            if loaded is None or loaded.row_version != candidate.row_version:
                raise _stale("candidate")
            return loaded

        return self.backend.transact(work)

    def link_learning_candidate_evidence(
        self,
        workspace_id: str,
        candidate_id: str,
        evidence_id: str,
        *,
        expected_row_version: int,
    ) -> LearningCandidate:
        def work() -> LearningCandidate:
            candidate = self.get_learning_candidate(workspace_id, candidate_id)
            if candidate is None:
                raise _missing("candidate evidence link")
            if candidate.row_version != expected_row_version:
                raise _stale("candidate")
            if candidate.status not in {
                LearningCandidateStatus.PROPOSED,
                LearningCandidateStatus.PROMOTING,
            }:
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE,
                    "resolved learning candidate cannot receive evidence",
                )
            evidence = self.get_learning_evidence(workspace_id, evidence_id)
            if evidence is None:
                raise _missing("candidate evidence link")
            self._assert_candidate_evidence(workspace_id, candidate, evidence)
            if evidence_id in candidate.evidence_ids:
                return candidate
            next_candidate = LearningCandidate.model_validate(
                {
                    **candidate.model_dump(),
                    "evidence_ids": (*candidate.evidence_ids, evidence_id),
                    "row_version": candidate.row_version + 1,
                }
            )
            return self.save_learning_candidate(
                workspace_id,
                next_candidate,
                expected_row_version=expected_row_version,
            )

        return self.backend.transact(work)

    def _assert_candidate_evidence(
        self,
        workspace_id: str,
        candidate: LearningCandidate,
        evidence: LearningEvidence,
    ) -> None:
        """Keep re-review evidence inside the same accepted Outcome boundary."""

        origin = self.get_learning_review(workspace_id, candidate.origin_review_id)
        evidence_origin = self.get_learning_review(workspace_id, evidence.origin_review_id)
        if origin is None or evidence_origin is None:
            raise _missing("candidate evidence review")
        if evidence_origin.task_outcome_id != origin.task_outcome_id:
            raise StorageError(
                StorageErrorCode.UNAVAILABLE,
                "candidate evidence must belong to the same task outcome",
            )
        if evidence.task_run_id != origin.task_run_id:
            raise StorageError(
                StorageErrorCode.UNAVAILABLE,
                "candidate evidence must belong to the same task run",
            )

    def list_learning_candidate_evidence(
        self, workspace_id: str, candidate_id: str
    ) -> tuple[LearningEvidence, ...]:
        rows = self.backend.read_all(
            f"SELECT {_EVIDENCE_COLUMNS_QUALIFIED} FROM learning_evidence e "
            "JOIN learning_candidate_evidence l ON l.evidence_id = e.evidence_id "
            "WHERE l.workspace_id = ? AND l.candidate_id = ? "
            "ORDER BY e.created_at_unix ASC, e.evidence_id ASC",
            (workspace_id, candidate_id),
        )
        return tuple(_evidence_from_row(row) for row in rows)

    def get_learning_suppression(
        self, workspace_id: str, suppression_id: str
    ) -> LearningSuppression | None:
        row = self.backend.read_one(
            f"SELECT {_SUPPRESSION_COLUMNS} FROM learning_suppressions WHERE suppression_id = ?",
            (suppression_id,),
        )
        if row is None:
            return None
        if str(row[1]) != workspace_id:
            raise _workspace_error("suppression")
        return _suppression_from_row(row)

    def list_learning_suppressions(
        self,
        workspace_id: str,
        *,
        candidate_type: str | None = None,
        scope: LearningScope | None = None,
        semantic_key: str | None = None,
        fingerprint: str | None = None,
        limit: int = 100,
    ) -> tuple[LearningSuppression, ...]:
        if not 1 <= limit <= 500:
            raise StorageError(StorageErrorCode.UNAVAILABLE, "learning suppression page is invalid")
        sql = f"SELECT {_SUPPRESSION_COLUMNS} FROM learning_suppressions WHERE workspace_id = ?"
        parameters: list[object] = [workspace_id]
        if candidate_type is not None:
            sql += " AND candidate_type = ?"
            parameters.append(candidate_type)
        if scope is not None:
            sql += " AND scope = ?"
            parameters.append(scope.value)
        if semantic_key is not None:
            sql += " AND semantic_key = ?"
            parameters.append(semantic_key)
        if fingerprint is not None:
            sql += " AND fingerprint = ?"
            parameters.append(fingerprint)
        sql += " ORDER BY created_at_unix ASC, suppression_id ASC LIMIT ?"
        parameters.append(limit)
        return tuple(
            _suppression_from_row(row) for row in self.backend.read_all(sql, tuple(parameters))
        )

    def put_learning_suppression(
        self, workspace_id: str, suppression: LearningSuppression
    ) -> LearningSuppression:
        if suppression.workspace_id != workspace_id:
            raise _workspace_error("suppression")

        def work() -> LearningSuppression:
            if self.get_learning_suppression(workspace_id, suppression.suppression_id) is not None:
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE, "learning suppression already exists"
                )
            if suppression.source_candidate_id is not None:
                source = self.get_learning_candidate(workspace_id, suppression.source_candidate_id)
                if source is None:
                    raise _missing("suppression source candidate")
            self.backend.executor().execute(
                f"INSERT INTO learning_suppressions({_SUPPRESSION_COLUMNS}) VALUES ({','.join('?' for _ in range(13))})",
                self._suppression_values(suppression),
            )
            loaded = self.get_learning_suppression(workspace_id, suppression.suppression_id)
            if loaded is None:
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE, "learning suppression could not be read"
                )
            return loaded

        return self.backend.transact(work)

    @staticmethod
    def _suppression_values(suppression: LearningSuppression) -> tuple[object, ...]:
        return (
            suppression.suppression_id,
            suppression.workspace_id,
            suppression.candidate_type.value,
            suppression.scope.value,
            suppression.semantic_key,
            suppression.fingerprint,
            suppression.source_candidate_id,
            suppression.reason,
            suppression.status.value,
            _optional_unix(suppression.expires_at),
            suppression.row_version,
            _unix(suppression.created_at),
            _unix(suppression.updated_at),
        )

    def save_learning_suppression(
        self,
        workspace_id: str,
        suppression: LearningSuppression,
        *,
        expected_row_version: int,
    ) -> LearningSuppression:
        if suppression.workspace_id != workspace_id:
            raise _workspace_error("suppression")

        def work() -> LearningSuppression:
            existing = self.get_learning_suppression(workspace_id, suppression.suppression_id)
            if existing is None:
                raise _missing("suppression")
            if (
                existing.row_version != expected_row_version
                or suppression.row_version != expected_row_version + 1
            ):
                raise _stale("suppression")
            immutable = (
                "workspace_id",
                "candidate_type",
                "scope",
                "semantic_key",
                "fingerprint",
                "source_candidate_id",
                "created_at",
            )
            if any(getattr(existing, name) != getattr(suppression, name) for name in immutable):
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE, "learning suppression identity is immutable"
                )
            self.backend.executor().execute(
                """
                UPDATE learning_suppressions
                SET reason = ?, status = ?, expires_at_unix = ?, row_version = ?,
                    updated_at_unix = ?
                WHERE suppression_id = ? AND workspace_id = ? AND row_version = ?
                """,
                (
                    suppression.reason,
                    suppression.status.value,
                    _optional_unix(suppression.expires_at),
                    suppression.row_version,
                    _unix(suppression.updated_at),
                    suppression.suppression_id,
                    workspace_id,
                    expected_row_version,
                ),
            )
            loaded = self.get_learning_suppression(workspace_id, suppression.suppression_id)
            if loaded is None or loaded.row_version != suppression.row_version:
                raise _stale("suppression")
            return loaded

        return self.backend.transact(work)
