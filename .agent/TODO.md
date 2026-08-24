# TODO

## Current stage

Stage 6 implementation in progress.

## Active subplan

Subplan 73 — MCP Runtime and Security Adapter.

## Tasks (Subplan 73)

- `[x]` Freeze enabled Server/Catalog evidence into new AgentRun launch and tool snapshots.
- `[x]` Add narrowly scoped MCP review evidence and deny-first compound policy evaluation.
- `[x]` Register MCP tools through the existing validator/recovery seams and ordinary ToolExecutor.
- `[x]` Implement lazy per-AgentRun pool, cancellation/timeout/crash handling and no-retry behavior.
- `[x]` Normalize bounded MCP result content and import binary/embedded payloads as Artifacts.
- `[x]` Add status/doctor facts, Fake runtime/policy/result/recovery tests and standard quality gates.

## Boundaries

- Do not implement backup v2, doctor closeout, learned routing or unrestricted Skill injection.
- Do not use real credentials, networked MCP Servers, live Providers or user state.
- Work is on `codex/feat/stage6-mcp-runtime`; preserve the verified Subplan72 commits and do not
  introduce MCP-specific branches into ToolExecutor or recovery.
- Preserve AgentLoop/ConversationLog, permission policy defaults, public events and Stage 4/5 behavior.
