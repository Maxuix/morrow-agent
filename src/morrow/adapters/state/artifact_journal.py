"""SQLite persistence for Artifact metadata and cross-domain references."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime

from morrow.adapters.state.transaction import SqliteJournalBackend
from morrow.core.artifacts import (
    TASK_ARTIFACT_MAX_BYTES,
    ArtifactBudgetError,
    ArtifactKind,
    ArtifactMetadata,
    ArtifactProvenanceRef,
    ArtifactRetention,
    ArtifactSensitivity,
    ArtifactState,
)
from morrow.core.domain import ArtifactReference, canonical_json_bytes
from morrow.core.store import StorageError, StorageErrorCode

_ARTIFACT_COLUMNS = (
    "artifact_id, workspace_id, session_id, task_run_id, kind, sensitivity, state, retention, "
    "sha256, byte_size, excerpt, provenance_json, row_version, created_at_unix, updated_at_unix"
)
_REFERENCE_COLUMNS = "artifact_id, workspace_id, owner_kind, owner_id, role, created_at_unix"


def _unix(value: datetime) -> int:
    return int(value.timestamp())


def _from_unix(value: object) -> datetime:
    return datetime.fromtimestamp(int(value), UTC)


class SqliteArtifactJournal:
    """Bounded Artifact repository sharing one outer transaction backend."""

    def __init__(
        self,
        backend: SqliteJournalBackend,
        *,
        session_exists: Callable[[str, str], bool],
        task_belongs_to_session: Callable[[str, str, str], bool],
    ) -> None:
        self.backend = backend
        self.session_exists = session_exists
        self.task_belongs_to_session = task_belongs_to_session

    def reserve(self, workspace_id: str, metadata: ArtifactMetadata) -> ArtifactMetadata:
        if metadata.workspace_id != workspace_id:
            raise StorageError(
                StorageErrorCode.UNAVAILABLE, "operational artifact is outside the workspace"
            )
        if metadata.state is not ArtifactState.STAGING:
            raise StorageError(
                StorageErrorCode.UNAVAILABLE, "new operational artifact must start staging"
            )

        def work() -> ArtifactMetadata:
            self.validate_scope(workspace_id, metadata)
            if metadata.task_run_id is not None:
                used = self.bytes_for_task(workspace_id, metadata.task_run_id)
                if used + metadata.byte_size > TASK_ARTIFACT_MAX_BYTES:
                    raise ArtifactBudgetError("TaskRun artifact byte budget exceeded")
            self.backend.executor().execute(
                f"INSERT INTO artifacts({_ARTIFACT_COLUMNS}) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    metadata.artifact_id,
                    metadata.workspace_id,
                    metadata.session_id,
                    metadata.task_run_id,
                    metadata.kind.value,
                    metadata.sensitivity.value,
                    metadata.state.value,
                    metadata.retention.value,
                    metadata.sha256,
                    metadata.byte_size,
                    metadata.excerpt,
                    _optional_json(metadata.provenance_refs),
                    metadata.row_version,
                    _unix(metadata.created_at),
                    _unix(metadata.updated_at),
                ),
            )
            loaded = self.get(workspace_id, metadata.artifact_id)
            if loaded is None:
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE, "operational artifact could not be read"
                )
            return loaded

        return self.backend.transact(work)

    def get(self, workspace_id: str, artifact_id: str) -> ArtifactMetadata | None:
        row = self.backend.read_one(
            f"SELECT {_ARTIFACT_COLUMNS} FROM artifacts WHERE artifact_id = ? AND workspace_id = ?",
            (artifact_id, workspace_id),
        )
        return _artifact_from_row(row) if row is not None else None

    def list(
        self,
        workspace_id: str,
        *,
        session_id: str | None = None,
        task_run_id: str | None = None,
    ) -> tuple[ArtifactMetadata, ...]:
        sql = f"SELECT {_ARTIFACT_COLUMNS} FROM artifacts WHERE workspace_id = ?"
        parameters: list[object] = [workspace_id]
        if session_id is not None:
            sql += " AND session_id = ?"
            parameters.append(session_id)
        if task_run_id is not None:
            sql += " AND task_run_id = ?"
            parameters.append(task_run_id)
        sql += " ORDER BY created_at_unix ASC, artifact_id ASC"
        return tuple(
            _artifact_from_row(row) for row in self.backend.read_all(sql, tuple(parameters))
        )

    def save(
        self,
        workspace_id: str,
        metadata: ArtifactMetadata,
        *,
        expected_row_version: int,
    ) -> ArtifactMetadata:
        if metadata.workspace_id != workspace_id:
            raise StorageError(
                StorageErrorCode.UNAVAILABLE, "operational artifact is outside the workspace"
            )

        def work() -> ArtifactMetadata:
            existing = self.get(workspace_id, metadata.artifact_id)
            if existing is None:
                raise StorageError(StorageErrorCode.NOT_FOUND, "operational artifact is missing")
            if existing.row_version != expected_row_version:
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE, "operational artifact row version is stale"
                )
            if metadata.row_version != expected_row_version + 1:
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE, "operational artifact row version is stale"
                )
            immutable_fields = (
                "workspace_id",
                "session_id",
                "task_run_id",
                "kind",
                "sensitivity",
                "sha256",
                "byte_size",
                "provenance_refs",
                "created_at",
            )
            if any(
                getattr(existing, field) != getattr(metadata, field) for field in immutable_fields
            ):
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE, "operational artifact identity is immutable"
                )
            _validate_state(existing.state, metadata.state)
            self.backend.executor().execute(
                """
                UPDATE artifacts
                SET state = ?, retention = ?, excerpt = ?, row_version = ?, updated_at_unix = ?
                WHERE artifact_id = ? AND workspace_id = ? AND row_version = ?
                """,
                (
                    metadata.state.value,
                    metadata.retention.value,
                    metadata.excerpt,
                    metadata.row_version,
                    _unix(metadata.updated_at),
                    metadata.artifact_id,
                    workspace_id,
                    expected_row_version,
                ),
            )
            loaded = self.get(workspace_id, metadata.artifact_id)
            if loaded is None or loaded.row_version != metadata.row_version:
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE, "operational artifact row version is stale"
                )
            return loaded

        return self.backend.transact(work)

    def bytes_for_task(self, workspace_id: str, task_run_id: str) -> int:
        row = self.backend.read_one(
            "SELECT COALESCE(SUM(byte_size), 0) FROM artifacts "
            "WHERE workspace_id = ? AND task_run_id = ?",
            (workspace_id, task_run_id),
        )
        return int(row[0]) if row is not None else 0

    def list_references(
        self, workspace_id: str, artifact_id: str | None = None
    ) -> tuple[tuple[str, str, str, str], ...]:
        sql = f"SELECT {_REFERENCE_COLUMNS} FROM artifact_references WHERE workspace_id = ?"
        parameters: list[object] = [workspace_id]
        if artifact_id is not None:
            sql += " AND artifact_id = ?"
            parameters.append(artifact_id)
        rows = self.backend.read_all(sql, tuple(parameters))
        checkpoint_sql = (
            "SELECT artifact_id, workspace_id, checkpoint_id, role "
            "FROM checkpoint_artifact_references WHERE workspace_id = ?"
        )
        checkpoint_parameters: list[object] = [workspace_id]
        if artifact_id is not None:
            checkpoint_sql += " AND artifact_id = ?"
            checkpoint_parameters.append(artifact_id)
        checkpoint_rows = self.backend.read_all(checkpoint_sql, tuple(checkpoint_parameters))
        references = [(str(row[0]), str(row[2]), str(row[3]), str(row[4])) for row in rows]
        references.extend(
            (str(row[0]), "context_checkpoint", str(row[2]), str(row[3])) for row in checkpoint_rows
        )
        return tuple(sorted(references, key=lambda item: (item[0], item[1], item[2], item[3])))

    def replace_references(
        self,
        workspace_id: str,
        *,
        owner_kind: str,
        owner_id: str,
        references: tuple[ArtifactReference, ...],
        created_at: datetime,
    ) -> None:
        executor = self.backend.executor()
        executor.execute(
            "DELETE FROM artifact_references WHERE workspace_id = ? AND owner_kind = ? "
            "AND owner_id = ?",
            (workspace_id, owner_kind, owner_id),
        )
        for reference in references:
            executor.execute(
                f"INSERT INTO artifact_references({_REFERENCE_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    reference.artifact_id,
                    workspace_id,
                    owner_kind,
                    owner_id,
                    reference.role,
                    _unix(created_at),
                ),
            )

    def validate_scope(self, workspace_id: str, metadata: ArtifactMetadata) -> None:
        if metadata.session_id is None:
            if metadata.task_run_id is not None:
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE, "operational artifact scope is inconsistent"
                )
            return
        if not self.session_exists(workspace_id, metadata.session_id):
            raise StorageError(
                StorageErrorCode.NOT_FOUND, "operational artifact session is missing"
            )
        if metadata.task_run_id is not None and not self.task_belongs_to_session(
            workspace_id, metadata.task_run_id, metadata.session_id
        ):
            raise StorageError(
                StorageErrorCode.UNAVAILABLE, "operational artifact task scope is invalid"
            )


def _optional_json(value: object | None) -> str | None:
    if value is None:
        return None
    if hasattr(value, "model_dump"):
        dumped = value.model_dump(mode="json")
    elif isinstance(value, (tuple, list)):
        dumped = [
            item.model_dump(mode="json") if hasattr(item, "model_dump") else item for item in value
        ]
    else:
        dumped = value
    return canonical_json_bytes(dumped).decode("utf-8")


def _validate_state(current: ArtifactState, target: ArtifactState) -> None:
    if current is target:
        return
    allowed = {
        ArtifactState.STAGING: {
            ArtifactState.AVAILABLE,
            ArtifactState.MISSING,
            ArtifactState.CORRUPT,
        },
        ArtifactState.AVAILABLE: {ArtifactState.MISSING, ArtifactState.CORRUPT},
        ArtifactState.MISSING: set(),
        ArtifactState.CORRUPT: set(),
    }
    if target not in allowed[current]:
        raise StorageError(
            StorageErrorCode.UNAVAILABLE, "operational artifact state transition is invalid"
        )


def _artifact_from_row(row: tuple[object, ...]) -> ArtifactMetadata:
    try:
        provenance_raw = json.loads(str(row[11]))
        if not isinstance(provenance_raw, list):
            raise ValueError("artifact provenance is not a list")
        return ArtifactMetadata(
            artifact_id=str(row[0]),
            workspace_id=str(row[1]),
            session_id=str(row[2]) if row[2] is not None else None,
            task_run_id=str(row[3]) if row[3] is not None else None,
            kind=ArtifactKind(str(row[4])),
            sensitivity=ArtifactSensitivity(str(row[5])),
            state=ArtifactState(str(row[6])),
            retention=ArtifactRetention(str(row[7])),
            sha256=str(row[8]),
            byte_size=int(row[9]),
            excerpt=str(row[10]),
            provenance_refs=tuple(
                ArtifactProvenanceRef.model_validate(item) for item in provenance_raw
            ),
            row_version=int(row[12]),
            created_at=_from_unix(row[13]),
            updated_at=_from_unix(row[14]),
        )
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise StorageError(
            StorageErrorCode.NEEDS_REPAIR, "operational artifact metadata is invalid"
        ) from exc
