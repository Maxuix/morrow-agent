# TODO

## Current stage

Stage 6 implementation in progress.

## Active subplan

None. Subplan 72 is complete locally; Subplan 73 remains pending activation.

## Tasks (Subplan 72)

- `[x]` Add the approved MCP and JSON Schema dependencies and lock impact.
- `[x]` Define strict stdio server configuration and YAML control operations.
- `[x]` Implement the narrow cancellable stdio handshake/list/close adapter seam.
- `[x]` Normalize schemas, annotations and deterministic provider-safe local names.
- `[x]` Add v16 catalog/server/run snapshot persistence and query projections.
- `[x]` Add Fake Server, migration, YAML/OCC, namespace and catalog integrity tests.
- `[x]` Run focused MCP control/migration/CLI tests and the standard quality gates.

## Boundaries

- Do not implement MCP runtime/security, learned routing or unrestricted Skill injection.
- Do not use real credentials, networked MCP Servers, live Providers or user state.
- Work was completed on `feat/stage6-mcp-catalog`; preserve the verified Subplan71 commits and do not
  introduce MCP-specific branches into ToolExecutor or recovery.
- Preserve AgentLoop/ConversationLog, permission policy defaults, public events and Stage 4/5 behavior.
