"""v11 SQLite repositories for Learning decisions and Project Knowledge."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from morrow.adapters.state.transaction import SqliteJournalBackend
from morrow.core.learning_memory import (
    LearningCandidateDecision,
    MemoryWorkspaceState,
    ProjectKnowledgeCategory,
    ProjectKnowledgeEvidenceLink,
    ProjectKnowledgeHead,
    ProjectKnowledgeRevision,
    ProjectKnowledgeStatus,
)
from morrow.core.store import StorageError, StorageErrorCode

_DECISION_COLUMNS = (
    "decision_id, workspace_id, candidate_id, kind, actor, original_proposal_digest, "
    "final_proposal_json, final_proposal_bytes, scope, conflict_resolution, command_id, "
    "created_at_unix"
)
_HEAD_COLUMNS = (
    "knowledge_id, workspace_id, semantic_key, category, status, current_revision_id, "
    "row_version, created_at_unix, updated_at_unix"
)
_REVISION_COLUMNS = (
    "knowledge_revision_id, knowledge_id, workspace_id, revision, statement, statement_digest, "
    "source_candidate_id, source_decision_id, supersedes_revision_id, sensitivity, "
    "valid_from_unix, valid_until_unix, created_at_unix, last_confirmed_at_unix"
)


def _unix(value: datetime) -> int:
    return int(value.timestamp())


def _optional_unix(value: datetime | None) -> int | None:
    return None if value is None else _unix(value)


def _from_unix(value: object) -> datetime:
    return datetime.fromtimestamp(int(value), UTC)


def _from_optional_unix(value: object) -> datetime | None:
    return None if value is None else _from_unix(value)


def _workspace_error(label: str) -> StorageError:
    return StorageError(StorageErrorCode.UNAVAILABLE, f"learning {label} is outside the workspace")


def _missing(label: str) -> StorageError:
    return StorageError(StorageErrorCode.NOT_FOUND, f"learning {label} is missing")


def _stale(label: str) -> StorageError:
    return StorageError(StorageErrorCode.UNAVAILABLE, f"learning {label} row version is stale")


def _decision_from_row(row: tuple[object, ...]) -> LearningCandidateDecision:
    try:
        return LearningCandidateDecision(
            decision_id=str(row[0]),
            workspace_id=str(row[1]),
            candidate_id=str(row[2]),
            kind=str(row[3]),
            actor=str(row[4]),
            original_proposal_digest=str(row[5]),
            final_proposal_json=str(row[6]) if row[6] is not None else None,
            final_proposal_bytes=int(row[7]) if row[7] is not None else None,
            scope=str(row[8]),
            conflict_resolution=str(row[9]),
            command_id=str(row[10]),
            created_at=_from_unix(row[11]),
        )
    except (TypeError, ValueError, IndexError, OverflowError, json.JSONDecodeError) as exc:
        raise StorageError(
            StorageErrorCode.NEEDS_REPAIR, "learning candidate decision is invalid"
        ) from exc


def _head_from_row(row: tuple[object, ...]) -> ProjectKnowledgeHead:
    try:
        return ProjectKnowledgeHead(
            knowledge_id=str(row[0]),
            workspace_id=str(row[1]),
            semantic_key=str(row[2]),
            category=str(row[3]),
            status=str(row[4]),
            current_revision_id=str(row[5]) if row[5] is not None else None,
            row_version=int(row[6]),
            created_at=_from_unix(row[7]),
            updated_at=_from_unix(row[8]),
        )
    except (TypeError, ValueError, IndexError, OverflowError) as exc:
        raise StorageError(
            StorageErrorCode.NEEDS_REPAIR, "project knowledge head is invalid"
        ) from exc


def _revision_from_row(row: tuple[object, ...]) -> ProjectKnowledgeRevision:
    try:
        return ProjectKnowledgeRevision(
            knowledge_revision_id=str(row[0]),
            knowledge_id=str(row[1]),
            workspace_id=str(row[2]),
            revision=int(row[3]),
            statement=str(row[4]),
            statement_digest=str(row[5]),
            source_candidate_id=str(row[6]) if row[6] is not None else None,
            source_decision_id=str(row[7]) if row[7] is not None else None,
            supersedes_revision_id=str(row[8]) if row[8] is not None else None,
            sensitivity=str(row[9]),
            valid_from=_from_optional_unix(row[10]),
            valid_until=_from_optional_unix(row[11]),
            created_at=_from_unix(row[12]),
            last_confirmed_at=_from_unix(row[13]),
        )
    except (TypeError, ValueError, IndexError, OverflowError) as exc:
        raise StorageError(
            StorageErrorCode.NEEDS_REPAIR, "project knowledge revision is invalid"
        ) from exc


def _link_from_row(row: tuple[object, ...]) -> ProjectKnowledgeEvidenceLink:
    try:
        return ProjectKnowledgeEvidenceLink(
            workspace_id=str(row[0]),
            knowledge_revision_id=str(row[1]),
            evidence_id=str(row[2]),
        )
    except (TypeError, ValueError, IndexError) as exc:
        raise StorageError(
            StorageErrorCode.NEEDS_REPAIR, "project knowledge evidence link is invalid"
        ) from exc


def _state_from_row(row: tuple[object, ...]) -> MemoryWorkspaceState:
    try:
        return MemoryWorkspaceState(
            workspace_id=str(row[0]),
            memory_revision=int(row[1]),
            row_version=int(row[2]),
            updated_at=_from_unix(row[3]),
        )
    except (TypeError, ValueError, IndexError, OverflowError) as exc:
        raise StorageError(
            StorageErrorCode.NEEDS_REPAIR, "memory workspace state is invalid"
        ) from exc


class SqliteLearningMemoryJournal:
    """Small composition root for v11 immutable decisions and memory state."""

    def __init__(self, backend: SqliteJournalBackend) -> None:
        self.backend = backend

    def put_learning_candidate_decision(
        self, workspace_id: str, decision: LearningCandidateDecision
    ) -> LearningCandidateDecision:
        if decision.workspace_id != workspace_id:
            raise _workspace_error("candidate decision")

        def work() -> LearningCandidateDecision:
            if self.get_learning_candidate_decision(workspace_id, decision.decision_id) is not None:
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE, "learning candidate decision already exists"
                )
            candidate = self.backend.read_one(
                "SELECT workspace_id, fingerprint FROM learning_candidates WHERE candidate_id = ?",
                (decision.candidate_id,),
            )
            if candidate is None:
                raise _missing("candidate decision candidate")
            if str(candidate[0]) != workspace_id:
                raise _workspace_error("candidate decision candidate")
            if str(candidate[1]) != decision.original_proposal_digest:
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE,
                    "candidate decision proposal digest does not match candidate",
                )
            self.backend.executor().execute(
                f"INSERT INTO learning_candidate_decisions({_DECISION_COLUMNS}) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    decision.decision_id,
                    decision.workspace_id,
                    decision.candidate_id,
                    decision.kind.value,
                    decision.actor.value,
                    decision.original_proposal_digest,
                    decision.final_proposal_json,
                    decision.final_proposal_bytes,
                    decision.scope.value,
                    decision.conflict_resolution.value,
                    decision.command_id,
                    _unix(decision.created_at),
                ),
            )
            loaded = self.get_learning_candidate_decision(workspace_id, decision.decision_id)
            if loaded is None:
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE, "learning candidate decision could not be read"
                )
            return loaded

        return self.backend.transact(work)

    def get_learning_candidate_decision(
        self, workspace_id: str, decision_id: str
    ) -> LearningCandidateDecision | None:
        row = self.backend.read_one(
            f"SELECT {_DECISION_COLUMNS} FROM learning_candidate_decisions WHERE decision_id = ?",
            (decision_id,),
        )
        if row is None:
            return None
        if str(row[1]) != workspace_id:
            raise _workspace_error("candidate decision")
        return _decision_from_row(row)

    def list_learning_candidate_decisions(
        self,
        workspace_id: str,
        *,
        candidate_id: str | None = None,
        limit: int = 100,
    ) -> tuple[LearningCandidateDecision, ...]:
        _check_limit(limit, "candidate decision")
        sql = f"SELECT {_DECISION_COLUMNS} FROM learning_candidate_decisions WHERE workspace_id = ?"
        parameters: list[object] = [workspace_id]
        if candidate_id is not None:
            sql += " AND candidate_id = ?"
            parameters.append(candidate_id)
        sql += " ORDER BY created_at_unix ASC, decision_id ASC LIMIT ?"
        parameters.append(limit)
        return tuple(
            _decision_from_row(row) for row in self.backend.read_all(sql, tuple(parameters))
        )

    def get_project_knowledge_head(
        self, workspace_id: str, knowledge_id: str
    ) -> ProjectKnowledgeHead | None:
        row = self.backend.read_one(
            f"SELECT {_HEAD_COLUMNS} FROM project_knowledge_heads WHERE knowledge_id = ?",
            (knowledge_id,),
        )
        if row is None:
            return None
        if str(row[1]) != workspace_id:
            raise _workspace_error("knowledge head")
        return _head_from_row(row)

    def get_project_knowledge_head_by_key(
        self, workspace_id: str, semantic_key: str
    ) -> ProjectKnowledgeHead | None:
        row = self.backend.read_one(
            f"SELECT {_HEAD_COLUMNS} FROM project_knowledge_heads "
            "WHERE workspace_id = ? AND semantic_key = ?",
            (workspace_id, semantic_key),
        )
        return None if row is None else _head_from_row(row)

    def list_project_knowledge_heads(
        self,
        workspace_id: str,
        *,
        status: ProjectKnowledgeStatus | None = None,
        category: ProjectKnowledgeCategory | None = None,
        include_deleted: bool = False,
        limit: int = 100,
    ) -> tuple[ProjectKnowledgeHead, ...]:
        _check_limit(limit, "knowledge head")
        sql = f"SELECT {_HEAD_COLUMNS} FROM project_knowledge_heads WHERE workspace_id = ?"
        parameters: list[object] = [workspace_id]
        if status is not None:
            sql += " AND status = ?"
            parameters.append(status.value)
        elif not include_deleted:
            sql += " AND status != ?"
            parameters.append(ProjectKnowledgeStatus.DELETED.value)
        if category is not None:
            sql += " AND category = ?"
            parameters.append(category.value)
        sql += " ORDER BY semantic_key ASC, knowledge_id ASC LIMIT ?"
        parameters.append(limit)
        return tuple(_head_from_row(row) for row in self.backend.read_all(sql, tuple(parameters)))

    def put_project_knowledge_head(
        self, workspace_id: str, head: ProjectKnowledgeHead
    ) -> ProjectKnowledgeHead:
        if head.workspace_id != workspace_id:
            raise _workspace_error("knowledge head")

        def work() -> ProjectKnowledgeHead:
            if self.get_project_knowledge_head(workspace_id, head.knowledge_id) is not None:
                raise StorageError(StorageErrorCode.UNAVAILABLE, "knowledge head already exists")
            self._assert_current_revision(workspace_id, head)
            self.backend.executor().execute(
                f"INSERT INTO project_knowledge_heads({_HEAD_COLUMNS}) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    head.knowledge_id,
                    head.workspace_id,
                    head.semantic_key,
                    head.category.value,
                    head.status.value,
                    head.current_revision_id,
                    head.row_version,
                    _unix(head.created_at),
                    _unix(head.updated_at),
                ),
            )
            loaded = self.get_project_knowledge_head(workspace_id, head.knowledge_id)
            if loaded is None:
                raise StorageError(StorageErrorCode.UNAVAILABLE, "knowledge head could not be read")
            return loaded

        return self.backend.transact(work)

    def save_project_knowledge_head(
        self,
        workspace_id: str,
        head: ProjectKnowledgeHead,
        *,
        expected_row_version: int,
    ) -> ProjectKnowledgeHead:
        if head.workspace_id != workspace_id:
            raise _workspace_error("knowledge head")

        def work() -> ProjectKnowledgeHead:
            existing = self.get_project_knowledge_head(workspace_id, head.knowledge_id)
            if existing is None:
                raise _missing("knowledge head")
            if (
                existing.row_version != expected_row_version
                or head.row_version != expected_row_version + 1
            ):
                raise _stale("knowledge head")
            if (
                existing.workspace_id != head.workspace_id
                or existing.semantic_key != head.semantic_key
                or existing.category != head.category
                or existing.created_at != head.created_at
            ):
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE, "knowledge head identity is immutable"
                )
            if head.updated_at < existing.updated_at:
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE, "knowledge head timestamp is stale"
                )
            self._assert_current_revision(workspace_id, head)
            self.backend.executor().execute(
                """
                UPDATE project_knowledge_heads
                SET status = ?, current_revision_id = ?, row_version = ?, updated_at_unix = ?
                WHERE knowledge_id = ? AND workspace_id = ? AND row_version = ?
                """,
                (
                    head.status.value,
                    head.current_revision_id,
                    head.row_version,
                    _unix(head.updated_at),
                    head.knowledge_id,
                    workspace_id,
                    expected_row_version,
                ),
            )
            loaded = self.get_project_knowledge_head(workspace_id, head.knowledge_id)
            if loaded is None or loaded.row_version != head.row_version:
                raise _stale("knowledge head")
            return loaded

        return self.backend.transact(work)

    def get_project_knowledge_revision(
        self, workspace_id: str, revision_id: str
    ) -> ProjectKnowledgeRevision | None:
        row = self.backend.read_one(
            f"SELECT {_REVISION_COLUMNS} FROM project_knowledge_revisions "
            "WHERE knowledge_revision_id = ?",
            (revision_id,),
        )
        if row is None:
            return None
        if str(row[2]) != workspace_id:
            raise _workspace_error("knowledge revision")
        return _revision_from_row(row)

    def list_project_knowledge_revisions(
        self,
        workspace_id: str,
        knowledge_id: str,
        *,
        limit: int = 100,
    ) -> tuple[ProjectKnowledgeRevision, ...]:
        _check_limit(limit, "knowledge revision")
        rows = self.backend.read_all(
            f"SELECT {_REVISION_COLUMNS} FROM project_knowledge_revisions "
            "WHERE workspace_id = ? AND knowledge_id = ? "
            "ORDER BY revision ASC, knowledge_revision_id ASC LIMIT ?",
            (workspace_id, knowledge_id, limit),
        )
        return tuple(_revision_from_row(row) for row in rows)

    def put_project_knowledge_revision(
        self, workspace_id: str, revision: ProjectKnowledgeRevision
    ) -> ProjectKnowledgeRevision:
        if revision.workspace_id != workspace_id:
            raise _workspace_error("knowledge revision")

        def work() -> ProjectKnowledgeRevision:
            if (
                self.get_project_knowledge_revision(workspace_id, revision.knowledge_revision_id)
                is not None
            ):
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE, "knowledge revision already exists"
                )
            head = self.get_project_knowledge_head(workspace_id, revision.knowledge_id)
            if head is None:
                row = self.backend.read_one(
                    "SELECT workspace_id FROM project_knowledge_heads WHERE knowledge_id = ?",
                    (revision.knowledge_id,),
                )
                if row is not None and str(row[0]) != workspace_id:
                    raise _workspace_error("knowledge revision head")
                raise _missing("knowledge revision head")
            self._assert_revision_sources(workspace_id, revision)
            self.backend.executor().execute(
                f"INSERT INTO project_knowledge_revisions({_REVISION_COLUMNS}) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    revision.knowledge_revision_id,
                    revision.knowledge_id,
                    revision.workspace_id,
                    revision.revision,
                    revision.statement,
                    revision.statement_digest,
                    revision.source_candidate_id,
                    revision.source_decision_id,
                    revision.supersedes_revision_id,
                    revision.sensitivity.value,
                    _optional_unix(revision.valid_from),
                    _optional_unix(revision.valid_until),
                    _unix(revision.created_at),
                    _unix(revision.last_confirmed_at),
                ),
            )
            loaded = self.get_project_knowledge_revision(
                workspace_id, revision.knowledge_revision_id
            )
            if loaded is None:
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE, "knowledge revision could not be read"
                )
            return loaded

        return self.backend.transact(work)

    def confirm_project_knowledge_revision(
        self,
        workspace_id: str,
        revision_id: str,
        *,
        confirmed_at: datetime,
    ) -> ProjectKnowledgeRevision:
        def work() -> ProjectKnowledgeRevision:
            revision = self.get_project_knowledge_revision(workspace_id, revision_id)
            if revision is None:
                raise _missing("knowledge confirmation revision")
            stamp = confirmed_at
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=UTC)
            else:
                stamp = stamp.astimezone(UTC)
            if stamp < revision.last_confirmed_at:
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE, "knowledge confirmation timestamp is stale"
                )
            if stamp == revision.last_confirmed_at:
                return revision
            self.backend.executor().execute(
                """
                UPDATE project_knowledge_revisions
                SET last_confirmed_at_unix = ?
                WHERE knowledge_revision_id = ? AND workspace_id = ?
                """,
                (_unix(stamp), revision_id, workspace_id),
            )
            loaded = self.get_project_knowledge_revision(workspace_id, revision_id)
            if loaded is None:
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE, "knowledge confirmation could not be read"
                )
            return loaded

        return self.backend.transact(work)

    def put_project_knowledge_evidence(
        self, workspace_id: str, link: ProjectKnowledgeEvidenceLink
    ) -> ProjectKnowledgeEvidenceLink:
        if link.workspace_id != workspace_id:
            raise _workspace_error("knowledge evidence")

        def work() -> ProjectKnowledgeEvidenceLink:
            revision = self.get_project_knowledge_revision(workspace_id, link.knowledge_revision_id)
            if revision is None:
                raise _missing("knowledge evidence revision")
            evidence = self.backend.read_one(
                "SELECT workspace_id FROM learning_evidence WHERE evidence_id = ?",
                (link.evidence_id,),
            )
            if evidence is None:
                raise _missing("knowledge evidence")
            if str(evidence[0]) != workspace_id:
                raise _workspace_error("knowledge evidence")
            existing = self.backend.read_one(
                "SELECT workspace_id, knowledge_revision_id, evidence_id "
                "FROM project_knowledge_evidence "
                "WHERE knowledge_revision_id = ? AND evidence_id = ?",
                (link.knowledge_revision_id, link.evidence_id),
            )
            if existing is not None:
                if str(existing[0]) != workspace_id:
                    raise _workspace_error("knowledge evidence")
                return _link_from_row(existing)
            self.backend.executor().execute(
                "INSERT INTO project_knowledge_evidence("
                "workspace_id, knowledge_revision_id, evidence_id) VALUES (?, ?, ?)",
                (workspace_id, link.knowledge_revision_id, link.evidence_id),
            )
            return link

        return self.backend.transact(work)

    def list_project_knowledge_evidence(
        self, workspace_id: str, revision_id: str
    ) -> tuple[ProjectKnowledgeEvidenceLink, ...]:
        rows = self.backend.read_all(
            "SELECT workspace_id, knowledge_revision_id, evidence_id "
            "FROM project_knowledge_evidence "
            "WHERE workspace_id = ? AND knowledge_revision_id = ? "
            "ORDER BY evidence_id ASC",
            (workspace_id, revision_id),
        )
        return tuple(_link_from_row(row) for row in rows)

    def get_memory_workspace_state(self, workspace_id: str) -> MemoryWorkspaceState | None:
        row = self.backend.read_one(
            "SELECT workspace_id, memory_revision, row_version, updated_at_unix "
            "FROM memory_workspace_state WHERE workspace_id = ?",
            (workspace_id,),
        )
        return None if row is None else _state_from_row(row)

    def ensure_memory_workspace_state(self, workspace_id: str) -> MemoryWorkspaceState:
        def work() -> MemoryWorkspaceState:
            existing = self.get_memory_workspace_state(workspace_id)
            if existing is not None:
                return existing
            state = MemoryWorkspaceState(workspace_id=workspace_id, updated_at=self.backend.now())
            self.backend.executor().execute(
                "INSERT INTO memory_workspace_state("
                "workspace_id, memory_revision, row_version, updated_at_unix) VALUES (?, ?, ?, ?)",
                (
                    state.workspace_id,
                    state.memory_revision,
                    state.row_version,
                    _unix(state.updated_at),
                ),
            )
            loaded = self.get_memory_workspace_state(workspace_id)
            if loaded is None:
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE, "memory workspace state could not be read"
                )
            return loaded

        return self.backend.transact(work)

    def save_memory_workspace_state(
        self,
        workspace_id: str,
        state: MemoryWorkspaceState,
        *,
        expected_row_version: int,
    ) -> MemoryWorkspaceState:
        if state.workspace_id != workspace_id:
            raise _workspace_error("workspace memory state")

        def work() -> MemoryWorkspaceState:
            existing = self.get_memory_workspace_state(workspace_id)
            if existing is None:
                raise _missing("workspace memory state")
            if (
                existing.row_version != expected_row_version
                or state.row_version != expected_row_version + 1
            ):
                raise _stale("workspace memory state")
            if state.updated_at < existing.updated_at:
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE, "workspace memory state timestamp is stale"
                )
            self.backend.executor().execute(
                """
                UPDATE memory_workspace_state
                SET memory_revision = ?, row_version = ?, updated_at_unix = ?
                WHERE workspace_id = ? AND row_version = ?
                """,
                (
                    state.memory_revision,
                    state.row_version,
                    _unix(state.updated_at),
                    workspace_id,
                    expected_row_version,
                ),
            )
            loaded = self.get_memory_workspace_state(workspace_id)
            if loaded is None or loaded.row_version != state.row_version:
                raise _stale("workspace memory state")
            return loaded

        return self.backend.transact(work)

    def _assert_current_revision(self, workspace_id: str, head: ProjectKnowledgeHead) -> None:
        if head.current_revision_id is None:
            return
        row = self.backend.read_one(
            "SELECT workspace_id, knowledge_id FROM project_knowledge_revisions "
            "WHERE knowledge_revision_id = ?",
            (head.current_revision_id,),
        )
        if row is None:
            raise _missing("knowledge current revision")
        if str(row[0]) != workspace_id or str(row[1]) != head.knowledge_id:
            raise _workspace_error("knowledge current revision")

    def _assert_revision_sources(
        self, workspace_id: str, revision: ProjectKnowledgeRevision
    ) -> None:
        if revision.source_candidate_id is not None:
            candidate = self.backend.read_one(
                "SELECT workspace_id FROM learning_candidates WHERE candidate_id = ?",
                (revision.source_candidate_id,),
            )
            if candidate is None:
                raise _missing("knowledge source candidate")
            if str(candidate[0]) != workspace_id:
                raise _workspace_error("knowledge source candidate")
        if revision.source_decision_id is not None:
            decision = self.backend.read_one(
                "SELECT workspace_id, candidate_id FROM learning_candidate_decisions "
                "WHERE decision_id = ?",
                (revision.source_decision_id,),
            )
            if decision is None:
                raise _missing("knowledge source decision")
            if str(decision[0]) != workspace_id:
                raise _workspace_error("knowledge source decision")
            if (
                revision.source_candidate_id is not None
                and str(decision[1]) != revision.source_candidate_id
            ):
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE,
                    "knowledge source decision does not match candidate",
                )
        if revision.supersedes_revision_id is not None:
            superseded = self.backend.read_one(
                "SELECT workspace_id, knowledge_id FROM project_knowledge_revisions "
                "WHERE knowledge_revision_id = ?",
                (revision.supersedes_revision_id,),
            )
            if superseded is None:
                raise _missing("knowledge superseded revision")
            if str(superseded[0]) != workspace_id or str(superseded[1]) != revision.knowledge_id:
                raise _workspace_error("knowledge superseded revision")


def _check_limit(limit: int, label: str) -> None:
    if not 1 <= limit <= 500:
        raise StorageError(StorageErrorCode.UNAVAILABLE, f"learning {label} page is invalid")


__all__ = ["SqliteLearningMemoryJournal"]
