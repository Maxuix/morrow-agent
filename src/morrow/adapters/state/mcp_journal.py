"""SQLite MCP Server/Catalog and future run-evidence repository for v16."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from morrow.adapters.state.migrations_v14_skills import GLOBAL_SCOPE_ID
from morrow.adapters.state.transaction import SqliteJournalBackend
from morrow.core.domain import sha256_digest
from morrow.core.mcp import (
    McpApprovalMode,
    McpCatalogSnapshot,
    McpCatalogStatus,
    McpHandshake,
    McpLaunchSnapshot,
    McpResultArtifactLink,
    McpServerDefinition,
    McpToolCatalogEntry,
    McpToolCatalogStatus,
    McpToolSnapshot,
    mcp_server_config_digest,
)
from morrow.core.models import ToolEffect
from morrow.core.store import StorageError, StorageErrorCode


def _json(value: object) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise StorageError(StorageErrorCode.UNAVAILABLE, "MCP durable value is not JSON") from exc


def _load_json(value: object, *, fallback: object | None = None) -> object:
    if value is None:
        return fallback
    try:
        return json.loads(str(value))
    except (TypeError, ValueError) as exc:
        raise StorageError(StorageErrorCode.NEEDS_REPAIR, "MCP durable JSON is corrupt") from exc


def _unix(value: datetime) -> int:
    return int(value.timestamp())


def _from_unix(value: object) -> datetime:
    return datetime.fromtimestamp(int(value), UTC)


def _stored_scope_id(scope_id: str | None) -> str:
    return GLOBAL_SCOPE_ID if scope_id is None else scope_id


def _domain_scope_id(scope_id: str) -> str | None:
    return None if scope_id == GLOBAL_SCOPE_ID else scope_id


def _scope_fields(definition: McpServerDefinition) -> tuple[str, str]:
    return definition.scope, _stored_scope_id(definition.scope_id)


def _catalog_row_id(snapshot: McpCatalogSnapshot) -> str:
    identity = f"{snapshot.server_id}\x00{snapshot.revision}".encode()
    return f"mcpr_{sha256_digest(identity)[:32]}"


def _schema_digest_or_none(value: object) -> str | None:
    return str(value) if value is not None else None


class SqliteMcpJournal:
    """Bounded v16 MCP repository sharing the operational journal transaction."""

    def __init__(self, backend: SqliteJournalBackend) -> None:
        self.backend = backend

    def put_server(
        self,
        definition: McpServerDefinition,
        *,
        catalog: McpCatalogSnapshot | None = None,
    ) -> McpServerDefinition:
        return self.backend.transact(lambda: self._put_server(definition, catalog=catalog))

    def _put_server(
        self,
        definition: McpServerDefinition,
        *,
        catalog: McpCatalogSnapshot | None,
    ) -> McpServerDefinition:
        scope, scope_id = _scope_fields(definition)
        now = _unix(self.backend.now())
        existing_catalog = self.backend.read_one(
            """
            SELECT catalog_revision, catalog_digest, catalog_status, degraded_reason
            FROM mcp_servers WHERE scope = ? AND scope_id = ? AND server_id = ?
            """,
            (scope, scope_id, definition.server_id),
        )
        if catalog is not None:
            catalog_status = catalog.status.value
            catalog_revision = catalog.revision
            catalog_digest = catalog.catalog_digest
            degraded_reason = catalog.degraded_reason
        elif existing_catalog is not None:
            catalog_revision = int(existing_catalog[0]) if existing_catalog[0] is not None else None
            catalog_digest = str(existing_catalog[1]) if existing_catalog[1] is not None else None
            catalog_status = str(existing_catalog[2])
            degraded_reason = str(existing_catalog[3]) if existing_catalog[3] is not None else None
        else:
            catalog_status = McpCatalogStatus.EMPTY.value
            catalog_revision = None
            catalog_digest = None
            degraded_reason = None
        values = (
            definition.server_id,
            scope,
            scope_id,
            definition.display_name,
            definition.transport.value,
            definition.executable,
            _json(list(definition.argv)),
            definition.argv_digest,
            definition.executable_kind,
            definition.cwd_policy.value,
            definition.cwd,
            definition.timeout_ms,
            _json(list(definition.credential_refs)),
            definition.workspace_visibility.value,
            _json([item.value for item in definition.requested_launch_risks]),
            int(definition.enabled),
            _json(definition.tool_policy.model_dump(mode="json")),
            definition.revision,
            mcp_server_config_digest(definition),
            catalog_revision,
            catalog_digest,
            catalog_status,
            degraded_reason,
            now,
            now,
        )
        self.backend.executor().execute(
            """
            INSERT INTO mcp_servers(
                server_id, scope, scope_id, display_name, transport, executable, argv_json,
                argv_digest, executable_kind, cwd_policy, cwd, timeout_ms, credential_refs_json,
                workspace_visibility, launch_risks_json, enabled, tool_policy_json,
                config_revision, config_digest, catalog_revision, catalog_digest, catalog_status,
                degraded_reason, created_at_unix, updated_at_unix
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(scope, scope_id, server_id) DO UPDATE SET
                display_name = excluded.display_name,
                transport = excluded.transport,
                executable = excluded.executable,
                argv_json = excluded.argv_json,
                argv_digest = excluded.argv_digest,
                executable_kind = excluded.executable_kind,
                cwd_policy = excluded.cwd_policy,
                cwd = excluded.cwd,
                timeout_ms = excluded.timeout_ms,
                credential_refs_json = excluded.credential_refs_json,
                workspace_visibility = excluded.workspace_visibility,
                launch_risks_json = excluded.launch_risks_json,
                enabled = excluded.enabled,
                tool_policy_json = excluded.tool_policy_json,
                config_revision = excluded.config_revision,
                config_digest = excluded.config_digest,
                catalog_revision = excluded.catalog_revision,
                catalog_digest = excluded.catalog_digest,
                catalog_status = excluded.catalog_status,
                degraded_reason = excluded.degraded_reason,
                updated_at_unix = excluded.updated_at_unix
            """,
            values,
        )
        if catalog is not None:
            self._put_catalog(definition, catalog)
        return definition

    def put_catalog(
        self, definition: McpServerDefinition, snapshot: McpCatalogSnapshot
    ) -> McpCatalogSnapshot:
        return self.backend.transact(lambda: self._put_catalog(definition, snapshot))

    def _put_catalog(
        self, definition: McpServerDefinition, snapshot: McpCatalogSnapshot
    ) -> McpCatalogSnapshot:
        if snapshot.server_id != definition.server_id:
            raise StorageError(StorageErrorCode.UNAVAILABLE, "MCP Catalog Server does not match")
        scope, scope_id = _scope_fields(definition)
        server = self.backend.read_one(
            "SELECT 1 FROM mcp_servers WHERE scope = ? AND scope_id = ? AND server_id = ?",
            (scope, scope_id, definition.server_id),
        )
        if server is None:
            raise StorageError(StorageErrorCode.NOT_FOUND, "MCP Server is missing")
        now = _unix(snapshot.created_at)
        catalog_id = _catalog_row_id(snapshot)
        handshake_json = (
            _json(snapshot.handshake.model_dump(mode="json")) if snapshot.handshake else None
        )
        self.backend.executor().execute(
            """
            INSERT INTO mcp_catalog_revisions(
                catalog_revision_id, server_id, scope, scope_id, revision, status,
                catalog_digest, handshake_json, degraded_reason, created_at_unix
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(scope, scope_id, server_id, revision) DO UPDATE SET
                catalog_revision_id = excluded.catalog_revision_id,
                status = excluded.status,
                catalog_digest = excluded.catalog_digest,
                handshake_json = excluded.handshake_json,
                degraded_reason = excluded.degraded_reason,
                created_at_unix = excluded.created_at_unix
            """,
            (
                catalog_id,
                definition.server_id,
                scope,
                scope_id,
                snapshot.revision,
                snapshot.status.value,
                snapshot.catalog_digest,
                handshake_json,
                snapshot.degraded_reason,
                now,
            ),
        )
        self.backend.executor().execute(
            "DELETE FROM mcp_catalog_tools WHERE catalog_revision_id = ?", (catalog_id,)
        )
        for entry in snapshot.tools:
            mapping = entry.risk_mapping
            self.backend.executor().execute(
                """
                INSERT INTO mcp_catalog_tools(
                    catalog_revision_id, remote_name, local_name, description,
                    input_schema_json, output_schema_json, schema_dialect,
                    input_schema_digest, output_schema_digest, annotations_json,
                    status, invalid_reason, effect, approval, mapping_enabled
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    catalog_id,
                    entry.remote_name,
                    entry.local_name,
                    entry.description,
                    _json(entry.input_schema) if entry.input_schema is not None else None,
                    _json(entry.output_schema) if entry.output_schema is not None else None,
                    entry.schema_dialect,
                    entry.input_schema_digest,
                    entry.output_schema_digest,
                    _json(entry.annotations),
                    entry.status.value,
                    entry.invalid_reason,
                    mapping.effect.value if mapping is not None else None,
                    mapping.approval.value if mapping is not None else None,
                    int(mapping.enabled) if mapping is not None else None,
                ),
            )
        self.backend.executor().execute(
            """
            UPDATE mcp_servers
            SET catalog_revision = ?, catalog_digest = ?, catalog_status = ?, degraded_reason = ?
            WHERE scope = ? AND scope_id = ? AND server_id = ?
            """,
            (
                snapshot.revision,
                snapshot.catalog_digest,
                snapshot.status.value,
                snapshot.degraded_reason,
                scope,
                scope_id,
                definition.server_id,
            ),
        )
        return snapshot

    def get_server(
        self, scope: str, server_id: str, *, scope_id: str | None = None
    ) -> McpServerDefinition | None:
        row = self.backend.read_one(
            """
            SELECT server_id, scope, scope_id, display_name, transport, executable, argv_json,
                   executable_kind, cwd_policy, cwd, timeout_ms, credential_refs_json,
                   workspace_visibility, launch_risks_json, enabled, tool_policy_json,
                   config_revision
            FROM mcp_servers WHERE scope = ? AND scope_id = ? AND server_id = ?
            """,
            (scope, _stored_scope_id(scope_id), server_id),
        )
        if row is None:
            return None
        try:
            return McpServerDefinition(
                server_id=str(row[0]),
                scope=str(row[1]),
                scope_id=_domain_scope_id(str(row[2])),
                display_name=str(row[3]),
                transport=str(row[4]),
                executable=str(row[5]),
                argv=tuple(_load_json(row[6], fallback=[])),
                executable_kind=str(row[7]),
                cwd_policy=str(row[8]),
                cwd=str(row[9]) if row[9] is not None else None,
                timeout_ms=int(row[10]),
                credential_refs=tuple(_load_json(row[11], fallback=[])),
                workspace_visibility=str(row[12]),
                requested_launch_risks=tuple(_load_json(row[13], fallback=[])),
                enabled=bool(row[14]),
                tool_policy=_load_json(row[15], fallback={}),
                revision=int(row[16]),
            )
        except (TypeError, ValueError, KeyError) as exc:
            raise StorageError(
                StorageErrorCode.NEEDS_REPAIR, "MCP Server durable row is invalid"
            ) from exc

    def list_servers(
        self, scope: str, *, scope_id: str | None = None
    ) -> tuple[McpServerDefinition, ...]:
        rows = self.backend.read_all(
            "SELECT server_id FROM mcp_servers WHERE scope = ? AND scope_id = ? ORDER BY server_id",
            (scope, _stored_scope_id(scope_id)),
        )
        values = tuple(self.get_server(scope, str(row[0]), scope_id=scope_id) for row in rows)
        return tuple(value for value in values if value is not None)

    def get_catalog(
        self,
        scope: str,
        server_id: str,
        *,
        scope_id: str | None = None,
        revision: int | None = None,
    ) -> McpCatalogSnapshot | None:
        stored_scope = _stored_scope_id(scope_id)
        if revision is None:
            row = self.backend.read_one(
                """
                SELECT catalog_revision_id, revision, status, catalog_digest,
                       handshake_json, degraded_reason
                FROM mcp_catalog_revisions
                WHERE scope = ? AND scope_id = ? AND server_id = ?
                ORDER BY revision DESC LIMIT 1
                """,
                (scope, stored_scope, server_id),
            )
        else:
            row = self.backend.read_one(
                """
                SELECT catalog_revision_id, revision, status, catalog_digest,
                       handshake_json, degraded_reason
                FROM mcp_catalog_revisions
                WHERE scope = ? AND scope_id = ? AND server_id = ? AND revision = ?
                """,
                (scope, stored_scope, server_id, revision),
            )
        if row is None:
            return None
        catalog_id = str(row[0])
        tool_rows = self.backend.read_all(
            """
            SELECT remote_name, local_name, description, input_schema_json, output_schema_json,
                   schema_dialect, input_schema_digest, output_schema_digest, annotations_json,
                   status, invalid_reason, effect, approval, mapping_enabled
            FROM mcp_catalog_tools WHERE catalog_revision_id = ? ORDER BY remote_name
            """,
            (catalog_id,),
        )
        entries: list[McpToolCatalogEntry] = []
        for tool in tool_rows:
            effect = ToolEffect(str(tool[11])) if tool[11] is not None else None
            mapping = None
            if effect is not None and tool[12] is not None:
                from morrow.core.mcp import McpToolRiskMapping

                mapping = McpToolRiskMapping(
                    remote_name=str(tool[0]),
                    effect=effect,
                    approval=McpApprovalMode(str(tool[12])),
                    enabled=bool(tool[13]),
                )
            entries.append(
                McpToolCatalogEntry(
                    remote_name=str(tool[0]),
                    local_name=str(tool[1]),
                    description=str(tool[2]),
                    input_schema=_load_json(tool[3]) if tool[3] is not None else None,
                    output_schema=_load_json(tool[4]) if tool[4] is not None else None,
                    schema_dialect=str(tool[5]) if tool[5] is not None else None,
                    input_schema_digest=_schema_digest_or_none(tool[6]),
                    output_schema_digest=_schema_digest_or_none(tool[7]),
                    annotations=_load_json(tool[8], fallback={}),
                    status=McpToolCatalogStatus(str(tool[9])),
                    invalid_reason=str(tool[10]) if tool[10] is not None else None,
                    risk_mapping=mapping,
                )
            )
        try:
            return McpCatalogSnapshot(
                server_id=server_id,
                revision=int(row[1]),
                status=McpCatalogStatus(str(row[2])),
                tools=tuple(entries),
                handshake=(
                    McpHandshake.model_validate(_load_json(row[4])) if row[4] is not None else None
                ),
                catalog_digest=str(row[3]),
                degraded_reason=str(row[5]) if row[5] is not None else None,
                created_at=_from_unix(
                    self.backend.read_one(
                        "SELECT created_at_unix FROM mcp_catalog_revisions WHERE catalog_revision_id = ?",
                        (catalog_id,),
                    )[0]
                ),
            )
        except (TypeError, ValueError, KeyError) as exc:
            raise StorageError(
                StorageErrorCode.NEEDS_REPAIR, "MCP Catalog durable row is invalid"
            ) from exc

    def put_launch_snapshot(self, snapshot: McpLaunchSnapshot) -> McpLaunchSnapshot:
        return self.backend.transact(lambda: self._put_launch_snapshot(snapshot))

    def _put_launch_snapshot(self, snapshot: McpLaunchSnapshot) -> McpLaunchSnapshot:
        self.backend.executor().execute(
            """
            INSERT INTO mcp_run_launch_snapshots(
                launch_snapshot_id, workspace_id, agent_run_id, server_id, config_revision,
                config_digest, catalog_revision, catalog_digest, transport, argv_digest,
                executable_digest, cwd_policy, workspace_visibility, network_risk,
                credential_risk, outside_workspace_risk, allowlisted_remote_tools_json,
                toolset_digest, created_at_unix
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(launch_snapshot_id) DO NOTHING
            """,
            (
                snapshot.launch_snapshot_id,
                snapshot.workspace_id,
                snapshot.agent_run_id,
                snapshot.server_id,
                snapshot.config_revision,
                snapshot.config_digest,
                snapshot.catalog_revision,
                snapshot.catalog_digest,
                snapshot.transport.value,
                snapshot.argv_digest,
                snapshot.executable_digest,
                snapshot.cwd_policy.value,
                snapshot.workspace_visibility.value,
                int(snapshot.network_risk),
                int(snapshot.credential_risk),
                int(snapshot.outside_workspace_risk),
                _json(list(snapshot.allowlisted_remote_tools)),
                snapshot.toolset_digest,
                _unix(snapshot.created_at),
            ),
        )
        return snapshot

    def list_launch_snapshots(
        self, workspace_id: str, agent_run_id: str
    ) -> tuple[McpLaunchSnapshot, ...]:
        rows = self.backend.read_all(
            """
            SELECT launch_snapshot_id, workspace_id, agent_run_id, server_id, config_revision,
                   config_digest, catalog_revision, catalog_digest, transport, argv_digest,
                   executable_digest, cwd_policy, workspace_visibility, network_risk,
                   credential_risk, outside_workspace_risk, allowlisted_remote_tools_json,
                   toolset_digest, created_at_unix
            FROM mcp_run_launch_snapshots
            WHERE workspace_id = ? AND agent_run_id = ?
            ORDER BY server_id, launch_snapshot_id
            """,
            (workspace_id, agent_run_id),
        )
        try:
            return tuple(
                McpLaunchSnapshot(
                    launch_snapshot_id=str(row[0]),
                    workspace_id=str(row[1]),
                    agent_run_id=str(row[2]),
                    server_id=str(row[3]),
                    config_revision=int(row[4]),
                    config_digest=str(row[5]),
                    catalog_revision=int(row[6]) if row[6] is not None else None,
                    catalog_digest=str(row[7]) if row[7] is not None else None,
                    transport=str(row[8]),
                    argv_digest=str(row[9]),
                    executable_digest=str(row[10]),
                    cwd_policy=str(row[11]),
                    workspace_visibility=str(row[12]),
                    network_risk=bool(row[13]),
                    credential_risk=bool(row[14]),
                    outside_workspace_risk=bool(row[15]),
                    allowlisted_remote_tools=tuple(_load_json(row[16], fallback=[])),
                    toolset_digest=str(row[17]) if row[17] is not None else None,
                    created_at=_from_unix(row[18]),
                )
                for row in rows
            )
        except (TypeError, ValueError, KeyError) as exc:
            raise StorageError(
                StorageErrorCode.NEEDS_REPAIR, "MCP launch snapshot durable row is invalid"
            ) from exc

    def put_tool_snapshot(self, snapshot: McpToolSnapshot, *, workspace_id: str) -> McpToolSnapshot:
        return self.backend.transact(lambda: self._put_tool_snapshot(snapshot, workspace_id))

    def _put_tool_snapshot(self, snapshot: McpToolSnapshot, workspace_id: str) -> McpToolSnapshot:
        self.backend.executor().execute(
            """
            INSERT INTO mcp_run_tool_snapshots(
                tool_snapshot_id, workspace_id, launch_snapshot_id, agent_run_id, server_id,
                remote_name, local_name, schema_dialect, input_schema_digest,
                output_schema_digest, effect, approval, catalog_revision,
                recovery_declaration_json, created_at_unix
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(tool_snapshot_id) DO NOTHING
            """,
            (
                snapshot.tool_snapshot_id,
                workspace_id,
                snapshot.launch_snapshot_id,
                snapshot.agent_run_id,
                snapshot.server_id,
                snapshot.remote_name,
                snapshot.local_name,
                snapshot.schema_dialect,
                snapshot.input_schema_digest,
                snapshot.output_schema_digest,
                snapshot.effect.value,
                snapshot.approval.value,
                snapshot.catalog_revision,
                _json(snapshot.recovery_declaration),
                _unix(snapshot.created_at),
            ),
        )
        return snapshot

    def list_tool_snapshots(
        self, workspace_id: str, agent_run_id: str
    ) -> tuple[McpToolSnapshot, ...]:
        rows = self.backend.read_all(
            """
            SELECT tool_snapshot_id, launch_snapshot_id, agent_run_id, server_id,
                   remote_name, local_name, schema_dialect, input_schema_digest,
                   output_schema_digest, effect, approval, catalog_revision,
                   recovery_declaration_json, created_at_unix
            FROM mcp_run_tool_snapshots
            WHERE workspace_id = ? AND agent_run_id = ?
            ORDER BY local_name, tool_snapshot_id
            """,
            (workspace_id, agent_run_id),
        )
        try:
            return tuple(
                McpToolSnapshot(
                    tool_snapshot_id=str(row[0]),
                    launch_snapshot_id=str(row[1]),
                    agent_run_id=str(row[2]),
                    server_id=str(row[3]),
                    remote_name=str(row[4]),
                    local_name=str(row[5]),
                    schema_dialect=str(row[6]),
                    input_schema_digest=str(row[7]),
                    output_schema_digest=str(row[8]) if row[8] is not None else None,
                    effect=str(row[9]),
                    approval=str(row[10]),
                    catalog_revision=int(row[11]),
                    recovery_declaration=_load_json(row[12], fallback={}),
                    created_at=_from_unix(row[13]),
                )
                for row in rows
            )
        except (TypeError, ValueError, KeyError) as exc:
            raise StorageError(
                StorageErrorCode.NEEDS_REPAIR, "MCP tool snapshot durable row is invalid"
            ) from exc

    def put_result_artifact_link(self, link: McpResultArtifactLink) -> McpResultArtifactLink:
        return self.backend.transact(lambda: self._put_result_artifact_link(link))

    def _put_result_artifact_link(self, link: McpResultArtifactLink) -> McpResultArtifactLink:
        execution = self.backend.read_one(
            "SELECT workspace_id FROM tool_executions WHERE tool_execution_id = ?",
            (link.tool_execution_id,),
        )
        if execution is None:
            raise StorageError(StorageErrorCode.NOT_FOUND, "MCP tool execution is missing")
        artifact = self.backend.read_one(
            "SELECT workspace_id FROM artifacts WHERE artifact_id = ?",
            (link.artifact_id,),
        )
        if artifact is None:
            raise StorageError(StorageErrorCode.NOT_FOUND, "MCP result Artifact is missing")
        if str(execution[0]) != link.workspace_id or str(artifact[0]) != link.workspace_id:
            raise StorageError(
                StorageErrorCode.IDENTITY_MISMATCH,
                "MCP result Artifact and execution workspace do not match",
            )
        self.backend.executor().execute(
            """
            INSERT INTO mcp_result_artifact_links(
                link_id, workspace_id, tool_execution_id, artifact_id, role, created_at_unix
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(tool_execution_id, artifact_id, role) DO NOTHING
            """,
            (
                link.link_id,
                link.workspace_id,
                link.tool_execution_id,
                link.artifact_id,
                link.role,
                _unix(link.created_at),
            ),
        )
        return link

    def list_result_artifact_links(
        self, workspace_id: str, tool_execution_id: str
    ) -> tuple[McpResultArtifactLink, ...]:
        rows = self.backend.read_all(
            """
            SELECT link_id, workspace_id, tool_execution_id, artifact_id, role, created_at_unix
            FROM mcp_result_artifact_links
            WHERE workspace_id = ? AND tool_execution_id = ?
            ORDER BY role, link_id
            """,
            (workspace_id, tool_execution_id),
        )
        try:
            return tuple(
                McpResultArtifactLink(
                    link_id=str(row[0]),
                    workspace_id=str(row[1]),
                    tool_execution_id=str(row[2]),
                    artifact_id=str(row[3]),
                    role=str(row[4]),
                    created_at=_from_unix(row[5]),
                )
                for row in rows
            )
        except (TypeError, ValueError, KeyError) as exc:
            raise StorageError(
                StorageErrorCode.NEEDS_REPAIR, "MCP Artifact link durable row is invalid"
            ) from exc


__all__ = ["SqliteMcpJournal"]
