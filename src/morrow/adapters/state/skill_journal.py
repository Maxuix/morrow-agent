"""SQLite Skill journal for v14 catalog/run evidence and v15 Draft/Usage rows."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime

from morrow.adapters.state.migrations_v14_skills import GLOBAL_SCOPE_ID
from morrow.adapters.state.transaction import SqliteJournalBackend
from morrow.core.domain import canonical_json_bytes, sha256_digest
from morrow.core.skills.catalog import (
    SkillAvailability,
    SkillConflictStatus,
    SkillDefinition,
    SkillVersion,
)
from morrow.core.skills.context import SkillContextEntry
from morrow.core.skills.drafts import SkillDraft, SkillDraftValidationReport
from morrow.core.skills.selection import SkillSelection
from morrow.core.skills.trust import SourceKind, TrustLevel
from morrow.core.skills.usage import SkillUsage, SkillUsageStatus
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
_DRAFT_COLUMNS = (
    "draft_id, workspace_id, candidate_id, root_draft_id, parent_draft_id, revision, status, "
    "skill_id, name, display_version, scope_id, candidate_fingerprint, evidence_refs_json, "
    "package_ref, tree_digest, file_count, total_bytes, validation_id, accepted_version_id, "
    "acceptance_command_id, rejection_reason, row_version, created_at_unix, updated_at_unix"
)
_VALIDATION_COLUMNS = (
    "validation_id, draft_id, revision, validator_version, valid, report_json, report_bytes, "
    "report_digest, created_at_unix"
)
_USAGE_COLUMNS = (
    "usage_id, workspace_id, agent_run_id, task_run_id, selection_id, skill_id, version_id, "
    "activation_reason, status, user_correction, input_tokens, output_tokens, duration_ms, "
    "tool_call_count, artifact_refs_json, facts_digest, created_at_unix"
)

SCOPE_VALUES = ("global", "workspace")
AVAILABILITY_VALUES = tuple(item.value for item in SkillAvailability)
CONFLICT_VALUES = tuple(item.value for item in SkillConflictStatus)
TRUST_VALUES = tuple(item.value for item in TrustLevel)
SOURCE_VALUES = tuple(item.value for item in SourceKind)


@dataclass(frozen=True, slots=True)
class SkillOperationReceipt:
    """Bounded durable command projection used when no workspace receipt exists."""

    operation_id: str
    scope: str
    scope_id: str | None
    skill_id: str
    version_id: str | None
    operation: str
    disposition: str
    request_digest: str | None


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


def _stored_scope_id(scope_id: str | None) -> str:
    return GLOBAL_SCOPE_ID if scope_id is None else scope_id


def _domain_scope_id(scope_id: str) -> str | None:
    return None if scope_id == GLOBAL_SCOPE_ID else scope_id


def _context_json(context: SkillContextEntry) -> str:
    return json.dumps(
        {
            "selection_id": context.selection_id,
            "tree_digest": context.tree_digest,
            "content": context.content,
            "context_digest": context.context_digest,
            "truncated": context.truncated,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def _report_digest(report: SkillDraftValidationReport) -> str:
    payload = report.model_dump(mode="json")
    payload.pop("report_digest", None)
    return sha256_digest(canonical_json_bytes(payload))


def _usage_digest(usage: SkillUsage) -> str:
    payload = usage.model_dump(mode="json")
    payload.pop("facts_digest", None)
    return sha256_digest(canonical_json_bytes(payload))


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
        if definition.effective_trust not in TRUST_VALUES:
            raise StorageError(StorageErrorCode.UNAVAILABLE, "Skill definition Trust is invalid")
        stored_scope_id = _stored_scope_id(definition.scope_id)
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
                stored_scope_id,
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
        stored_scope_id = _stored_scope_id(version.scope_id)
        values = (
            version.version_id,
            scope,
            stored_scope_id,
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
        )
        existing = self.backend.read_one(
            f"SELECT {_VERSION_COLUMNS} FROM skill_versions WHERE version_id = ?",
            (version.version_id,),
        )
        if existing is not None:
            # The installation timestamp is assigned by the first insert when
            # callers omit ``created_at``. All other fields are immutable.
            if tuple(existing[:12]) != tuple(values[:12]) or (
                version.created_at is not None and existing[12] != values[12]
            ):
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE,
                    "Skill version already exists with different immutable content",
                )
            return
        self.backend.executor().execute(
            f"""
            INSERT INTO skill_versions({_VERSION_COLUMNS})
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            values,
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
                _stored_scope_id(scope_id),
                skill_id,
                version_id,
                operation,
                disposition,
                evidence_digest,
                reason[:1024] if reason else None,
                _unix(created_at),
            ),
        )

    def get_operation(self, operation_id: str) -> SkillOperationReceipt | None:
        row = self.backend.read_one(
            """
            SELECT operation_id, scope, scope_id, skill_id, version_id,
                   operation, disposition, reason
            FROM skill_catalog_operations
            WHERE operation_id = ?
            """,
            (operation_id,),
        )
        if row is None:
            return None
        stored_scope_id = str(row[2])
        request_digest = str(row[7]) if row[7] is not None else None
        if request_digest is not None and len(request_digest) != 64:
            request_digest = None
        return SkillOperationReceipt(
            operation_id=str(row[0]),
            scope=str(row[1]),
            scope_id=_domain_scope_id(stored_scope_id),
            skill_id=str(row[3]),
            version_id=str(row[4]) if row[4] is not None else None,
            operation=str(row[5]),
            disposition=str(row[6]),
            request_digest=request_digest,
        )

    def get_definition(self, scope_id: str | None, skill_id: str) -> SkillDefinition | None:
        scope = "global" if scope_id is None else "workspace"
        stored_scope_id = _stored_scope_id(scope_id)
        row = self.backend.read_one(
            f"""
            SELECT {_DEFINITION_COLUMNS} FROM skill_definitions
            WHERE scope = ? AND scope_id = ? AND skill_id = ?
            """,
            (scope, stored_scope_id, skill_id),
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

    def skill_version_references(self, version_id: str) -> tuple[str, ...]:
        """Return bounded reference labels before an immutable version removal."""

        tables = (
            ("agent_run_skill_selections", "version_id"),
            ("agent_run_skill_contexts", "version_id"),
            ("skill_drafts", "accepted_version_id"),
            ("skill_usage", "version_id"),
            ("agent_definition_skills", "skill_version_id"),
        )
        references: list[str] = []
        for table, column in tables:
            exists = self.backend.read_one(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?",
                (table,),
            )
            if exists is None:
                continue
            rows = self.backend.read_all(
                f"SELECT 1 FROM {table} WHERE {column} = ? LIMIT 9",
                (version_id,),
            )
            references.extend(f"{table}:{index}" for index, _ in enumerate(rows))
        return tuple(references[:32])

    def delete_version(self, version_id: str) -> None:
        references = self.skill_version_references(version_id)
        if references:
            raise StorageError(
                StorageErrorCode.UNAVAILABLE,
                "Skill version is referenced and cannot be removed",
            )
        identity = self.backend.read_one(
            "SELECT scope, scope_id, skill_id FROM skill_versions WHERE version_id = ?",
            (version_id,),
        )
        self.backend.executor().execute(
            "DELETE FROM skill_versions WHERE version_id = ?",
            (version_id,),
        )
        if identity is not None:
            remaining = self.backend.read_one(
                """
                SELECT 1 FROM skill_versions
                WHERE scope = ? AND scope_id = ? AND skill_id = ?
                LIMIT 1
                """,
                (identity[0], identity[1], identity[2]),
            )
            if remaining is None:
                self.backend.executor().execute(
                    """
                    DELETE FROM skill_definitions
                    WHERE scope = ? AND scope_id = ? AND skill_id = ?
                    """,
                    (identity[0], identity[1], identity[2]),
                )

    def put_selection(self, selection: SkillSelection) -> SkillSelection:
        _validate_scope(selection.scope, selection.scope_id, label="Skill selection")
        stored_scope_id = _stored_scope_id(selection.scope_id)
        self.backend.executor().execute(
            """
            INSERT INTO agent_run_skill_selections(
                selection_id, agent_run_id, scope, scope_id, skill_id, version_id,
                activation_reason, tree_digest, created_at_unix
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                selection.selection_id,
                selection.agent_run_id,
                selection.scope,
                stored_scope_id,
                selection.skill_id,
                selection.version_id,
                selection.activation_reason,
                selection.tree_digest,
                _unix(self.backend.now()),
            ),
        )
        return selection

    def get_selection(self, workspace_id: str, selection_id: str) -> SkillSelection | None:
        row = self.backend.read_one(
            """
            SELECT s.selection_id, s.agent_run_id, s.scope, s.scope_id, s.skill_id,
                   s.version_id, s.activation_reason, s.tree_digest, v.source_kind
            FROM agent_run_skill_selections AS s
            JOIN agent_runs AS r ON r.agent_run_id = s.agent_run_id
            JOIN skill_versions AS v ON v.version_id = s.version_id
            WHERE r.session_id IN (
                SELECT session_id FROM sessions WHERE workspace_id = ?
            ) AND s.selection_id = ?
            """,
            (workspace_id, selection_id),
        )
        return self._selection_from_row(row) if row is not None else None

    def list_selections(self, workspace_id: str, agent_run_id: str) -> tuple[SkillSelection, ...]:
        rows = self.backend.read_all(
            """
            SELECT s.selection_id, s.agent_run_id, s.scope, s.scope_id, s.skill_id,
                   s.version_id, s.activation_reason, s.tree_digest, v.source_kind
            FROM agent_run_skill_selections AS s
            JOIN agent_runs AS r ON r.agent_run_id = s.agent_run_id
            JOIN skill_versions AS v ON v.version_id = s.version_id
            JOIN sessions AS ss ON ss.session_id = r.session_id
            WHERE ss.workspace_id = ? AND s.agent_run_id = ?
            ORDER BY s.selection_id
            """,
            (workspace_id, agent_run_id),
        )
        return tuple(self._selection_from_row(row) for row in rows)

    def put_context(self, context: SkillContextEntry) -> SkillContextEntry:
        _validate_scope(context.scope, context.scope_id, label="Skill context")
        raw = _context_json(context)
        if len(raw.encode("utf-8")) > 16 * 1024:
            raise StorageError(StorageErrorCode.UNAVAILABLE, "Skill context is unbounded")
        self.backend.executor().execute(
            """
            INSERT INTO agent_run_skill_contexts(
                context_id, agent_run_id, scope, scope_id, skill_id, version_id,
                context_digest, context_json, omitted_count, created_at_unix
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                context.context_id,
                context.agent_run_id,
                context.scope,
                _stored_scope_id(context.scope_id),
                context.skill_id,
                context.version_id,
                context.context_digest,
                raw,
                context.omitted_count,
                _unix(self.backend.now()),
            ),
        )
        return context

    def get_context(self, workspace_id: str, context_id: str) -> SkillContextEntry | None:
        row = self.backend.read_one(
            """
            SELECT c.context_id, c.agent_run_id, c.scope, c.scope_id, c.skill_id,
                   c.version_id, c.context_digest, c.context_json, c.omitted_count
            FROM agent_run_skill_contexts AS c
            JOIN agent_runs AS r ON r.agent_run_id = c.agent_run_id
            JOIN sessions AS ss ON ss.session_id = r.session_id
            WHERE ss.workspace_id = ? AND c.context_id = ?
            """,
            (workspace_id, context_id),
        )
        return self._context_from_row(row) if row is not None else None

    def list_contexts(self, workspace_id: str, agent_run_id: str) -> tuple[SkillContextEntry, ...]:
        rows = self.backend.read_all(
            """
            SELECT c.context_id, c.agent_run_id, c.scope, c.scope_id, c.skill_id,
                   c.version_id, c.context_digest, c.context_json, c.omitted_count
            FROM agent_run_skill_contexts AS c
            JOIN agent_runs AS r ON r.agent_run_id = c.agent_run_id
            JOIN sessions AS ss ON ss.session_id = r.session_id
            WHERE ss.workspace_id = ? AND c.agent_run_id = ?
            ORDER BY c.context_id
            """,
            (workspace_id, agent_run_id),
        )
        return tuple(self._context_from_row(row) for row in rows)

    def put_draft(self, draft: SkillDraft) -> SkillDraft:
        _validate_scope("workspace", draft.workspace_id, label="Skill Draft")
        candidate = self.backend.read_one(
            "SELECT workspace_id FROM learning_candidates WHERE candidate_id = ?",
            (draft.candidate_id,),
        )
        if candidate is None:
            raise StorageError(StorageErrorCode.NOT_FOUND, "Skill Draft candidate is missing")
        if str(candidate[0]) != draft.workspace_id:
            raise StorageError(StorageErrorCode.UNAVAILABLE, "Skill Draft is outside the workspace")
        values = self._draft_values(draft)
        self.backend.executor().execute(
            f"INSERT INTO skill_drafts({_DRAFT_COLUMNS}) VALUES ({','.join('?' for _ in values)})",
            values,
        )
        loaded = self.get_draft(draft.workspace_id, draft.draft_id)
        if loaded is None:
            raise StorageError(StorageErrorCode.NEEDS_REPAIR, "Skill Draft could not be read")
        return loaded

    def get_draft(self, workspace_id: str, draft_id: str) -> SkillDraft | None:
        row = self.backend.read_one(
            f"SELECT {_DRAFT_COLUMNS} FROM skill_drafts WHERE workspace_id = ? AND draft_id = ?",
            (workspace_id, draft_id),
        )
        return self._draft_from_row(row) if row is not None else None

    def get_draft_by_candidate(
        self, workspace_id: str, candidate_id: str, *, latest: bool = True
    ) -> SkillDraft | None:
        order = "DESC" if latest else "ASC"
        row = self.backend.read_one(
            f"SELECT {_DRAFT_COLUMNS} FROM skill_drafts "
            f"WHERE workspace_id = ? AND candidate_id = ? ORDER BY revision {order} LIMIT 1",
            (workspace_id, candidate_id),
        )
        return self._draft_from_row(row) if row is not None else None

    def list_drafts(
        self,
        workspace_id: str,
        *,
        candidate_id: str | None = None,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[SkillDraft, ...]:
        if not 1 <= limit <= 500:
            raise StorageError(StorageErrorCode.UNAVAILABLE, "Skill Draft page is invalid")
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            raise StorageError(StorageErrorCode.UNAVAILABLE, "page offset is invalid")
        sql = f"SELECT {_DRAFT_COLUMNS} FROM skill_drafts WHERE workspace_id = ?"
        params: list[object] = [workspace_id]
        if candidate_id is not None:
            sql += " AND candidate_id = ?"
            params.append(candidate_id)
        if status is not None:
            sql += " AND status = ?"
            params.append(status)
        sql += " ORDER BY updated_at_unix DESC, revision DESC, draft_id ASC LIMIT ? OFFSET ?"
        params.extend((limit, offset))
        return tuple(self._draft_from_row(row) for row in self.backend.read_all(sql, tuple(params)))

    def save_draft(self, draft: SkillDraft, *, expected_row_version: int) -> SkillDraft:
        existing = self.get_draft(draft.workspace_id, draft.draft_id)
        if existing is None:
            raise StorageError(StorageErrorCode.NOT_FOUND, "Skill Draft is missing")
        if (
            existing.row_version != expected_row_version
            or draft.row_version != expected_row_version + 1
        ):
            raise StorageError(StorageErrorCode.UNAVAILABLE, "Skill Draft row version is stale")
        immutable = (
            "draft_id",
            "workspace_id",
            "candidate_id",
            "root_draft_id",
            "parent_draft_id",
            "revision",
            "skill_id",
            "name",
            "display_version",
            "scope_id",
            "candidate_fingerprint",
            "evidence_refs",
            "package_ref",
            "tree_digest",
            "file_count",
            "total_bytes",
            "created_at",
        )
        if any(getattr(existing, field) != getattr(draft, field) for field in immutable):
            raise StorageError(StorageErrorCode.UNAVAILABLE, "Skill Draft identity is immutable")
        values = self._draft_values(draft)
        self.backend.executor().execute(
            """
            UPDATE skill_drafts
            SET status = ?, validation_id = ?, accepted_version_id = ?,
                acceptance_command_id = ?, rejection_reason = ?, row_version = ?,
                updated_at_unix = ?
            WHERE workspace_id = ? AND draft_id = ? AND row_version = ?
            """,
            (
                values[6],
                values[17],
                values[18],
                values[19],
                values[20],
                values[21],
                values[22],
                draft.workspace_id,
                draft.draft_id,
                expected_row_version,
            ),
        )
        loaded = self.get_draft(draft.workspace_id, draft.draft_id)
        if loaded is None or loaded.row_version != draft.row_version:
            raise StorageError(StorageErrorCode.UNAVAILABLE, "Skill Draft row version is stale")
        return loaded

    def put_validation(self, report: SkillDraftValidationReport) -> SkillDraftValidationReport:
        raw = canonical_json_bytes(report.model_dump(mode="json"))
        if len(raw) > 32 * 1024:
            raise StorageError(StorageErrorCode.UNAVAILABLE, "Skill validation report is unbounded")
        if _report_digest(report) != report.report_digest:
            raise StorageError(
                StorageErrorCode.UNAVAILABLE, "Skill validation report digest is invalid"
            )
        self.backend.executor().execute(
            f"INSERT INTO skill_draft_validations({_VALIDATION_COLUMNS}) "
            f"VALUES ({','.join('?' for _ in range(9))})",
            (
                report.validation_id,
                report.draft_id,
                report.revision,
                report.validator_version,
                int(report.valid),
                raw.decode("utf-8"),
                len(raw),
                report.report_digest,
                _unix(report.created_at),
            ),
        )
        loaded = self.get_validation(report.draft_id, report.validation_id)
        if loaded is None:
            raise StorageError(
                StorageErrorCode.NEEDS_REPAIR, "Skill validation report could not be read"
            )
        return loaded

    def get_validation(
        self, draft_id: str, validation_id: str
    ) -> SkillDraftValidationReport | None:
        row = self.backend.read_one(
            f"SELECT {_VALIDATION_COLUMNS} FROM skill_draft_validations "
            "WHERE draft_id = ? AND validation_id = ?",
            (draft_id, validation_id),
        )
        return self._validation_from_row(row) if row is not None else None

    def list_validations(
        self, draft_id: str, *, limit: int = 32
    ) -> tuple[SkillDraftValidationReport, ...]:
        if not 1 <= limit <= 128:
            raise StorageError(StorageErrorCode.UNAVAILABLE, "Skill validation page is invalid")
        rows = self.backend.read_all(
            f"SELECT {_VALIDATION_COLUMNS} FROM skill_draft_validations "
            "WHERE draft_id = ? ORDER BY created_at_unix DESC, validation_id DESC LIMIT ?",
            (draft_id, limit),
        )
        return tuple(self._validation_from_row(row) for row in rows)

    def put_usage(self, usage: SkillUsage) -> SkillUsage:
        if usage.workspace_id != str(usage.workspace_id):
            raise StorageError(StorageErrorCode.UNAVAILABLE, "Skill usage workspace is invalid")
        raw = canonical_json_bytes([item.model_dump(mode="json") for item in usage.artifact_refs])
        if len(raw) > 8 * 1024:
            raise StorageError(
                StorageErrorCode.UNAVAILABLE, "Skill usage Artifact references are unbounded"
            )
        if _usage_digest(usage) != usage.facts_digest:
            raise StorageError(StorageErrorCode.UNAVAILABLE, "Skill usage facts digest is invalid")
        self.backend.executor().execute(
            f"INSERT INTO skill_usage({_USAGE_COLUMNS}) VALUES ({','.join('?' for _ in range(17))})",
            (
                usage.usage_id,
                usage.workspace_id,
                usage.agent_run_id,
                usage.task_run_id,
                usage.selection_id,
                usage.skill_id,
                usage.version_id,
                usage.activation_reason,
                usage.status.value,
                int(usage.user_correction),
                usage.input_tokens,
                usage.output_tokens,
                usage.duration_ms,
                usage.tool_call_count,
                raw.decode("utf-8"),
                usage.facts_digest,
                _unix(usage.created_at),
            ),
        )
        loaded = self.get_usage(usage.workspace_id, usage.usage_id)
        if loaded is None:
            raise StorageError(StorageErrorCode.NEEDS_REPAIR, "Skill usage could not be read")
        return loaded

    def get_usage(self, workspace_id: str, usage_id: str) -> SkillUsage | None:
        row = self.backend.read_one(
            f"SELECT {_USAGE_COLUMNS} FROM skill_usage WHERE workspace_id = ? AND usage_id = ?",
            (workspace_id, usage_id),
        )
        return self._usage_from_row(row) if row is not None else None

    def list_usages(
        self,
        workspace_id: str,
        *,
        skill_id: str | None = None,
        version_id: str | None = None,
        agent_run_id: str | None = None,
        limit: int = 100,
    ) -> tuple[SkillUsage, ...]:
        if not 1 <= limit <= 1000:
            raise StorageError(StorageErrorCode.UNAVAILABLE, "Skill usage page is invalid")
        sql = f"SELECT {_USAGE_COLUMNS} FROM skill_usage WHERE workspace_id = ?"
        params: list[object] = [workspace_id]
        for column, value in (
            ("skill_id", skill_id),
            ("version_id", version_id),
            ("agent_run_id", agent_run_id),
        ):
            if value is not None:
                sql += f" AND {column} = ?"
                params.append(value)
        sql += " ORDER BY created_at_unix ASC, usage_id ASC LIMIT ?"
        params.append(limit)
        return tuple(self._usage_from_row(row) for row in self.backend.read_all(sql, tuple(params)))

    @staticmethod
    def _selection_from_row(row) -> SkillSelection:
        return SkillSelection(
            selection_id=str(row[0]),
            agent_run_id=str(row[1]),
            scope=str(row[2]),
            scope_id=_domain_scope_id(str(row[3])),
            skill_id=str(row[4]),
            version_id=str(row[5]),
            activation_reason=str(row[6]),
            tree_digest=str(row[7]),
            source_kind=SourceKind(str(row[8])),
        )

    @staticmethod
    def _context_from_row(row) -> SkillContextEntry:
        try:
            payload = json.loads(str(row[7]))
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise StorageError(
                StorageErrorCode.NEEDS_REPAIR, "Skill context JSON is invalid"
            ) from exc
        if not isinstance(payload, dict):
            raise StorageError(StorageErrorCode.NEEDS_REPAIR, "Skill context JSON is invalid")
        try:
            content = str(payload.get("content", ""))
            return SkillContextEntry(
                context_id=str(row[0]),
                agent_run_id=str(row[1]),
                scope=str(row[2]),
                scope_id=_domain_scope_id(str(row[3])),
                skill_id=str(row[4]),
                version_id=str(row[5]),
                selection_id=str(payload.get("selection_id", "")),
                tree_digest=str(payload.get("tree_digest", "0" * 64)),
                content=content,
                context_digest=str(row[6]),
                omitted_count=int(row[8]),
                truncated=bool(payload.get("truncated", False)),
            )
        except (TypeError, ValueError) as exc:
            raise StorageError(
                StorageErrorCode.NEEDS_REPAIR, "Skill context row is invalid"
            ) from exc

    @staticmethod
    def _draft_values(draft: SkillDraft) -> tuple[object, ...]:
        return (
            draft.draft_id,
            draft.workspace_id,
            draft.candidate_id,
            draft.root_draft_id,
            draft.parent_draft_id,
            draft.revision,
            draft.status.value,
            draft.skill_id,
            draft.name,
            draft.display_version,
            draft.scope_id,
            draft.candidate_fingerprint,
            canonical_json_bytes(list(draft.evidence_refs)).decode("utf-8"),
            draft.package_ref,
            draft.tree_digest,
            draft.file_count,
            draft.total_bytes,
            draft.validation_id,
            draft.accepted_version_id,
            draft.acceptance_command_id,
            draft.rejection_reason,
            draft.row_version,
            _unix(draft.created_at),
            _unix(draft.updated_at),
        )

    @staticmethod
    def _draft_from_row(row) -> SkillDraft:
        try:
            evidence = json.loads(str(row[12]))
            if not isinstance(evidence, list) or any(
                not isinstance(item, str) for item in evidence
            ):
                raise ValueError("invalid evidence refs")
            return SkillDraft(
                draft_id=str(row[0]),
                workspace_id=str(row[1]),
                candidate_id=str(row[2]),
                root_draft_id=str(row[3]),
                parent_draft_id=str(row[4]) if row[4] is not None else None,
                revision=int(row[5]),
                status=str(row[6]),
                skill_id=str(row[7]),
                name=str(row[8]),
                display_version=str(row[9]) if row[9] is not None else None,
                scope_id=str(row[10]),
                candidate_fingerprint=str(row[11]),
                evidence_refs=tuple(evidence),
                package_ref=str(row[13]),
                tree_digest=str(row[14]),
                file_count=int(row[15]),
                total_bytes=int(row[16]),
                validation_id=str(row[17]) if row[17] is not None else None,
                accepted_version_id=str(row[18]) if row[18] is not None else None,
                acceptance_command_id=str(row[19]) if row[19] is not None else None,
                rejection_reason=str(row[20]) if row[20] is not None else None,
                row_version=int(row[21]),
                created_at=_from_unix(row[22]),
                updated_at=_from_unix(row[23]),
            )
        except (TypeError, ValueError, IndexError, OverflowError, json.JSONDecodeError) as exc:
            raise StorageError(StorageErrorCode.NEEDS_REPAIR, "Skill Draft row is invalid") from exc

    @staticmethod
    def _validation_from_row(row) -> SkillDraftValidationReport:
        try:
            raw = str(row[5]).encode("utf-8")
            if len(raw) != int(row[6]) or canonical_json_bytes(json.loads(raw)) != raw:
                raise ValueError("non-canonical validation report")
            report = SkillDraftValidationReport.model_validate(json.loads(raw))
            if report.validation_id != str(row[0]) or report.draft_id != str(row[1]):
                raise ValueError("validation identity mismatch")
            if (
                report.report_digest != str(row[7])
                or _report_digest(report) != report.report_digest
            ):
                raise ValueError("validation digest mismatch")
            return report
        except (TypeError, ValueError, IndexError, OverflowError, json.JSONDecodeError) as exc:
            raise StorageError(
                StorageErrorCode.NEEDS_REPAIR, "Skill validation row is invalid"
            ) from exc

    @staticmethod
    def _usage_from_row(row) -> SkillUsage:
        try:
            raw = str(row[14]).encode("utf-8")
            payload = json.loads(raw)
            if canonical_json_bytes(payload) != raw or not isinstance(payload, list):
                raise ValueError("non-canonical Artifact refs")
            usage = SkillUsage(
                usage_id=str(row[0]),
                workspace_id=str(row[1]),
                agent_run_id=str(row[2]),
                task_run_id=str(row[3]) if row[3] is not None else None,
                selection_id=str(row[4]) if row[4] is not None else None,
                skill_id=str(row[5]),
                version_id=str(row[6]),
                activation_reason=str(row[7]),
                status=SkillUsageStatus(str(row[8])),
                user_correction=bool(row[9]),
                input_tokens=int(row[10]),
                output_tokens=int(row[11]),
                duration_ms=int(row[12]),
                tool_call_count=int(row[13]),
                artifact_refs=tuple(payload),
                facts_digest=str(row[15]),
                created_at=_from_unix(row[16]),
            )
            if _usage_digest(usage) != usage.facts_digest:
                raise ValueError("usage digest mismatch")
            return usage
        except (TypeError, ValueError, IndexError, OverflowError, json.JSONDecodeError) as exc:
            raise StorageError(StorageErrorCode.NEEDS_REPAIR, "Skill usage row is invalid") from exc

    @staticmethod
    def _definition_from_row(row) -> SkillDefinition:
        return SkillDefinition(
            skill_id=row[2],
            name=row[3],
            source_kind=SourceKind(row[4]),
            scope_id=_domain_scope_id(row[1]),
            availability=SkillAvailability(row[5]),
            conflict_status=SkillConflictStatus(row[6]),
            effective_trust=TrustLevel(row[7]),
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
            scope_id=_domain_scope_id(row[2]),
            provenance=row[9],
            evidence_refs=tuple(json.loads(row[10])),
            effective_trust=TrustLevel(row[11]),
            created_at=_from_unix(row[12]),
        )
