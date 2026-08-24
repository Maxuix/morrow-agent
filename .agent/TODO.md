# TODO

## Current stage

Stage 6 implementation in progress.

## Active subplan

None. Subplan 71 is complete locally; Subplan 72 awaits exact dependency approval.

## Tasks (Subplan 71)

- `[x]` Define the bounded validator interface and Pydantic compatibility path.
- `[x]` Implement bounded JSON Schema dialect validation without remote references.
- `[x]` Attach frozen recovery declarations to every RegisteredTool and durable execution.
- `[x]` Migrate local tools without changing ordinary outcomes or policy behavior.
- `[x]` Prove unknown dynamic tool validation, approval, execution and recovery stability.
- `[x]` Run focused dynamic-tool tests and the standard quality gates.

## Boundaries

- Do not implement MCP, learned routing or unrestricted Skill injection.
- Do not use real credentials, networked MCP Servers, live Providers or user state.
- Work is on `feat/stage6-dynamic-tools`; preserve the verified Subplan70 commits and do not
  introduce MCP-specific branches into ToolExecutor or recovery.
- Preserve AgentLoop/ConversationLog, permission policy defaults, public events and Stage 4/5 behavior.
