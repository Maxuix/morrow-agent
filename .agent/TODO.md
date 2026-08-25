# TODO

## Current stage

Stage 6 implementation is complete locally through Subplan 76.

## Active subplan

No active implementation subplan. Subplan 76 is complete locally.

## Tasks (Subplan 76)

- `[x]` Inventory hardcoded runtime defaults and fixed safety invariants.
- `[x]` Add packaged `runtime-policy.toml` and typed safe override contracts.
- `[x]` Preserve optional runtime-policy overrides in user `config.yaml`.
- `[x]` Wire effective AgentRun/Learning/Preference review policy at bootstrap.
- `[x]` Add focused regression tests and hardcoding classification documentation.
- `[x]` Run full offline/quality/CLI gates and reconcile execution state.
- `[x]` Commit verified work, fast-forward local `main`, and retire the topic branch.

## Boundaries

- Do not change permission defaults, secret/path/schema/payload safety invariants, public events,
  AgentLoop/ConversationLog ownership, or extension authority.
- Do not use real credentials, networked MCP Servers, live Providers or user state.
- Do not make per-MCP, per-script or per-command timeouts a competing global authority.
- Preserve the verified Stage 6 history and all unrelated user changes.
