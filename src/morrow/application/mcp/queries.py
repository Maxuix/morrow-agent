"""Sanitized MCP Server and Catalog query projections."""

from __future__ import annotations

from collections.abc import Callable

from morrow.application.mcp.definitions import McpDefinitionError, McpDefinitionService, Scope
from morrow.core.mcp import (
    McpCatalogSnapshot,
    McpCatalogStatus,
    McpServerDefinition,
    McpServerInspection,
    McpServerProjection,
    McpToolProjection,
    mcp_server_config_digest,
)


class McpQueryError(ValueError):
    """Sanitized MCP query failure."""


def _catalog_lookup(
    catalog_source: Callable[[str], McpCatalogSnapshot | None]
    | dict[str, McpCatalogSnapshot]
    | None,
    server_id: str,
) -> McpCatalogSnapshot | None:
    if catalog_source is None:
        return None
    if callable(catalog_source):
        return catalog_source(server_id)
    return catalog_source.get(server_id)


def project_server(
    definition: McpServerDefinition,
    catalog: McpCatalogSnapshot | None = None,
) -> McpServerProjection:
    entries = catalog.tools if catalog is not None else ()
    return McpServerProjection(
        server_id=definition.server_id,
        display_name=definition.display_name,
        transport=definition.transport,
        executable=definition.executable,
        argv_digest=definition.argv_digest,
        config_digest=mcp_server_config_digest(definition),
        argv_count=len(definition.argv),
        cwd_policy=definition.cwd_policy,
        workspace_visibility=definition.workspace_visibility,
        requested_launch_risks=definition.requested_launch_risks,
        enabled=definition.enabled,
        config_revision=definition.revision,
        catalog_revision=catalog.revision if catalog is not None else None,
        catalog_status=catalog.status if catalog is not None else McpCatalogStatus.EMPTY,
        catalog_digest=catalog.catalog_digest if catalog is not None else None,
        tool_count=len(entries),
        mapped_tool_count=sum(1 for item in entries if item.risk_mapping is not None),
        degraded_reason=catalog.degraded_reason if catalog is not None else None,
    )


def project_tool(entry) -> McpToolProjection:
    mapping = entry.risk_mapping
    return McpToolProjection(
        remote_name=entry.remote_name,
        local_name=entry.local_name,
        status=entry.status,
        schema_dialect=entry.schema_dialect,
        input_schema_digest=entry.input_schema_digest,
        output_schema_digest=entry.output_schema_digest,
        annotation_keys=tuple(sorted(entry.annotations)),
        effect=mapping.effect if mapping is not None else None,
        approval=mapping.approval if mapping is not None else None,
        invalid_reason=entry.invalid_reason,
    )


class McpQueries:
    """Queries never expose credential refs, environment, raw stderr, or full schemas."""

    def __init__(
        self,
        definitions: McpDefinitionService,
        *,
        catalogs: Callable[[str], McpCatalogSnapshot | None]
        | dict[str, McpCatalogSnapshot]
        | None = None,
    ) -> None:
        self.definitions = definitions
        self.catalogs = catalogs

    def _definition(
        self, server_id: str, scope: Scope, scope_id: str | None
    ) -> McpServerDefinition:
        try:
            return self.definitions.show(server_id, scope, scope_id=scope_id)
        except McpDefinitionError as exc:
            raise McpQueryError(str(exc)) from exc

    def list(self, scope: Scope, *, scope_id: str | None = None) -> tuple[McpServerProjection, ...]:
        try:
            values = self.definitions.list(scope, scope_id=scope_id)
        except McpDefinitionError as exc:
            raise McpQueryError(str(exc)) from exc
        return tuple(
            project_server(value, _catalog_lookup(self.catalogs, value.server_id))
            for value in values
        )

    def show(
        self, server_id: str, scope: Scope, *, scope_id: str | None = None
    ) -> McpServerProjection:
        value = self._definition(server_id, scope, scope_id)
        return project_server(value, _catalog_lookup(self.catalogs, value.server_id))

    def inspect(
        self, server_id: str, scope: Scope, *, scope_id: str | None = None
    ) -> McpServerInspection:
        value = self._definition(server_id, scope, scope_id)
        catalog = _catalog_lookup(self.catalogs, value.server_id)
        return McpServerInspection(
            server=project_server(value, catalog),
            tools=tuple(project_tool(item) for item in (catalog.tools if catalog else ())),
        )

    def status(
        self, server_id: str, scope: Scope, *, scope_id: str | None = None
    ) -> McpServerProjection:
        return self.show(server_id, scope, scope_id=scope_id)


__all__ = [
    "McpQueries",
    "McpQueryError",
    "project_server",
    "project_tool",
]
