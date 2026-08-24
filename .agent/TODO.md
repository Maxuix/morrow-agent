# TODO

## Current stage

Stage 6 implementation in progress.

## Active subplan

Subplan 72 — MCP Control Plane and Catalog.

## Tasks (Subplan 72)

- `[x]` Add the approved MCP and JSON Schema dependencies and lock impact.
- `[ ]` Define strict stdio server configuration and YAML control operations.
- `[ ]` Implement the narrow cancellable stdio handshake/list/close adapter seam.
- `[ ]` Normalize schemas, annotations and deterministic provider-safe local names.
- `[ ]` Add v16 catalog/server/run snapshot persistence and query projections.
- `[ ]` Add Fake Server, migration, YAML/OCC, namespace and catalog integrity tests.
- `[ ]` Run focused MCP control/migration/CLI tests and the standard quality gates.

## Boundaries

- Do not implement MCP, learned routing or unrestricted Skill injection.
- Do not use real credentials, networked MCP Servers, live Providers or user state.
- Work is on `feat/stage6-mcp-catalog`; preserve the verified Subplan71 commits and do not
  introduce MCP-specific branches into ToolExecutor or recovery.
- Preserve AgentLoop/ConversationLog, permission policy defaults, public events and Stage 4/5 behavior.
