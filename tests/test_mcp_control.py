"""Subplan 72 MCP control-plane contracts, Fake discovery and persistence."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from morrow.adapters.mcp.stdio_client import McpStdioClient
from morrow.adapters.state.extension_yaml import ExtensionYamlConflict, ExtensionYamlStore
from morrow.adapters.state.journal import SqliteOperationalJournal
from morrow.adapters.state.operational import OperationalStore
from morrow.application.mcp.catalog import build_catalog_snapshot
from morrow.application.mcp.definitions import McpDefinitionError, McpDefinitionService
from morrow.core.mcp import (
    MCP_SCHEMA_DIALECT,
    McpCatalogStatus,
    McpCwdPolicy,
    McpDiscovery,
    McpHandshake,
    McpRemoteTool,
    McpServerDefinition,
    McpToolCatalogStatus,
    McpToolPolicy,
    McpToolRiskMapping,
    mcp_local_tool_name,
    normalize_json_schema,
)
from morrow.core.models import ToolEffect

FAKE_SERVER = Path(__file__).parent / "spikes" / "fake_mcp_stdio_server.py"


def _definition(*, policy: bool = True) -> McpServerDefinition:
    return McpServerDefinition(
        server_id="mcp_fake",
        display_name="Fake",
        executable=sys.executable,
        argv=(str(FAKE_SERVER.resolve()),),
        cwd_policy=McpCwdPolicy.MANAGED,
        tool_policy=(
            McpToolPolicy(
                allowlist=("echo",),
                mappings=(McpToolRiskMapping(remote_name="echo", effect=ToolEffect.NONE),),
            )
            if policy
            else McpToolPolicy()
        ),
    )


def test_server_config_rejects_unsafe_launch_inputs() -> None:
    with pytest.raises(ValidationError):
        McpServerDefinition(id="mcp_demo", name="Demo", command="python")
    with pytest.raises(ValidationError):
        McpServerDefinition(id="mcp_demo", name="Demo", command="/usr/bin/uvx")
    with pytest.raises(ValidationError):
        McpServerDefinition(
            id="mcp_demo", name="Demo", command="/usr/bin/python3", argv=("api_key=secret",)
        )
    with pytest.raises(ValidationError):
        McpServerDefinition(
            id="mcp_demo",
            name="Demo",
            command="/usr/bin/python3",
            cwd_policy="absolute",
        )


def test_schema_normalization_selects_dialect_and_rejects_remote_refs() -> None:
    normalized = normalize_json_schema({"type": "object"})
    assert normalized.schema["$schema"] == MCP_SCHEMA_DIALECT
    with pytest.raises(ValueError):
        normalize_json_schema({"$schema": "http://json-schema.org/draft-07/schema#"})
    with pytest.raises(ValueError):
        normalize_json_schema({"$ref": "https://example.invalid/schema.json"})


def test_fake_stdio_discovery_and_catalog_are_offline_and_deterministic() -> None:
    async def discover():
        return await McpStdioClient(_definition()).discover()

    discovery = asyncio.run(discover())
    assert discovery.handshake.server_name == "fake-stdio"
    assert [item.name for item in discovery.tools] == ["echo", "add", "fail", "slow"]
    first = build_catalog_snapshot(_definition(), discovery)
    second = build_catalog_snapshot(_definition(), discovery)
    assert first.catalog_digest == second.catalog_digest
    assert first.status is McpCatalogStatus.READY
    assert first.tools[0].remote_name == "add"
    assert next(item for item in first.tools if item.remote_name == "echo").status is (
        McpToolCatalogStatus.READY
    )
    assert all(len(item.local_name) <= 64 for item in first.tools)


def test_invalid_tool_schema_isolated_from_valid_catalog() -> None:
    discovery = McpDiscovery(
        handshake=McpHandshake(server_name="fake", protocol_version="1"),
        tools=(
            McpRemoteTool(name="good", input_schema={"type": "object"}),
            McpRemoteTool(
                name="bad",
                input_schema={"$schema": "https://json-schema.org/draft/2019-09/schema"},
            ),
        ),
    )
    snapshot = build_catalog_snapshot(_definition(policy=False), discovery)
    assert snapshot.status is McpCatalogStatus.READY
    assert next(item for item in snapshot.tools if item.remote_name == "good").status is (
        McpToolCatalogStatus.UNMAPPED
    )
    assert next(item for item in snapshot.tools if item.remote_name == "bad").status is (
        McpToolCatalogStatus.INVALID_SCHEMA
    )


def test_local_namespace_truncation_and_collision_are_stable() -> None:
    long_name = "x" * 128
    first = mcp_local_tool_name("mcp_demo", long_name)
    second = mcp_local_tool_name("mcp_demo", long_name, used_names=frozenset({first}))
    assert len(first) <= 64
    assert first != second
    assert second.endswith(
        "_" + __import__("hashlib").sha256(f"mcp_demo\x00{long_name}".encode()).hexdigest()[:16]
    )


def test_yaml_add_is_disabled_enable_requires_catalog_and_honors_occ(tmp_path: Path) -> None:
    service = McpDefinitionService(ExtensionYamlStore(tmp_path))
    definition = _definition()
    added = service.add(definition, scope="global")
    assert added.value.mcp.servers[0].enabled is False
    with pytest.raises(McpDefinitionError, match="refreshed"):
        service.enable("mcp_fake", scope="global")
    discovery = McpDiscovery(
        handshake=McpHandshake(server_name="fake", protocol_version="1"),
        tools=(McpRemoteTool(name="echo", input_schema={"type": "object"}),),
    )
    catalog = build_catalog_snapshot(definition, discovery)
    enabled = service.enable("mcp_fake", scope="global", catalog=catalog)
    assert enabled.value.mcp.servers[0].enabled is True
    stale = service.prepare_change(operation="disable", server_id="mcp_fake", scope="global")
    service.enable("mcp_fake", scope="global", catalog=catalog)
    with pytest.raises(ExtensionYamlConflict):
        service.apply_change(stale)


def test_mcp_journal_round_trip_and_v16_tables(tmp_path: Path) -> None:
    handle = OperationalStore(tmp_path).initialize()
    journal = SqliteOperationalJournal(handle)
    definition = _definition()
    discovery = McpDiscovery(
        handshake=McpHandshake(server_name="fake", protocol_version="1"),
        tools=(McpRemoteTool(name="echo", input_schema={"type": "object"}),),
    )
    catalog = build_catalog_snapshot(definition, discovery)
    journal.put_mcp_server(definition, catalog=catalog)
    assert journal.get_mcp_server("global", "mcp_fake") == definition
    restored = journal.get_mcp_catalog("global", "mcp_fake")
    assert restored is not None
    assert restored.catalog_digest == catalog.catalog_digest
    tables = handle.run_read(
        lambda executor: executor.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name LIKE 'mcp_%' "
            "ORDER BY name"
        )
    )
    assert {row[0] for row in tables} == {
        "mcp_catalog_revisions",
        "mcp_catalog_tools",
        "mcp_result_artifact_links",
        "mcp_run_launch_snapshots",
        "mcp_run_tool_snapshots",
        "mcp_servers",
    }
    handle.close()
