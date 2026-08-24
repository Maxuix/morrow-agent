# Subplan 72 — MCP Control Plane and Catalog

> Status: in progress
> Branch: `feat/stage6-mcp-catalog`
> Prerequisite: Subplans 63 and 71 complete; user approves any exact dependency change

## Objective

Add stdio MCP Server desired state, safe local definitions, explicit discovery/refresh, deterministic
provider-compatible tool names and Operational Store v16 Catalog/run-evidence schema. Do not expose
MCP tools to AgentRun execution until Subplan 73.

## Ownership

- `src/morrow/core/mcp/` definitions, catalog and snapshot contracts
- `src/morrow/adapters/mcp/stdio_client.py` and SDK conversion boundary
- `src/morrow/application/mcp/definitions.py`, `catalog.py`, `queries.py`
- MCP section in the Extension YAML adapter created by Subplan 66
- `src/morrow/adapters/state/migrations_v16_mcp.py`, `mcp_journal.py`, thin registration
- `src/morrow/interfaces/mcp_cli.py`, Fake stdio fixture and focused tests

## Tasks

1. Present the Subplan 63 dependency recommendation and obtain explicit user approval before editing
   `pyproject.toml`/`uv.lock`. If denied, mark this subplan blocked without implementing a custom
   partial client.
2. Add only approved dependencies, pin/range them according to project policy, and record lockfile/
   license impact.
3. Define strict stdio Server configuration: ID, frozen argv (no shell), resolved managed/absolute
   executable, cwd policy, timeout, credential-ref names, workspace visibility, requested launch
   risks, enabled flag and tool policy. Reject secret values and package-runner auto-download defaults.
4. Implement add/show/list/enable/disable/remove with Extension YAML revision/OCC and sanitized
   projections. Add does not enable; enable does not approve every tool call.
5. Implement a narrow stdio adapter for initialize/list-tools/close against the local Fake Server.
   Protocol stdout is separate from bounded redacted stderr; every phase is cancellable and timed.
6. Normalize runtime schemas and annotations into Catalog entries. Validate selected `$schema`
   dialect, isolate one invalid tool, and degrade only the whole Server when handshake/catalog
   integrity is unusable.
7. Generate local names as `mcp__<server-slug>__<tool-slug>` under the existing 64-character
   Provider constraint. Use deterministic digest suffixes for truncation/collision and persist the
   exact remote↔local mapping.
8. Require local tool risk mapping/allowlist before enable. MCP annotations never set authoritative
   ToolEffect, approval or Trust.
9. Add v16 tables for Server/Catalog revisions, run launch/tool snapshots and result Artifact links.
   Keep actual calls in existing ToolExecution tables.
10. Implement inspect/refresh/status queries without credential values, full environment, raw stderr
    or full remote schemas by default.

## Validation

- Config/path/argv/secrets, workspace visibility, YAML OCC/replay and enable policy.
- Fake Server handshake/catalog/close, timeout/cancel/crash, invalid Server vs isolated invalid tool,
  Schema dialect and deterministic namespace collisions.
- v15→v16 migration, future schema, Catalog digest/reference doctor checks.
- Focused MCP control/migration/CLI tests, standard quality commands and full non-live suite.

## Exit criteria

- MCP Server definitions and Catalog are inspectable and reproducible but not yet callable by the
  Agent.
- v16 is fixed and contains all later runtime snapshot/result-reference columns.
- Dependency additions exactly match explicit approval and no networked/live MCP path ran.
