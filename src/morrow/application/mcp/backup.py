"""Cross-store MCP evidence checks used by Backup v2 and Doctor."""

from __future__ import annotations

import sqlite3


def verify_mcp_backup_references(connection: sqlite3.Connection) -> tuple[bool, tuple[str, ...]]:
    """Validate MCP snapshot/catalog links without reading raw payloads."""

    issues: list[str] = []
    if not _has_table(connection, "mcp_servers"):
        return True, ()
    required = (
        "mcp_catalog_revisions",
        "mcp_catalog_tools",
        "mcp_run_launch_snapshots",
        "mcp_run_tool_snapshots",
    )
    for table in required:
        if not _has_table(connection, table):
            issues.append("mcp_table_missing")
    if "mcp_table_missing" in issues:
        normalized = tuple(dict.fromkeys(issues))
        return False, normalized
    try:
        launches: dict[str, tuple[str, str, str, int | None]] = {}
        rows = connection.execute(
            "SELECT launch_snapshot_id, workspace_id, agent_run_id, server_id, catalog_revision "
            "FROM mcp_run_launch_snapshots"
        ).fetchall()
        for launch_id, workspace_id, agent_run_id, server_id, catalog_revision in rows:
            launches[str(launch_id)] = (
                str(workspace_id),
                str(agent_run_id),
                str(server_id),
                None if catalog_revision is None else int(catalog_revision),
            )
            scope = "workspace"
            scope_id = str(workspace_id)
            server = connection.execute(
                "SELECT 1 FROM mcp_servers WHERE scope = ? AND scope_id = ? "
                "AND server_id = ? LIMIT 1",
                (scope, scope_id, server_id),
            ).fetchone()
            if server is None:
                server = connection.execute(
                    "SELECT 1 FROM mcp_servers WHERE scope = 'global' AND scope_id = '' "
                    "AND server_id = ? LIMIT 1",
                    (server_id,),
                ).fetchone()
            if server is None:
                issues.append("mcp_launch_server")
            if catalog_revision is not None:
                catalog = connection.execute(
                    "SELECT 1 FROM mcp_catalog_revisions WHERE server_id = ? "
                    "AND revision = ? AND ((scope = 'global' AND scope_id = '') OR "
                    "(scope = 'workspace' AND scope_id = ?)) LIMIT 1",
                    (server_id, catalog_revision, workspace_id),
                ).fetchone()
                if catalog is None:
                    issues.append("mcp_launch_catalog")
            run = connection.execute(
                "SELECT 1 FROM agent_runs AS ar JOIN sessions AS s ON s.session_id = ar.session_id "
                "WHERE ar.agent_run_id = ? AND s.workspace_id = ? LIMIT 1",
                (agent_run_id, workspace_id),
            ).fetchone()
            if run is None:
                issues.append("mcp_launch_run")
        tool_rows = connection.execute(
            "SELECT launch_snapshot_id, workspace_id, agent_run_id, server_id, remote_name, "
            "catalog_revision FROM mcp_run_tool_snapshots"
        ).fetchall()
        for (
            launch_id,
            workspace_id,
            agent_run_id,
            server_id,
            remote_name,
            catalog_revision,
        ) in tool_rows:
            launch = launches.get(str(launch_id))
            if launch is None:
                issues.append("mcp_tool_launch")
                continue
            launch_workspace, launch_run, launch_server, launch_catalog = launch
            if (
                str(workspace_id) != launch_workspace
                or str(agent_run_id) != launch_run
                or str(server_id) != launch_server
                or catalog_revision is None
                or launch_catalog != int(catalog_revision)
            ):
                issues.append("mcp_tool_launch_mismatch")
                continue
            catalog = connection.execute(
                "SELECT catalog_revision_id FROM mcp_catalog_revisions WHERE server_id = ? "
                "AND revision = ? AND ((scope = 'global' AND scope_id = '') OR "
                "(scope = 'workspace' AND scope_id = ?)) LIMIT 1",
                (server_id, catalog_revision, workspace_id),
            ).fetchone()
            if catalog is None:
                issues.append("mcp_tool_catalog")
                continue
            tool = connection.execute(
                "SELECT 1 FROM mcp_catalog_tools WHERE catalog_revision_id = ? "
                "AND remote_name = ? LIMIT 1",
                (catalog[0], remote_name),
            ).fetchone()
            if tool is None:
                issues.append("mcp_tool_catalog")
    except sqlite3.Error:
        issues.append("mcp_reference_unreadable")
    normalized = tuple(dict.fromkeys(issues))
    return not normalized, normalized


def _has_table(connection: sqlite3.Connection, name: str) -> bool:
    return (
        connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)
        ).fetchone()
        is not None
    )


__all__ = ["verify_mcp_backup_references"]
