# TODO

## Current stage

Stage 6 implementation is complete locally through Subplan 77.

## Active subplan

No active implementation subplan. Subplan 77 is complete locally.

## Tasks (Subplan 77)

- `[x]` Preserve bounded Script diagnostic codes/messages through ToolExecutor.
- `[x]` Preserve only explicitly public-safe diagnostics at the AgentLoop fallback boundary.
- `[x]` Render frozen `selection_id` in low-authority Skill context.
- `[x]` Prove unknown/secret-bearing errors remain hidden and context grants no authority.
- `[x]` Run focused and full offline/quality gates and reconcile execution state.
- `[x]` Commit verified work, fast-forward local `main`, and retire the topic branch.

## Boundaries

- Do not change permission defaults, secret/path/schema/payload safety invariants, public events,
  AgentLoop/ConversationLog ownership, or extension authority.
- Do not use real credentials, networked MCP Servers, live Providers or user state.
- Do not expose raw exceptions, tracebacks, credentials, host paths or arbitrary handler text.
- Do not change public event keys/lifecycle or make Skill context authoritative.
- Preserve the verified Stage 6 history and all unrelated user changes.
