"""MCP handshake/catalog refresh and schema/risk normalization."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from morrow.adapters.mcp.stdio_client import McpAdapterError, McpStdioClient
from morrow.core.domain import canonical_json_bytes
from morrow.core.mcp import (
    MCP_MAX_REMOTE_TOOLS,
    McpCatalogSnapshot,
    McpCatalogStatus,
    McpDiscovery,
    McpServerDefinition,
    McpToolCatalogEntry,
    McpToolCatalogStatus,
    mcp_local_tool_name,
)
from morrow.core.mcp.schema import McpSchemaError, normalize_json_schema


class McpCatalogError(ValueError):
    """Sanitized Catalog normalization failure."""

    def __init__(self, code: str, message: str = "MCP Catalog operation failed") -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _annotation_evidence(value: dict[str, object]) -> dict[str, object]:
    """Keep only bounded, known MCP hints; these remain non-authoritative facts."""

    allowed = {
        "title",
        "readOnlyHint",
        "destructiveHint",
        "idempotentHint",
        "openWorldHint",
    }
    result: dict[str, object] = {}
    for key in allowed:
        item = value.get(key)
        if isinstance(item, (str, bool)) and (not isinstance(item, str) or len(item) <= 256):
            result[key] = item
    return result


def _invalid_entry(
    server_id: str,
    remote_name: str,
    *,
    reason: str,
    used_names: frozenset[str],
    description: str,
    annotations: dict[str, object],
) -> McpToolCatalogEntry:
    return McpToolCatalogEntry(
        remote_name=remote_name,
        local_name=mcp_local_tool_name(server_id, remote_name, used_names=used_names),
        description=description,
        annotations=annotations,
        status=McpToolCatalogStatus.INVALID_SCHEMA,
        invalid_reason=reason,
    )


def build_catalog_snapshot(
    definition: McpServerDefinition,
    discovery: McpDiscovery,
    *,
    revision: int = 1,
) -> McpCatalogSnapshot:
    """Convert one successful discovery into an immutable Catalog revision."""

    if discovery.handshake.server_name.strip() == "":
        raise McpCatalogError("handshake_integrity", "MCP handshake identity is missing")
    if len(discovery.tools) > MCP_MAX_REMOTE_TOOLS:
        raise McpCatalogError("tool_limit", "MCP Server returned too many tools")
    entries: list[McpToolCatalogEntry] = []
    used_names: set[str] = set()
    for remote in discovery.tools:
        try:
            local_name = mcp_local_tool_name(
                definition.server_id,
                remote.name,
                used_names=frozenset(used_names),
            )
            used_names.add(local_name)
        except ValueError as exc:
            raise McpCatalogError(
                "namespace_collision", "MCP local tool namespace is ambiguous"
            ) from exc
        annotations = _annotation_evidence(remote.annotations)
        try:
            input_schema = normalize_json_schema(remote.input_schema, label="input")
            output_schema = (
                normalize_json_schema(remote.output_schema, label="output")
                if remote.output_schema is not None
                else None
            )
        except McpSchemaError as exc:
            entries.append(
                _invalid_entry(
                    definition.server_id,
                    remote.name,
                    reason=exc.code,
                    used_names=frozenset(used_names - {local_name}),
                    description=remote.description,
                    annotations=annotations,
                )
            )
            continue
        mapping = definition.tool_policy.mapping_for(remote.name)
        allowlisted = remote.name in definition.tool_policy.allowlist
        if not allowlisted:
            status = McpToolCatalogStatus.UNMAPPED
        elif mapping is None or not mapping.enabled:
            status = McpToolCatalogStatus.DISALLOWED
        else:
            status = McpToolCatalogStatus.READY
        entries.append(
            McpToolCatalogEntry(
                remote_name=remote.name,
                local_name=local_name,
                description=remote.description,
                input_schema=input_schema.schema,
                output_schema=output_schema.schema if output_schema else None,
                schema_dialect=input_schema.dialect,
                input_schema_digest=input_schema.digest,
                output_schema_digest=output_schema.digest if output_schema else None,
                annotations=annotations,
                status=status,
                risk_mapping=mapping,
            )
        )
    return McpCatalogSnapshot(
        server_id=definition.server_id,
        revision=revision,
        status=McpCatalogStatus.READY,
        tools=tuple(entries),
        handshake=discovery.handshake,
    )


def degraded_catalog_snapshot(
    definition: McpServerDefinition,
    *,
    revision: int = 1,
    reason: str = "unavailable",
) -> McpCatalogSnapshot:
    return McpCatalogSnapshot(
        server_id=definition.server_id,
        revision=revision,
        status=McpCatalogStatus.DEGRADED,
        degraded_reason=reason[:128],
    )


class McpCatalogService:
    """Explicit refresh service; no refresh occurs as a side effect of AgentRun."""

    def __init__(
        self,
        *,
        client_factory: Callable[..., McpStdioClient] = McpStdioClient,
        workspace_root: Path | None = None,
    ) -> None:
        self.client_factory = client_factory
        self.workspace_root = workspace_root

    async def refresh(
        self,
        definition: McpServerDefinition,
        *,
        previous: McpCatalogSnapshot | None = None,
        environment: dict[str, str] | None = None,
    ) -> McpCatalogSnapshot:
        revision = (previous.revision + 1) if previous is not None else 1
        try:
            client = self.client_factory(
                definition,
                workspace_root=self.workspace_root,
                environment=environment,
            )
            discovery = await client.discover()
        except McpAdapterError as exc:
            return degraded_catalog_snapshot(definition, revision=revision, reason=exc.code)
        except Exception as exc:
            # Adapter/SDK implementation details are never part of the Catalog projection.
            raise McpCatalogError("adapter_failure", "MCP Catalog refresh failed") from exc
        try:
            snapshot = build_catalog_snapshot(definition, discovery, revision=revision)
        except (McpCatalogError, ValueError):
            return degraded_catalog_snapshot(
                definition, revision=revision, reason="catalog_integrity"
            )
        if len(canonical_json_bytes(snapshot.model_dump(mode="json"))) > 64 * 1024:
            return degraded_catalog_snapshot(definition, revision=revision, reason="catalog_budget")
        return snapshot


__all__ = [
    "McpCatalogError",
    "McpCatalogService",
    "build_catalog_snapshot",
    "degraded_catalog_snapshot",
]
