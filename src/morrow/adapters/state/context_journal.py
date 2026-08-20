"""SQLite persistence for immutable context checkpoints."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime

from morrow.adapters.state.transaction import SqliteJournalBackend
from morrow.core.context import (
    ContextCheckpoint,
    ContextCheckpointOmission,
    ContextCheckpointSection,
)
from morrow.core.domain import (
    ArtifactReference,
    DurableAgentRun,
    DurableConversationRecord,
    DurableSession,
    DurableTaskRun,
    canonical_json_bytes,
)
from morrow.core.store import StorageError, StorageErrorCode

_CHECKPOINT_ARTIFACT_REFERENCE_COLUMNS = (
    "artifact_id, workspace_id, checkpoint_id, role, created_at_unix"
)


def _unix(value: datetime) -> int:
    return int(value.timestamp())


def _from_unix(value: object) -> datetime:
    return datetime.fromtimestamp(int(value), UTC)


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


class SqliteContextJournal:
    """Bounded context-checkpoint repository sharing one outer transaction backend."""

    def __init__(
        self,
        backend: SqliteJournalBackend,
        *,
        get_session: Callable[[str, str], DurableSession | None],
        get_task: Callable[[str, str], DurableTaskRun | None],
        get_agent_run: Callable[[str, str], DurableAgentRun | None],
        load_effective_records: Callable[[str, str], tuple[DurableConversationRecord, ...]],
        validate_artifact_refs: Callable[..., None],
    ) -> None:
        self.backend = backend
        self.get_session = get_session
        self.get_task = get_task
        self.get_agent_run = get_agent_run
        self.load_effective_records = load_effective_records
        self.validate_artifact_refs = validate_artifact_refs

    def put(self, workspace_id: str, checkpoint: ContextCheckpoint) -> ContextCheckpoint:
        if checkpoint.workspace_id != workspace_id:
            raise StorageError(
                StorageErrorCode.UNAVAILABLE, "context checkpoint is outside the workspace"
            )

        def work() -> ContextCheckpoint:
            existing = self.get(workspace_id, checkpoint.checkpoint_id)
            if existing is not None:
                if existing.model_dump(mode="json") != checkpoint.model_dump(mode="json"):
                    raise StorageError(
                        StorageErrorCode.UNAVAILABLE, "context checkpoint identity is immutable"
                    )
                return existing
            self._validate_scope(workspace_id, checkpoint)
            executor = self.backend.executor()
            executor.execute(
                """
                INSERT INTO context_checkpoints(
                    checkpoint_id, workspace_id, session_id, task_run_id,
                    source_agent_run_id, codec, method_version, source_start_record_id,
                    source_start_position, source_end_record_id, source_end_position,
                    retained_record_ids_json, sections_json, omitted_sections_json,
                    artifact_refs_json, input_bytes, output_bytes, request_estimate_chars,
                    created_at_unix
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    checkpoint.checkpoint_id,
                    checkpoint.workspace_id,
                    checkpoint.session_id,
                    checkpoint.task_run_id,
                    checkpoint.source_agent_run_id,
                    checkpoint.codec,
                    checkpoint.method_version,
                    checkpoint.source_start_record_id,
                    checkpoint.source_start_position,
                    checkpoint.source_end_record_id,
                    checkpoint.source_end_position,
                    _optional_json(checkpoint.retained_record_ids),
                    _optional_json(checkpoint.sections),
                    _optional_json(checkpoint.omitted_sections),
                    _optional_json(checkpoint.artifact_refs),
                    checkpoint.input_bytes,
                    checkpoint.output_bytes,
                    checkpoint.request_estimate_chars,
                    _unix(checkpoint.created_at),
                ),
            )
            for reference in checkpoint.artifact_refs:
                executor.execute(
                    f"INSERT INTO checkpoint_artifact_references("
                    f"{_CHECKPOINT_ARTIFACT_REFERENCE_COLUMNS}) VALUES (?, ?, ?, ?, ?)",
                    (
                        reference.artifact_id,
                        workspace_id,
                        checkpoint.checkpoint_id,
                        reference.role,
                        _unix(checkpoint.created_at),
                    ),
                )
            loaded = self.get(workspace_id, checkpoint.checkpoint_id)
            if loaded is None:
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE, "context checkpoint could not be read"
                )
            return loaded

        return self.backend.transact(work)

    def get(self, workspace_id: str, checkpoint_id: str) -> ContextCheckpoint | None:
        row = self.backend.read_one(
            "SELECT checkpoint_id, workspace_id, session_id, task_run_id, "
            "source_agent_run_id, codec, method_version, source_start_record_id, "
            "source_start_position, source_end_record_id, source_end_position, "
            "retained_record_ids_json, sections_json, omitted_sections_json, "
            "artifact_refs_json, input_bytes, output_bytes, request_estimate_chars, "
            "created_at_unix FROM context_checkpoints "
            "WHERE checkpoint_id = ? AND workspace_id = ?",
            (checkpoint_id, workspace_id),
        )
        return _checkpoint_from_row(row) if row is not None else None

    def list(
        self, workspace_id: str, session_id: str, *, task_run_id: str | None = None
    ) -> tuple[ContextCheckpoint, ...]:
        sql = (
            "SELECT checkpoint_id, workspace_id, session_id, task_run_id, "
            "source_agent_run_id, codec, method_version, source_start_record_id, "
            "source_start_position, source_end_record_id, source_end_position, "
            "retained_record_ids_json, sections_json, omitted_sections_json, "
            "artifact_refs_json, input_bytes, output_bytes, request_estimate_chars, "
            "created_at_unix FROM context_checkpoints "
            "WHERE workspace_id = ? AND session_id = ?"
        )
        parameters: list[object] = [workspace_id, session_id]
        if task_run_id is not None:
            sql += " AND task_run_id = ?"
            parameters.append(task_run_id)
        sql += " ORDER BY source_end_position ASC, created_at_unix ASC, checkpoint_id ASC"
        rows = self.backend.read_all(sql, tuple(parameters))
        return tuple(_checkpoint_from_row(row) for row in rows)

    def _validate_scope(self, workspace_id: str, checkpoint: ContextCheckpoint) -> None:
        session = self.get_session(workspace_id, checkpoint.session_id)
        if session is None:
            raise StorageError(StorageErrorCode.NOT_FOUND, "checkpoint Session is missing")
        if checkpoint.task_run_id is not None:
            task = self.get_task(workspace_id, checkpoint.task_run_id)
            if task is None or task.session_id != checkpoint.session_id:
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE, "checkpoint TaskRun scope is invalid"
                )
        if checkpoint.source_agent_run_id is not None:
            run = self.get_agent_run(workspace_id, checkpoint.source_agent_run_id)
            if run is None or run.session_id != checkpoint.session_id:
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE, "checkpoint AgentRun scope is invalid"
                )
        records = self.load_effective_records(workspace_id, checkpoint.session_id)
        by_position = {record.conversation_position: record for record in records}
        if checkpoint.source_end_position > session.conversation_position + 1:
            raise StorageError(StorageErrorCode.UNAVAILABLE, "checkpoint source range is invalid")
        if checkpoint.source_end_position - 1 not in by_position:
            raise StorageError(StorageErrorCode.UNAVAILABLE, "checkpoint source end is missing")
        end_record = by_position[checkpoint.source_end_position - 1]
        if end_record.record_id != checkpoint.source_end_record_id or end_record.kind != "terminal":
            raise StorageError(StorageErrorCode.UNAVAILABLE, "checkpoint must end at a closed Turn")
        if checkpoint.source_start_position:
            start_record = by_position.get(checkpoint.source_start_position)
            if start_record is None or start_record.record_id != checkpoint.source_start_record_id:
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE, "checkpoint source start is invalid"
                )
        elif checkpoint.source_start_record_id is not None:
            raise StorageError(StorageErrorCode.UNAVAILABLE, "checkpoint source start is invalid")
        valid_ids = {
            record.record_id
            for record in records
            if checkpoint.source_start_position
            <= record.conversation_position
            < checkpoint.source_end_position
        }
        if not set(checkpoint.retained_record_ids).issubset(valid_ids):
            raise StorageError(
                StorageErrorCode.UNAVAILABLE, "checkpoint retained records are invalid"
            )
        for section in checkpoint.sections:
            self._validate_section(section, checkpoint, valid_ids)
        for omission in checkpoint.omitted_sections:
            if not (
                checkpoint.source_start_position <= omission.source_start_position
                and omission.source_end_position <= checkpoint.source_end_position
            ):
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE, "checkpoint omission range is invalid"
                )
            if not set(omission.record_ids).issubset(valid_ids):
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE, "checkpoint omission records are invalid"
                )
        self.validate_artifact_refs(
            workspace_id,
            checkpoint.artifact_refs,
            session_id=checkpoint.session_id,
            task_run_id=checkpoint.task_run_id,
            require_available=False,
        )

    @staticmethod
    def _validate_section(
        section: ContextCheckpointSection,
        checkpoint: ContextCheckpoint,
        valid_ids: set[str],
    ) -> None:
        if not (
            checkpoint.source_start_position <= section.source_start_position
            and section.source_end_position <= checkpoint.source_end_position
        ):
            raise StorageError(StorageErrorCode.UNAVAILABLE, "checkpoint section range is invalid")
        if not set(reference.artifact_id for reference in section.artifact_refs).issubset(
            {reference.artifact_id for reference in checkpoint.artifact_refs}
        ):
            raise StorageError(
                StorageErrorCode.UNAVAILABLE, "checkpoint section artifact is invalid"
            )
        if not valid_ids:
            raise StorageError(StorageErrorCode.UNAVAILABLE, "checkpoint source range is empty")


def _checkpoint_from_row(row: tuple[object, ...]) -> ContextCheckpoint:
    try:
        retained = json.loads(str(row[11]))
        sections = json.loads(str(row[12]))
        omissions = json.loads(str(row[13]))
        artifacts = json.loads(str(row[14]))
        if not all(isinstance(value, list) for value in (retained, sections, omissions, artifacts)):
            raise ValueError("checkpoint JSON columns are not lists")
        return ContextCheckpoint(
            checkpoint_id=str(row[0]),
            workspace_id=str(row[1]),
            session_id=str(row[2]),
            task_run_id=str(row[3]) if row[3] is not None else None,
            source_agent_run_id=str(row[4]) if row[4] is not None else None,
            codec=str(row[5]),
            method_version=str(row[6]),
            source_start_record_id=str(row[7]) if row[7] is not None else None,
            source_start_position=int(row[8]),
            source_end_record_id=str(row[9]),
            source_end_position=int(row[10]),
            retained_record_ids=tuple(str(item) for item in retained),
            sections=tuple(ContextCheckpointSection.model_validate(item) for item in sections),
            omitted_sections=tuple(
                ContextCheckpointOmission.model_validate(item) for item in omissions
            ),
            artifact_refs=tuple(ArtifactReference.model_validate(item) for item in artifacts),
            input_bytes=int(row[15]),
            output_bytes=int(row[16]),
            request_estimate_chars=int(row[17]),
            created_at=_from_unix(row[18]),
        )
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise StorageError(StorageErrorCode.NEEDS_REPAIR, "context checkpoint is invalid") from exc
