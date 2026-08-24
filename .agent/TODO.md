# TODO

## Current stage

Stage 6 implementation in progress.

## Active subplan

None. Subplan 70 — Provider and Model Control Plane is complete locally; Subplan 71 is next.

## Tasks (Subplan 70)

- `[x]` Finalize Adapter defaults and exact Model capability snapshots.
- `[x]` Extend registry and model override validation without widening Adapter support.
- `[x]` Implement Provider CRUD/test and Model CRUD/sync/use/remove services.
- `[x]` Add provider/model CLI projections and sanitized test/sync output.
- `[x]` Prove second Adapter isolation and next-new-AgentRun refresh/freeze behavior.
- `[x]` Run focused provider tests and the standard quality gates.

## Boundaries

- Do not implement MCP, learned routing or unrestricted Skill injection.
- Do not use real credentials, networked MCP Servers, live Providers or user state.
- Work completed on `feat/stage6-provider-control`; preserve Subplans 65–69 and their review
  repairs while moving the verified commits to local `main`.
- Preserve AgentLoop/ConversationLog, permission policy defaults, public events and Stage 4/5 behavior.
