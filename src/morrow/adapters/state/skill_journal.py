"""SQLite Skill journal: v14 rows only; the aggregate journal delegates here."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from morrow.adapters.state.transaction import SqliteJournalBackend
from morrow.core.skills.catalog import (
    SkillAvailability,
    SkillConflictStatus,
    SkillDefinition,
    SkillVersion,
)
from morrow.core.skills.trust import SourceKind, TrustLevel
from morrow.core.store import StorageError, StorageErrorCode

_DEFINITION_COLUMNS = (
    "scope, scope_id, skill_id, name, source_kind, availability, conflict_status,"
    " effective_trust, updated_at_unix"
)
_VERSION_COLUMNS = (
    "version_id, scope, scope_id, skill_id, display_version, tree_digest, file_count,"
    " total_bytes, source_kind, provenance, evidence_refs_json, effective_trust,"
    " installed_at_unix"
)

SCOPE_VALUES = ("global", "workspace")
AVAILABILITY_VALUES = tuple(item.value for item in SkillAvailability)
CONFLICT_VALUES = tuple(item.value for item in SkillConflictStatus)
TRUST_VALUES = tuple(item.value for item in TrustLevel)
SOURCE_VALUES = tuple(item.value for item in SourceKind)


def _evidence_json(version: SkillVersion) -> str:
    return json.dumps(list(version.evidence_refs), sort_keys=True, separators=(",", ":"))


def _unix(value: datetime) -> int:
    return int(value.timestamp())


def _from_unix(value: object) -> datetime:
    return datetime.fromtimestamp(int(value), UTC)


def _validate_scope(scope: str, scope_id: str | None, *, label: str) -> None:
    if scope not in SCOPE_VALUES:
        raise StorageError(StorageErrorCode.UNAVAILABLE, f"{label} scope is invalid")
    if scope == "global" and scope_id is not None:
        raise StorageError(StorageErrorCode.UNAVAILABLE, f"{label} global scope must be null")
    if scope == "workspace" and not (scope_id and scope_id.startswith("ws_")):
        raise StorageError(StorageErrorCode.UNAVAILABLE, f"{label} workspace scope requires ws_ id")


class SqliteSkillJournal:
    """Bounded v14 Skill repository sharing one outer transaction backend."""

    def __init__(self, backend: SqliteJournalBackend) -> None:
        self.backend = backend

    def put_definition(self, definition: SkillDefinition, *, updated_at: datetime) -> None:
        scope = "global" if definition.scope_id is None else "workspace"
        _validate_scope(scope, definition.scope_id, label="Skill definition")
        if definition.source_kind not in SOURCE_VALUES:
            raise StorageError(StorageErrorCode.UNAVAILABLE, "Skill source_kind is invalid")
        if definition.availability not in AVAILABILITY_VALUES:
            raise StorageError(StorageErrorCode.UNAVAILABLE, "Skill availability is invalid")
        if definition.conflict_status not in CONFLICT_VALUES:
            raise StorageError(StorageErrorCode.UNAVAILABLE, "Skill conflict status is invalid")
        self.backend.executor().execute(
            f"""
            INSERT INTO skill_definitions({_DEFINITION_COLUMNS})
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(scope, scope_id, skill_id) DO UPDATE SET
                name = excluded.name,
                source_kind = excluded.source_kind,
                availability = excluded.availability,
                conflict_status = excluded.conflict_status,
                effective_trust = excluded.effective_trust,
                updated_at_unix = excluded.updated_at_unix
            """,
            (
                scope,
                definition.scope_id,
                definition.skill_id,
                definition.name,
                definition.source_kind.value,
                definition.availability.value,
                definition.conflict_status.value,
                definition.effective_trust.value,
                _unix(updated_at),
            ),
        )

    def put_version(self, version: SkillVersion) -> None:
        scope = "global" if version.scope_id is None else "workspace"
        _validate_scope(scope, version.scope_id, label="Skill version")
        if version.source_kind not in SOURCE_VALUES or version.effective_trust not in TRUST_VALUES:
            raise StorageError(StorageErrorCode.UNAVAILABLE, "Skill version enum is invalid")
        if len(_evidence_json(version)) > 8192:
            raise StorageError(StorageErrorCode.UNAVAILABLE, "Skill version evidence is unbounded")
        self.backend.executor().execute(
            f"""
            INSERT INTO skill_versions({_VERSION_COLUMNS})
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(version_id) DO UPDATE SET
                tree_digest = excluded.tree_digest,
                file_count = excluded.file_count,
                total_bytes = excluded.total_bytes,
                provenance = excluded.provenance,
                evidence_refs_json = excluded.evidence_refs_json,
                effective_trust = excluded.effective_trust
            """,
            (
                version.version_id,
                scope,
                version.scope_id,
                version.skill_id,
                version.display_version,
                version.tree_digest,
                version.file_count,
                version.total_bytes,
                version.source_kind.value,
                version.provenance,
                _evidence_json(version),
                version.effective_trust.value,
                _unix(version.created_at or datetime.now(UTC)),
            ),
        )

    def record_operation(
        self,
        *,
        operation_id: str,
        scope: str,
        scope_id: str | None,
        skill_id: str,
        version_id: str | None,
        operation: str,
        disposition: str,
        evidence_digest: str,
        reason: str | None,
        created_at: datetime,
    ) -> None:
        _validate_scope(scope, scope_id, label="Skill operation")
        allowed_operations = (
            "import",
            "enable",
            "disable",
            "pin",
            "unpin",
            "rollback",
            "remove",
            "draft_create",
            "draft_accept",
            "usage_record",
        )
        if operation not in allowed_operations or disposition not in (
            "applied",
            "rejected",
            "failed",
        ):
            raise StorageError(StorageErrorCode.UNAVAILABLE, "Skill operation enum is invalid")
        self.backend.executor().execute(
            """
            INSERT INTO skill_catalog_operations(
                operation_id, scope, scope_id, skill_id, version_id, operation,
                disposition, evidence_digest, reason, created_at_unix
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                operation_id,
                scope,
                scope_id,
                skill_id,
                version_id,
                operation,
                disposition,
                evidence_digest,
                reason[:1024] if reason else None,
                _unix(created_at),
            ),
        )

    def get_definition(self, scope_id: str | None, skill_id: str) -> SkillDefinition | None:
        scope = "global" if scope_id is None else "workspace"
        row = self.backend.read_one(
            f"""
            SELECT {_DEFINITION_COLUMNS} FROM skill_definitions
            WHERE scope = ? AND scope_id IS ? AND skill_id = ?
            """,
            (scope, scope_id, skill_id),
        )
        return self._definition_from_row(row) if row is not None else None

    def list_definitions(self) -> tuple[SkillDefinition, ...]:
        rows = self.backend.read_all(
            f"SELECT {_DEFINITION_COLUMNS} FROM skill_definitions ORDER BY skill_id"
        )
        return tuple(self._definition_from_row(row) for row in rows)

    def list_versions(self, *, workspace_id: str | None = None) -> tuple[SkillVersion, ...]:
        if workspace_id is None:
            rows = self.backend.read_all(
                f"SELECT {_VERSION_COLUMNS} FROM skill_versions ORDER BY version_id"
            )
        else:
            rows = self.backend.read_all(
                f"SELECT {_VERSION_COLUMNS} FROM skill_versions"
                " WHERE scope = 'workspace' AND scope_id = ? ORDER BY version_id",
                (workspace_id,),
            )
        return tuple(self._version_from_row(row) for row in rows)

    @staticmethod
    def _definition_from_row(row) -> SkillDefinition:
        return SkillDefinition(
            skill_id=row[2],
            name=row[3],
            source_kind=SourceKind(row[4]),
            scope_id=row[1],
            availability=SkillAvailability(row[5]),
            conflict_status=SkillConflictStatus(row[6]),
        )

    @staticmethod
    def _version_from_row(row) -> SkillVersion:
        return SkillVersion(
            version_id=row[0],
            skill_id=row[3],
            display_version=row[4],
            tree_digest=row[5],
            file_count=row[6],
            total_bytes=row[7],
            source_kind=SourceKind(row[8]),
            scope_id=row[2],
            provenance=row[9],
            evidence_refs=tuple(json.loads(row[10])),
            effective_trust=TrustLevel(row[11]),
            created_at=_from_unix(row[12]),
        )
