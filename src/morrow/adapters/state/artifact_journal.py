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
    "sha256, byte_size, excerpt, provenance_json, row_version, created_at_unix, updated_at_unix, "
    "text_safety_profile, contract_json, producer_node_run_id, output_slot"
)


def _unix(value: datetime) -> int:
    return int(value.timestamp())


def _from_unix(value: object) -> datetime:
    return datetime.fromtimestamp(int(value), UTC)


def _embedded_list(value: object, *, label: str) -> list[object]:
    if value is None or value == "":
        return []
    try:
        parsed = json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise StorageError(StorageErrorCode.NEEDS_REPAIR, f"{label} is corrupt") from exc
    if not isinstance(parsed, list):
        raise StorageError(StorageErrorCode.NEEDS_REPAIR, f"{label} is not a list")
    return parsed


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
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
                    metadata.text_safety_profile.value,
                    metadata.contract.model_dump_json() if metadata.contract else None,
                    metadata.producer_node_run_id,
                    metadata.output_slot,
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
                "text_safety_profile",
                "contract",
                "producer_node_run_id",
                "output_slot",
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
        sql = "SELECT artifact_id, references_json FROM artifacts WHERE workspace_id = ?"
        parameters: list[object] = [workspace_id]
        if artifact_id is not None:
            sql += " AND artifact_id = ?"
            parameters.append(artifact_id)
        rows = self.backend.read_all(sql, tuple(parameters))
        references: set[tuple[str, str, str, str]] = set()
        for row in rows:
            current_artifact_id = str(row[0])
            for item in _embedded_list(row[1], label="artifact references"):
                if not isinstance(item, dict):
                    raise StorageError(
                        StorageErrorCode.NEEDS_REPAIR, "artifact reference is not an object"
                    )
                try:
                    references.add(
                        (
                            current_artifact_id,
                            str(item["owner_kind"]),
                            str(item["owner_id"]),
                            str(item["role"]),
                        )
                    )
                except (KeyError, TypeError) as exc:
                    raise StorageError(
                        StorageErrorCode.NEEDS_REPAIR, "artifact reference is incomplete"
                    ) from exc
        # Checkpoint rows retain their complete input for direct reconstruction;
        # include them as a fallback for newly-created checkpoints and de-duplicate
        # migrated v44 rows already copied into artifacts.references_json.
        checkpoint_sql = (
            "SELECT checkpoint_id, artifact_refs_json FROM context_checkpoints "
            "WHERE workspace_id = ?"
        )
        checkpoint_rows = self.backend.read_all(checkpoint_sql, (workspace_id,))
        for checkpoint_id, refs_json in checkpoint_rows:
            for item in _embedded_list(refs_json, label="checkpoint artifact references"):
                if not isinstance(item, dict):
                    raise StorageError(
                        StorageErrorCode.NEEDS_REPAIR,
                        "checkpoint artifact reference is not an object",
                    )
                try:
                    current_artifact_id = str(item["artifact_id"])
                    if artifact_id is None or current_artifact_id == artifact_id:
                        references.add(
                            (
                                current_artifact_id,
                                "context_checkpoint",
                                str(checkpoint_id),
                                str(item["role"]),
                            )
                        )
                except (KeyError, TypeError) as exc:
                    raise StorageError(
                        StorageErrorCode.NEEDS_REPAIR,
                        "checkpoint artifact reference is incomplete",
                    ) from exc
        workflow_sql = (
            "SELECT b.artifact_id, b.workflow_run_id, b.node_run_id, b.name FROM workflow_artifact_bindings b "
            "JOIN workflow_runs r USING(workflow_run_id) WHERE r.workspace_id=?"
        )
        workflow_args = [workspace_id]
        if artifact_id is not None:
            workflow_sql += " AND b.artifact_id=?"
            workflow_args.append(artifact_id)
        references.update(
            (row[0], "workflow", row[2] or row[1], row[3])
            for row in self.backend.read_all(workflow_sql, tuple(workflow_args))
        )
        import_sql = (
            "SELECT i.artifact_id, i.workflow_run_id, i.source_node_id, i.output_slot "
            "FROM workflow_run_artifact_imports i JOIN workflow_runs r "
            "USING(workflow_run_id) WHERE r.workspace_id=?"
        )
        import_args = [workspace_id]
        if artifact_id is not None:
            import_sql += " AND i.artifact_id=?"
            import_args.append(artifact_id)
        references.update(
            (row[0], "workflow_import", row[1], f"{row[2]}.{row[3]}")
            for row in self.backend.read_all(import_sql, tuple(import_args))
        )
        attachment_sql = (
            "SELECT attachment_id, blob_refs_json FROM chat_attachments "
            "WHERE workspace_id=? AND state!='released'"
        )
        for attachment_identity, refs_json in self.backend.read_all(
            attachment_sql, (workspace_id,)
        ):
            for item in _embedded_list(refs_json, label="attachment artifact references"):
                if not isinstance(item, dict):
                    raise StorageError(
                        StorageErrorCode.NEEDS_REPAIR,
                        "attachment artifact reference is not an object",
                    )
                try:
                    current_artifact_id = str(item["artifact_id"])
                    if artifact_id is None or current_artifact_id == artifact_id:
                        references.add(
                            (
                                current_artifact_id,
                                "chat_attachment",
                                str(attachment_identity),
                                str(item["role"]),
                            )
                        )
                except (KeyError, TypeError) as exc:
                    raise StorageError(
                        StorageErrorCode.NEEDS_REPAIR,
                        "attachment artifact reference is incomplete",
                    ) from exc
        mcp_sql = (
            "SELECT tool_execution_id, mcp_artifact_links_json FROM tool_executions "
            "WHERE workspace_id=?"
        )
        for execution_id, refs_json in self.backend.read_all(mcp_sql, (workspace_id,)):
            for item in _embedded_list(refs_json, label="MCP Artifact links"):
                if not isinstance(item, dict):
                    raise StorageError(
                        StorageErrorCode.NEEDS_REPAIR, "MCP Artifact link is not an object"
                    )
                try:
                    current_artifact_id = item["artifact_id"]
                    role = item["role"]
                    if (
                        not isinstance(current_artifact_id, str)
                        or not current_artifact_id
                        or not isinstance(role, str)
                        or not role
                    ):
                        raise ValueError("MCP Artifact link identity is invalid")
                except (KeyError, TypeError) as exc:
                    raise StorageError(
                        StorageErrorCode.NEEDS_REPAIR, "MCP Artifact link is incomplete"
                    ) from exc
                except ValueError as exc:
                    raise StorageError(
                        StorageErrorCode.NEEDS_REPAIR, "MCP Artifact link is invalid"
                    ) from exc
                if artifact_id is None or current_artifact_id == artifact_id:
                    references.add((current_artifact_id, "mcp_result", str(execution_id), role))
        planning_sql = "SELECT planning_binding_id, artifact_ids_json FROM workflow_planning_bindings WHERE workspace_id=?"
        for binding_id, refs_json in self.backend.read_all(planning_sql, (workspace_id,)):
            for current_artifact_id in _embedded_list(
                refs_json, label="planning artifact references"
            ):
                current_artifact_id = str(current_artifact_id)
                if artifact_id is None or current_artifact_id == artifact_id:
                    references.add((current_artifact_id, "workflow_plan", str(binding_id), "input"))
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
        grouped: dict[str, list[dict[str, object]]] = {}
        for reference in references:
            artifact = self.backend.read_one(
                "SELECT references_json, workspace_id FROM artifacts WHERE artifact_id=?",
                (reference.artifact_id,),
            )
            if artifact is None:
                raise StorageError(StorageErrorCode.NOT_FOUND, "referenced Artifact is missing")
            if str(artifact[1]) != workspace_id:
                raise StorageError(
                    StorageErrorCode.IDENTITY_MISMATCH,
                    "referenced Artifact is outside the workspace",
                )
            grouped.setdefault(reference.artifact_id, []).append(
                {
                    "owner_kind": owner_kind,
                    "owner_id": owner_id,
                    "role": reference.role,
                    "created_at_unix": _unix(created_at),
                }
            )
        artifact_ids = {
            str(row[0])
            for row in self.backend.read_all(
                "SELECT artifact_id FROM artifacts WHERE workspace_id=? AND references_json IS NOT NULL",
                (workspace_id,),
            )
        }
        artifact_ids.update(grouped)
        for identity in artifact_ids:
            row = self.backend.read_one(
                "SELECT references_json FROM artifacts WHERE workspace_id=? AND artifact_id=?",
                (workspace_id, identity),
            )
            if row is None:
                continue
            current = _embedded_list(row[0], label="artifact references")
            current = [
                item
                for item in current
                if not (
                    isinstance(item, dict)
                    and item.get("owner_kind") == owner_kind
                    and item.get("owner_id") == owner_id
                )
            ]
            current.extend(grouped.get(identity, ()))
            # Keep one copy for a repeated ArtifactReference in a payload.
            unique: list[object] = []
            for item in current:
                if item not in unique:
                    unique.append(item)
            executor.execute(
                "UPDATE artifacts SET references_json=? WHERE workspace_id=? AND artifact_id=?",
                (canonical_json_bytes(unique).decode("utf-8"), workspace_id, identity),
            )

    def validate_scope(self, workspace_id: str, metadata: ArtifactMetadata) -> None:
        if metadata.producer_node_run_id is not None:
            owner = self.backend.read_one(
                "SELECT n.workspace_id, n.leaf_session_id, n.leaf_task_run_id "
                "FROM workflow_node_runs n WHERE n.node_run_id=? "
                "AND n.leaf_session_id IS NOT NULL AND n.leaf_task_run_id IS NOT NULL",
                (metadata.producer_node_run_id,),
            )
            if owner is None:
                # The Direct invoking-session adapter deliberately has no
                # standalone leaf-ownership row: its admitted NodeRun points
                # at the Workflow's exact root Session/Task pair.
                direct = self.backend.read_one(
                    "SELECT n.workspace_id, n.body_json, r.root_task_run_id, t.session_id "
                    "FROM workflow_node_runs n JOIN workflow_runs r USING(workflow_run_id) "
                    "JOIN task_runs t ON t.task_run_id=r.root_task_run_id "
                    "WHERE n.node_run_id=?",
                    (metadata.producer_node_run_id,),
                )
                if direct is not None:
                    from morrow.core.workflows.runs import NodeRun

                    node = NodeRun.model_validate_json(direct[1])
                    if (node.leaf_task_run_id, node.conversation_session_id) == (
                        direct[2],
                        direct[3],
                    ):
                        owner = (direct[0], direct[3], direct[2])
            if owner != (workspace_id, metadata.session_id, metadata.task_run_id):
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE, "Workflow Artifact producer scope mismatch"
                )
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
            text_safety_profile=str(row[15]),
            contract=json.loads(row[16]) if row[16] else None,
            producer_node_run_id=row[17],
            output_slot=row[18],
        )
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise StorageError(
            StorageErrorCode.NEEDS_REPAIR, "operational artifact metadata is invalid"
        ) from exc
