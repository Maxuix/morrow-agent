# TODO

## Current stage

Stage 6 implementation in progress.

## Active subplan

Subplan 70 — Provider and Model Control Plane (active in the current working tree).

## Tasks (Subplan 70)

- `[>]` Finalize Adapter defaults and exact Model capability snapshots.
- `[ ]` Extend registry and model override validation without widening Adapter support.
- `[ ]` Implement Provider CRUD/test and Model CRUD/sync/use/remove services.
- `[ ]` Add provider/model CLI projections and sanitized test/sync output.
- `[ ]` Prove second Adapter isolation and next-new-AgentRun refresh/freeze behavior.
- `[ ]` Run focused provider tests and the standard quality gates.

## Boundaries

- Do not implement MCP, learned routing or unrestricted Skill injection.
- Do not use real credentials, networked MCP Servers, live Providers or user state.
- Worktree is on `main` because `.git` refs are read-only; preserve Subplans 65–66 and their review
  repairs while implementing this subplan.
- Preserve AgentLoop/ConversationLog, permission policy defaults, public events and Stage 4/5 behavior.
