"""SQLite persistence for AgentRun permission snapshots and capability grants."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime

from morrow.adapters.state.transaction import SqliteJournalBackend
from morrow.core.capabilities import AccessScope, ApprovalMode, ProcessIsolation
from morrow.core.domain import (
    AgentRunSnapshot,
    DurableAgentRun,
    DurableTaskRun,
    DurableTurn,
    SourceRevisionRef,
    canonical_json_bytes,
)
from morrow.core.permissions import (
    CapabilityGrant,
    CapabilityIsolation,
    CapabilityName,
    GrantSource,
    IsolationLabel,
    PermissionSnapshot,
    capability_grant_digest,
)
from morrow.core.store import StorageError, StorageErrorCode

_AGENT_COLUMNS = (
    "agent_run_id, turn_id, session_id, resume_of_agent_run_id, snapshot_json, created_at_unix, "
    "permission_snapshot_id"
)
_GRANT_COLUMNS = (
    "grant_id, workspace_id, task_run_id, agent_run_id, capabilities_json, granted_by, "
    "command_id, reason, preview_digest, policy_version, schema_version, created_at_unix, "
    "expires_at_unix, revoked_at_unix, revocation_reason, row_version"
)
_GRANT_SELECT = f"g.{_GRANT_COLUMNS.replace(', ', ', g.')}"
_SNAPSHOT_COLUMNS = (
    "permission_snapshot_id, workspace_id, session_id, task_run_id, turn_id, agent_run_id, "
    "access_scope, approval_mode, process_isolation, workspace_root_digest, workspace_read_only, "
    "tool_schema_digest, run_policy_digest, permission_profile_digest, policy_version, "
    "schema_version, source_revisions_json, grant_id, grant_digest, granted_capabilities_json, "
    "capability_isolations_json, created_at_unix"
)
_SNAPSHOT_SELECT = f"p.{_SNAPSHOT_COLUMNS.replace(', ', ', p.')}"


def _unix(value: datetime) -> int:
    return int(value.timestamp())


def _from_unix(value: object) -> datetime:
    return datetime.fromtimestamp(int(value), UTC)


def _optional_unix(value: datetime | None) -> int | None:
    return _unix(value) if value is not None else None


class SqliteRunPermissionJournal:
    """Bounded AgentRun and permission repository sharing one outer transaction backend."""

    def __init__(
        self,
        backend: SqliteJournalBackend,
        *,
        get_turn: Callable[[str, str], DurableTurn | None],
        get_task: Callable[[str, str], DurableTaskRun | None],
    ) -> None:
        self.backend = backend
        self.get_turn = get_turn
        self.get_task = get_task

    def create_agent_run(self, workspace_id: str, run: DurableAgentRun) -> DurableAgentRun:
        def work() -> DurableAgentRun:
            self._insert_agent_run(workspace_id, run)
            return self._agent_run_or_raise(workspace_id, run.agent_run_id)

        return self.backend.transact(work)

    def create_agent_run_with_permission_snapshot(
        self,
        workspace_id: str,
        run: DurableAgentRun,
        permission_snapshot: PermissionSnapshot,
    ) -> DurableAgentRun:
        if run.permission_snapshot_id is not None:
            raise StorageError(
                StorageErrorCode.UNAVAILABLE, "AgentRun permission snapshot is already linked"
            )
        if permission_snapshot.agent_run_id != run.agent_run_id:
            raise StorageError(
                StorageErrorCode.UNAVAILABLE, "permission snapshot does not match the AgentRun"
            )

        def work() -> DurableAgentRun:
            self._insert_agent_run(workspace_id, run)
            self._insert_permission_snapshot(workspace_id, permission_snapshot)
            self._link_agent_run_permission_snapshot(
                workspace_id, run.agent_run_id, permission_snapshot.permission_snapshot_id
            )
            return self._agent_run_or_raise(workspace_id, run.agent_run_id)

        return self.backend.transact(work)

    def freeze_agent_run_permission_snapshot(
        self,
        workspace_id: str,
        agent_run_id: str,
        permission_snapshot: PermissionSnapshot,
    ) -> DurableAgentRun:
        if permission_snapshot.agent_run_id != agent_run_id:
            raise StorageError(
                StorageErrorCode.UNAVAILABLE, "permission snapshot does not match the AgentRun"
            )

        def work() -> DurableAgentRun:
            run = self.get_agent_run(workspace_id, agent_run_id)
            if run is None:
                raise StorageError(StorageErrorCode.NOT_FOUND, "operational run is missing")
            if run.permission_snapshot_id is not None:
                if run.permission_snapshot_id == permission_snapshot.permission_snapshot_id:
                    return run
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE,
                    "AgentRun permission snapshot cannot be replaced",
                )
            self._insert_permission_snapshot(workspace_id, permission_snapshot)
            self._link_agent_run_permission_snapshot(
                workspace_id, agent_run_id, permission_snapshot.permission_snapshot_id
            )
            return self._agent_run_or_raise(workspace_id, agent_run_id)

        return self.backend.transact(work)

    def get_agent_run(self, workspace_id: str, agent_run_id: str) -> DurableAgentRun | None:
        row = self.backend.read_one(
            "SELECT r.agent_run_id, r.turn_id, r.session_id, r.resume_of_agent_run_id, "
            "r.snapshot_json, r.created_at_unix, r.permission_snapshot_id FROM agent_runs r "
            "JOIN sessions s ON s.session_id = r.session_id "
            "WHERE r.agent_run_id = ? AND s.workspace_id = ?",
            (agent_run_id, workspace_id),
        )
        return _agent_from_row(row) if row is not None else None

    def list_session_agent_runs(
        self, workspace_id: str, session_id: str
    ) -> tuple[DurableAgentRun, ...]:
        rows = self.backend.read_all(
            "SELECT r.agent_run_id, r.turn_id, r.session_id, r.resume_of_agent_run_id, "
            "r.snapshot_json, r.created_at_unix, r.permission_snapshot_id FROM agent_runs r "
            "JOIN sessions s ON s.session_id = r.session_id "
            "WHERE r.session_id = ? AND s.workspace_id = ? "
            "ORDER BY r.created_at_unix ASC, r.agent_run_id ASC",
            (session_id, workspace_id),
        )
        return tuple(_agent_from_row(row) for row in rows)

    def get_permission_snapshot(
        self, workspace_id: str, permission_snapshot_id: str
    ) -> PermissionSnapshot | None:
        row = self.backend.read_one(
            f"SELECT {_SNAPSHOT_SELECT} FROM permission_snapshots p "
            "WHERE p.permission_snapshot_id = ? AND p.workspace_id = ?",
            (permission_snapshot_id, workspace_id),
        )
        return _permission_snapshot_from_row(row) if row is not None else None

    def get_permission_snapshot_for_run(
        self, workspace_id: str, agent_run_id: str
    ) -> PermissionSnapshot | None:
        row = self.backend.read_one(
            f"SELECT {_SNAPSHOT_SELECT} FROM permission_snapshots p "
            "WHERE p.agent_run_id = ? AND p.workspace_id = ?",
            (agent_run_id, workspace_id),
        )
        return _permission_snapshot_from_row(row) if row is not None else None

    def list_permission_snapshots(
        self, workspace_id: str, *, agent_run_id: str | None = None
    ) -> tuple[PermissionSnapshot, ...]:
        sql = f"SELECT {_SNAPSHOT_SELECT} FROM permission_snapshots p WHERE p.workspace_id = ?"
        parameters: list[object] = [workspace_id]
        if agent_run_id is not None:
            sql += " AND p.agent_run_id = ?"
            parameters.append(agent_run_id)
        sql += " ORDER BY p.created_at_unix ASC, p.permission_snapshot_id ASC"
        return tuple(
            _permission_snapshot_from_row(row)
            for row in self.backend.read_all(sql, tuple(parameters))
        )

    def put_permission_snapshot(
        self, workspace_id: str, permission_snapshot: PermissionSnapshot
    ) -> PermissionSnapshot:
        def work() -> PermissionSnapshot:
            self._insert_permission_snapshot(workspace_id, permission_snapshot)
            loaded = self.get_permission_snapshot(
                workspace_id, permission_snapshot.permission_snapshot_id
            )
            if loaded is None:
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE,
                    "operational permission snapshot could not be read",
                )
            return loaded

        return self.backend.transact(work)

    def link_agent_run_permission_snapshot(
        self, workspace_id: str, agent_run_id: str, permission_snapshot_id: str
    ) -> DurableAgentRun:
        def work() -> DurableAgentRun:
            self._link_agent_run_permission_snapshot(
                workspace_id, agent_run_id, permission_snapshot_id
            )
            return self._agent_run_or_raise(workspace_id, agent_run_id)

        return self.backend.transact(work)

    def put_capability_grant(self, workspace_id: str, grant: CapabilityGrant) -> CapabilityGrant:
        self._validate_grant_scope(workspace_id, grant)

        def work() -> CapabilityGrant:
            self._validate_grant_scope(workspace_id, grant)
            run = self.get_agent_run(workspace_id, grant.agent_run_id)
            if run is not None and run.permission_snapshot_id is not None:
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE,
                    "operational capability grant cannot be added after the AgentRun snapshot is frozen",
                )
            self.backend.executor().execute(
                f"INSERT INTO capability_grants({_GRANT_COLUMNS}) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    grant.grant_id,
                    grant.workspace_id,
                    grant.task_run_id,
                    grant.agent_run_id,
                    canonical_json_bytes([value.value for value in grant.capabilities]).decode(
                        "utf-8"
                    ),
                    grant.granted_by.value,
                    grant.command_id,
                    grant.reason,
                    grant.preview_digest,
                    grant.policy_version,
                    grant.schema_version,
                    _unix(grant.created_at),
                    _unix(grant.expires_at),
                    _optional_unix(grant.revoked_at),
                    grant.revocation_reason,
                    grant.row_version,
                ),
            )
            loaded = self.get_capability_grant(workspace_id, grant.grant_id)
            if loaded is None:
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE, "operational capability grant could not be read"
                )
            return loaded

        return self.backend.transact(work)

    def get_capability_grant(self, workspace_id: str, grant_id: str) -> CapabilityGrant | None:
        row = self.backend.read_one(
            f"SELECT {_GRANT_SELECT} FROM capability_grants g "
            "WHERE g.grant_id = ? AND g.workspace_id = ?",
            (grant_id, workspace_id),
        )
        return _grant_from_row(row) if row is not None else None

    def list_capability_grants(
        self, workspace_id: str, *, agent_run_id: str | None = None
    ) -> tuple[CapabilityGrant, ...]:
        sql = f"SELECT {_GRANT_SELECT} FROM capability_grants g WHERE g.workspace_id = ?"
        parameters: list[object] = [workspace_id]
        if agent_run_id is not None:
            sql += " AND g.agent_run_id = ?"
            parameters.append(agent_run_id)
        sql += " ORDER BY g.created_at_unix ASC, g.grant_id ASC"
        return tuple(_grant_from_row(row) for row in self.backend.read_all(sql, tuple(parameters)))

    def save_capability_grant(
        self,
        workspace_id: str,
        grant: CapabilityGrant,
        *,
        expected_row_version: int,
    ) -> CapabilityGrant:
        if grant.workspace_id != workspace_id:
            raise StorageError(
                StorageErrorCode.UNAVAILABLE,
                "operational capability grant is outside the workspace",
            )

        def work() -> CapabilityGrant:
            existing = self.get_capability_grant(workspace_id, grant.grant_id)
            if existing is None:
                raise StorageError(
                    StorageErrorCode.NOT_FOUND, "operational capability grant is missing"
                )
            if existing.row_version != expected_row_version:
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE,
                    "operational capability grant row version is stale",
                )
            if grant.row_version != expected_row_version + 1:
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE,
                    "operational capability grant row version is stale",
                )
            immutable_fields = (
                "grant_id",
                "workspace_id",
                "task_run_id",
                "agent_run_id",
                "capabilities",
                "granted_by",
                "command_id",
                "reason",
                "preview_digest",
                "policy_version",
                "schema_version",
                "created_at",
                "expires_at",
            )
            if any(getattr(existing, field) != getattr(grant, field) for field in immutable_fields):
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE,
                    "operational capability grant authority is immutable",
                )
            if existing.revoked_at is not None:
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE,
                    "operational capability grant is already revoked",
                )
            if grant.revoked_at is None or grant.revocation_reason is None:
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE,
                    "operational capability grant revocation is incomplete",
                )
            self._validate_grant_scope(workspace_id, grant)
            self.backend.executor().execute(
                """
                UPDATE capability_grants
                SET revoked_at_unix = ?, revocation_reason = ?, row_version = ?
                WHERE grant_id = ? AND workspace_id = ? AND row_version = ?
                """,
                (
                    _optional_unix(grant.revoked_at),
                    grant.revocation_reason,
                    grant.row_version,
                    grant.grant_id,
                    workspace_id,
                    expected_row_version,
                ),
            )
            loaded = self.get_capability_grant(workspace_id, grant.grant_id)
            if loaded is None or loaded.row_version != grant.row_version:
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE,
                    "operational capability grant row version is stale",
                )
            return loaded

        return self.backend.transact(work)

    def _validate_grant_scope(self, workspace_id: str, grant: CapabilityGrant) -> None:
        if grant.workspace_id != workspace_id:
            raise StorageError(
                StorageErrorCode.UNAVAILABLE,
                "operational capability grant is outside the workspace",
            )
        task = self.get_task(workspace_id, grant.task_run_id)
        run = self.get_agent_run(workspace_id, grant.agent_run_id)
        if task is None or run is None:
            raise StorageError(StorageErrorCode.NOT_FOUND, "operational grant subject is missing")
        turn = self.get_turn(workspace_id, run.turn_id)
        if (
            task.workspace_id != workspace_id
            or run.session_id != task.session_id
            or turn is None
            or turn.task_run_id != task.task_run_id
        ):
            raise StorageError(
                StorageErrorCode.UNAVAILABLE, "operational capability grant subject is mismatched"
            )

    def _insert_agent_run(self, workspace_id: str, run: DurableAgentRun) -> None:
        turn = self.get_turn(workspace_id, run.turn_id)
        if turn is None or turn.session_id != run.session_id:
            raise StorageError(
                StorageErrorCode.UNAVAILABLE, "operational run does not belong to the turn"
            )
        if run.permission_snapshot_id is not None:
            raise StorageError(
                StorageErrorCode.UNAVAILABLE,
                "permission snapshot must be linked after the AgentRun is created",
            )
        if run.resume_of_agent_run_id is not None:
            previous = self.get_agent_run(workspace_id, run.resume_of_agent_run_id)
            if previous is None or previous.turn_id != run.turn_id:
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE,
                    "operational run resume target is missing",
                )
        snapshot = canonical_json_bytes(run.snapshot.model_dump(mode="json")).decode("utf-8")
        self.backend.executor().execute(
            f"INSERT INTO agent_runs({_AGENT_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                run.agent_run_id,
                run.turn_id,
                run.session_id,
                run.resume_of_agent_run_id,
                snapshot,
                _unix(run.created_at),
                None,
            ),
        )

    def _agent_run_or_raise(self, workspace_id: str, agent_run_id: str) -> DurableAgentRun:
        value = self.get_agent_run(workspace_id, agent_run_id)
        if value is None:
            raise StorageError(StorageErrorCode.UNAVAILABLE, "operational run could not be read")
        return value

    def _insert_permission_snapshot(
        self, workspace_id: str, permission_snapshot: PermissionSnapshot
    ) -> None:
        if permission_snapshot.workspace_id != workspace_id:
            raise StorageError(
                StorageErrorCode.UNAVAILABLE,
                "operational permission snapshot is outside the workspace",
            )
        run = self.get_agent_run(workspace_id, permission_snapshot.agent_run_id)
        task = self.get_task(workspace_id, permission_snapshot.task_run_id)
        turn = self.get_turn(workspace_id, permission_snapshot.turn_id)
        if (
            run is None
            or task is None
            or turn is None
            or run.session_id != permission_snapshot.session_id
            or run.turn_id != permission_snapshot.turn_id
            or turn.session_id != permission_snapshot.session_id
            or turn.task_run_id != permission_snapshot.task_run_id
            or task.session_id != permission_snapshot.session_id
        ):
            raise StorageError(
                StorageErrorCode.UNAVAILABLE,
                "operational permission snapshot subjects are mismatched",
            )
        if run.permission_snapshot_id is not None:
            raise StorageError(
                StorageErrorCode.UNAVAILABLE,
                "AgentRun permission snapshot is already frozen",
            )
        if permission_snapshot.grant_id is not None:
            grant = self.get_capability_grant(workspace_id, permission_snapshot.grant_id)
            if (
                grant is None
                or grant.workspace_id != permission_snapshot.workspace_id
                or grant.task_run_id != permission_snapshot.task_run_id
                or grant.agent_run_id != permission_snapshot.agent_run_id
                or grant.capabilities != permission_snapshot.granted_capabilities
                or not grant.is_active(permission_snapshot.created_at)
                or permission_snapshot.grant_digest != capability_grant_digest(grant)
            ):
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE,
                    "permission snapshot grant evidence is mismatched",
                )
        if (
            permission_snapshot.tool_schema_digest != run.snapshot.tool_schema_digest
            or permission_snapshot.run_policy_digest != run.snapshot.run_policy_digest
            or permission_snapshot.permission_profile_digest
            != run.snapshot.permission_profile_digest
            or permission_snapshot.source_revisions != run.snapshot.source_revisions
        ):
            raise StorageError(
                StorageErrorCode.UNAVAILABLE,
                "permission snapshot does not match the frozen AgentRun snapshot",
            )
        self.backend.executor().execute(
            f"INSERT INTO permission_snapshots({_SNAPSHOT_COLUMNS}) "
            f"VALUES ({', '.join('?' for _ in range(22))})",
            (
                permission_snapshot.permission_snapshot_id,
                permission_snapshot.workspace_id,
                permission_snapshot.session_id,
                permission_snapshot.task_run_id,
                permission_snapshot.turn_id,
                permission_snapshot.agent_run_id,
                permission_snapshot.access_scope.value,
                permission_snapshot.approval_mode.value,
                permission_snapshot.process_isolation.value,
                permission_snapshot.workspace_root_digest,
                int(permission_snapshot.workspace_read_only),
                permission_snapshot.tool_schema_digest,
                permission_snapshot.run_policy_digest,
                permission_snapshot.permission_profile_digest,
                permission_snapshot.policy_version,
                permission_snapshot.schema_version,
                canonical_json_bytes(
                    [item.model_dump(mode="json") for item in permission_snapshot.source_revisions]
                ).decode("utf-8"),
                permission_snapshot.grant_id,
                permission_snapshot.grant_digest,
                canonical_json_bytes(
                    [item.value for item in permission_snapshot.granted_capabilities]
                ).decode("utf-8"),
                canonical_json_bytes(
                    [
                        item.model_dump(mode="json")
                        for item in permission_snapshot.capability_isolations
                    ]
                ).decode("utf-8"),
                _unix(permission_snapshot.created_at),
            ),
        )

    def _link_agent_run_permission_snapshot(
        self, workspace_id: str, agent_run_id: str, permission_snapshot_id: str
    ) -> None:
        run = self.get_agent_run(workspace_id, agent_run_id)
        snapshot = self.get_permission_snapshot(workspace_id, permission_snapshot_id)
        if (
            run is None
            or snapshot is None
            or snapshot.agent_run_id != agent_run_id
            or snapshot.session_id != run.session_id
            or snapshot.turn_id != run.turn_id
        ):
            raise StorageError(
                StorageErrorCode.UNAVAILABLE,
                "permission snapshot does not belong to the AgentRun",
            )
        if run.permission_snapshot_id is not None:
            if run.permission_snapshot_id == permission_snapshot_id:
                return
            raise StorageError(
                StorageErrorCode.UNAVAILABLE,
                "AgentRun permission snapshot cannot be replaced",
            )
        self.backend.executor().execute(
            "UPDATE agent_runs SET permission_snapshot_id = ? "
            "WHERE agent_run_id = ? AND permission_snapshot_id IS NULL",
            (permission_snapshot_id, agent_run_id),
        )


def _agent_from_row(row: tuple[object, ...]) -> DurableAgentRun:
    snapshot = AgentRunSnapshot.model_validate(json.loads(str(row[4])))
    return DurableAgentRun(
        agent_run_id=str(row[0]),
        turn_id=str(row[1]),
        session_id=str(row[2]),
        resume_of_agent_run_id=str(row[3]) if row[3] is not None else None,
        snapshot=snapshot,
        created_at=_from_unix(row[5]),
        permission_snapshot_id=str(row[6]) if row[6] is not None else None,
    )


def _grant_from_row(row: tuple[object, ...]) -> CapabilityGrant:
    try:
        capabilities_raw = json.loads(str(row[4]))
        if not isinstance(capabilities_raw, list):
            raise ValueError("grant capabilities are not a list")
        return CapabilityGrant(
            grant_id=str(row[0]),
            workspace_id=str(row[1]),
            task_run_id=str(row[2]),
            agent_run_id=str(row[3]),
            capabilities=tuple(CapabilityName(str(value)) for value in capabilities_raw),
            granted_by=GrantSource(str(row[5])),
            command_id=str(row[6]),
            reason=str(row[7]),
            preview_digest=str(row[8]),
            policy_version=str(row[9]),
            schema_version=int(row[10]),
            created_at=_from_unix(row[11]),
            expires_at=_from_unix(row[12]),
            revoked_at=_from_unix(row[13]) if row[13] is not None else None,
            revocation_reason=str(row[14]) if row[14] is not None else None,
            row_version=int(row[15]),
        )
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise StorageError(
            StorageErrorCode.NEEDS_REPAIR, "operational capability grant is invalid"
        ) from exc


def _permission_snapshot_from_row(row: tuple[object, ...]) -> PermissionSnapshot:
    try:
        source_revisions = json.loads(str(row[16]))
        granted_capabilities = json.loads(str(row[19]))
        capability_isolations = json.loads(str(row[20]))
        if row[10] not in (0, 1):
            raise ValueError("permission snapshot read-only flag is invalid")
        if not all(
            isinstance(value, list)
            for value in (source_revisions, granted_capabilities, capability_isolations)
        ):
            raise ValueError("permission snapshot JSON columns are not lists")
        return PermissionSnapshot(
            permission_snapshot_id=str(row[0]),
            workspace_id=str(row[1]),
            session_id=str(row[2]),
            task_run_id=str(row[3]),
            turn_id=str(row[4]),
            agent_run_id=str(row[5]),
            access_scope=AccessScope(str(row[6])),
            approval_mode=ApprovalMode(str(row[7])),
            process_isolation=ProcessIsolation(str(row[8])),
            workspace_root_digest=str(row[9]),
            workspace_read_only=bool(row[10]),
            tool_schema_digest=str(row[11]),
            run_policy_digest=str(row[12]),
            permission_profile_digest=str(row[13]),
            policy_version=str(row[14]),
            schema_version=int(row[15]),
            source_revisions=tuple(
                SourceRevisionRef.model_validate(item) for item in source_revisions
            ),
            grant_id=str(row[17]) if row[17] is not None else None,
            grant_digest=str(row[18]) if row[18] is not None else None,
            granted_capabilities=tuple(
                CapabilityName(str(value)) for value in granted_capabilities
            ),
            capability_isolations=tuple(
                CapabilityIsolation(
                    capability=CapabilityName(str(item["capability"])),
                    isolation=IsolationLabel(str(item["isolation"])),
                )
                for item in capability_isolations
            ),
            created_at=_from_unix(row[21]),
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise StorageError(
            StorageErrorCode.NEEDS_REPAIR, "operational permission snapshot is invalid"
        ) from exc
