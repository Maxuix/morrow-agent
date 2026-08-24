"""YAML-authoritative MCP Server desired-state operations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from morrow.adapters.state.extension_yaml import (
    ExtensionYamlConflict,
    ExtensionYamlLoadStatus,
    ExtensionYamlStore,
    extension_document_digest,
)
from morrow.core.mcp import (
    McpCatalogSnapshot,
    McpCatalogStatus,
    McpServerDefinition,
    McpToolCatalogStatus,
    McpWorkspaceVisibility,
)
from morrow.core.skills.bindings import (
    ExtensionDocument,
    ExtensionMcpSection,
    GlobalExtensionDocument,
    WorkspaceExtensionDocument,
)

Scope = Literal["global", "workspace"]


class McpDefinitionError(ValueError):
    """A requested MCP desired-state change cannot be applied safely."""

    def __init__(self, code: str, message: str = "MCP definition operation failed") -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class PreparedMcpChange:
    operation: str
    scope: Scope
    scope_id: str | None
    server_id: str
    before_document: ExtensionDocument
    after_document: ExtensionDocument
    before_server: McpServerDefinition | None
    after_server: McpServerDefinition | None


def _document_type(scope: Scope):
    return GlobalExtensionDocument if scope == "global" else WorkspaceExtensionDocument


def _server_for(document: ExtensionDocument, server_id: str) -> McpServerDefinition | None:
    return next((server for server in document.mcp.servers if server.server_id == server_id), None)


def _with_servers(
    document: ExtensionDocument,
    servers: tuple[McpServerDefinition, ...],
) -> ExtensionDocument:
    model_type = _document_type(document.scope)
    payload = document.model_dump(mode="python", by_alias=True)
    payload["mcp"] = ExtensionMcpSection(servers=servers)
    return model_type.model_validate(payload)


def _scoped_server(
    definition: McpServerDefinition,
    *,
    scope: Scope,
    scope_id: str | None,
    enabled: bool | None = None,
    revision: int | None = None,
) -> McpServerDefinition:
    payload = definition.model_dump(mode="python")
    payload.update(
        {
            "scope": scope,
            "scope_id": scope_id,
            "enabled": definition.enabled if enabled is None else enabled,
            "revision": definition.revision if revision is None else revision,
        }
    )
    return McpServerDefinition.model_validate(payload)


def _validate_scope(scope: Scope, scope_id: str | None) -> None:
    if scope == "global" and scope_id is not None:
        raise McpDefinitionError("invalid_scope", "global MCP definitions do not use workspace_id")
    if scope == "workspace" and not scope_id:
        raise McpDefinitionError("invalid_scope", "workspace MCP definitions require workspace_id")


def validate_enable_policy(
    definition: McpServerDefinition,
    catalog: McpCatalogSnapshot | None,
) -> None:
    """Require an explicit local allowlist and mapping before enabling a Server."""

    if definition.workspace_visibility is not McpWorkspaceVisibility.READ_WRITE:
        raise McpDefinitionError(
            "workspace_visibility_unsupported",
            "MCP stdio execution cannot enforce a restricted workspace visibility",
        )
    if catalog is None:
        raise McpDefinitionError("catalog_required", "MCP Server must be refreshed before enable")
    if catalog.server_id != definition.server_id:
        raise McpDefinitionError("catalog_mismatch", "MCP Catalog belongs to another Server")
    if catalog.status is not McpCatalogStatus.READY:
        raise McpDefinitionError("catalog_unavailable", "MCP Server Catalog is not ready")
    if not definition.tool_policy.allowlist:
        raise McpDefinitionError(
            "tool_allowlist_required", "MCP enable requires a local tool allowlist"
        )
    catalog_by_name = {item.remote_name: item for item in catalog.tools}
    for remote_name in definition.tool_policy.allowlist:
        entry = catalog_by_name.get(remote_name)
        mapping = definition.tool_policy.mapping_for(remote_name)
        if entry is None:
            raise McpDefinitionError("tool_missing", "MCP allowlist contains an undiscovered tool")
        if entry.status is not McpToolCatalogStatus.READY:
            raise McpDefinitionError("tool_unavailable", "MCP allowlist contains an unusable tool")
        if mapping is None or not mapping.enabled:
            raise McpDefinitionError(
                "risk_mapping_required", "MCP tools require local risk mappings"
            )


class McpDefinitionService:
    """Small YAML-authority service with revision and digest OCC."""

    def __init__(self, yaml_store: ExtensionYamlStore, workspace_id: str | None = None) -> None:
        self.yaml_store = yaml_store
        self.workspace_id = workspace_id

    def load(self, scope: Scope, *, scope_id: str | None = None):
        _validate_scope(scope, scope_id)
        if scope == "global":
            return self.yaml_store.load_global()
        return self.yaml_store.load_workspace(scope_id or self.workspace_id or "")

    def list(self, scope: Scope, *, scope_id: str | None = None) -> tuple[McpServerDefinition, ...]:
        load = self.load(scope, scope_id=scope_id)
        if load.status is not ExtensionYamlLoadStatus.OK or load.value is None:
            raise McpDefinitionError("yaml_unavailable", load.error or "Extension YAML unavailable")
        return tuple(sorted(load.value.mcp.servers, key=lambda item: item.server_id))

    def show(
        self, server_id: str, scope: Scope, *, scope_id: str | None = None
    ) -> McpServerDefinition:
        server = next(
            (item for item in self.list(scope, scope_id=scope_id) if item.server_id == server_id),
            None,
        )
        if server is None:
            raise McpDefinitionError("not_found", "MCP Server is missing")
        return server

    def prepare_add(
        self,
        definition: McpServerDefinition,
        *,
        scope: Scope,
        scope_id: str | None = None,
    ) -> PreparedMcpChange:
        _validate_scope(scope, scope_id)
        resolved_scope_id = None if scope == "global" else scope_id or self.workspace_id
        if scope == "workspace" and not resolved_scope_id:
            raise McpDefinitionError(
                "invalid_scope", "workspace MCP definitions require workspace_id"
            )
        load = self.load(scope, scope_id=resolved_scope_id)
        if load.status is not ExtensionYamlLoadStatus.OK or load.value is None:
            raise McpDefinitionError("yaml_unavailable", load.error or "Extension YAML unavailable")
        if _server_for(load.value, definition.server_id) is not None:
            raise McpDefinitionError("already_exists", "MCP Server already exists")
        # Add is intentionally never an implicit enable operation.
        after_server = _scoped_server(
            definition,
            scope=scope,
            scope_id=resolved_scope_id,
            enabled=False,
            revision=1,
        )
        after = _with_servers(load.value, (*load.value.mcp.servers, after_server))
        return PreparedMcpChange(
            "add",
            scope,
            resolved_scope_id,
            after_server.server_id,
            load.value,
            after,
            None,
            after_server,
        )

    def prepare_change(
        self,
        *,
        operation: Literal["enable", "disable", "remove"],
        server_id: str,
        scope: Scope,
        scope_id: str | None = None,
        catalog: McpCatalogSnapshot | None = None,
    ) -> PreparedMcpChange:
        _validate_scope(scope, scope_id)
        resolved_scope_id = None if scope == "global" else scope_id or self.workspace_id
        load = self.load(scope, scope_id=resolved_scope_id)
        if load.status is not ExtensionYamlLoadStatus.OK or load.value is None:
            raise McpDefinitionError("yaml_unavailable", load.error or "Extension YAML unavailable")
        current = _server_for(load.value, server_id)
        if current is None:
            raise McpDefinitionError("not_found", "MCP Server is missing")
        if operation == "enable":
            validate_enable_policy(current, catalog)
            after_server = _scoped_server(
                current,
                scope=scope,
                scope_id=resolved_scope_id,
                enabled=True,
                revision=current.revision + 1,
            )
            servers = tuple(
                after_server if item.server_id == server_id else item
                for item in load.value.mcp.servers
            )
        elif operation == "disable":
            after_server = _scoped_server(
                current,
                scope=scope,
                scope_id=resolved_scope_id,
                enabled=False,
                revision=current.revision + 1,
            )
            servers = tuple(
                after_server if item.server_id == server_id else item
                for item in load.value.mcp.servers
            )
        elif operation == "remove":
            after_server = None
            servers = tuple(item for item in load.value.mcp.servers if item.server_id != server_id)
        else:
            raise McpDefinitionError(
                "unsupported_operation", "unsupported MCP definition operation"
            )
        after = _with_servers(load.value, servers)
        return PreparedMcpChange(
            operation, scope, resolved_scope_id, server_id, load.value, after, current, after_server
        )

    def apply_change(self, change: PreparedMcpChange):
        current = self.load(change.scope, scope_id=change.scope_id)
        if current.status is not ExtensionYamlLoadStatus.OK or current.value is None:
            raise McpDefinitionError(
                "yaml_unavailable", current.error or "Extension YAML unavailable"
            )
        if current.revision != change.before_document.revision:
            raise ExtensionYamlConflict("Extension YAML changed during the MCP operation")
        if extension_document_digest(current.value) != extension_document_digest(
            change.before_document
        ):
            raise ExtensionYamlConflict("Extension YAML changed during the MCP operation")
        if current.value.mcp == change.after_document.mcp:
            return current
        if change.scope == "global":
            return self.yaml_store.write_global(
                GlobalExtensionDocument.model_validate(change.after_document),
                expected_revision=current.revision,
                expected_value_digest=current.digest,
            )
        return self.yaml_store.write_workspace(
            change.scope_id or "",
            WorkspaceExtensionDocument.model_validate(change.after_document),
            expected_revision=current.revision,
            expected_value_digest=current.digest,
        )

    def add(self, definition: McpServerDefinition, *, scope: Scope, scope_id: str | None = None):
        return self.apply_change(self.prepare_add(definition, scope=scope, scope_id=scope_id))

    def enable(
        self,
        server_id: str,
        *,
        scope: Scope,
        scope_id: str | None = None,
        catalog: McpCatalogSnapshot | None = None,
    ):
        return self.apply_change(
            self.prepare_change(
                operation="enable",
                server_id=server_id,
                scope=scope,
                scope_id=scope_id,
                catalog=catalog,
            )
        )

    def disable(self, server_id: str, *, scope: Scope, scope_id: str | None = None):
        return self.apply_change(
            self.prepare_change(
                operation="disable", server_id=server_id, scope=scope, scope_id=scope_id
            )
        )

    def remove(self, server_id: str, *, scope: Scope, scope_id: str | None = None):
        return self.apply_change(
            self.prepare_change(
                operation="remove", server_id=server_id, scope=scope, scope_id=scope_id
            )
        )


__all__ = [
    "McpDefinitionError",
    "McpDefinitionService",
    "PreparedMcpChange",
    "validate_enable_policy",
]
