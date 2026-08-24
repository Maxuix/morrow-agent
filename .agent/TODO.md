# TODO

## Current stage

Stage 6 implementation in progress.

## Active subplan

Subplan 64 — Per-AgentRun Runtime Preparation (completed on `codex/feat/stage6-run-preparation`).

## Tasks (Subplan 64)

- `[x]` Introduce `PreparedAgentRunSpec`, `PreparedAgentRunRuntime` and cleanup ownership without
  storing SDK/provider objects in Pydantic or SQLite; freeze a sanitized Provider runtime snapshot.
- `[x]` Add complete Adapter/Model capability contracts and an exact-Model resolution function;
  preserve current tool-support defaults through a compatibility constructor.
- `[x]` Implement `TurnSubmissionCoordinator.probe()` for new/closed-replay/recovery/conflict without
  creating IDs, reading mutable extension state or performing external work.
- `[x]` Implement `prepare_new()` from current global config and existing local tools.
- `[x]` Implement `rehydrate()` from stored AgentRun evidence; missing frozen CredentialRef becomes
  unavailable rather than fallback.
- `[x]` Change `AgentLoop.run_task()` to consume a prepared run and construct its runner/tool cycle
  per invocation; keep `run_turn()` a thin delegate and ConversationLog as the only history writer.
- `[x]` Retain the receipt recheck inside Turn admission; concurrent loser closes the unused runtime.
- `[x]` Add bounded cleanup for success, failure, cancellation, replay and preparation failure.
- `[x]` Preserve old AgentRun snapshot decoding; add optional reference fields with budget checks and
  decode/doctor coverage.

## Boundaries

- This subplan changes no production behavior and adds no dependency.
- Do not use real credentials, networked MCP Servers, live Providers or user state.
- Next: Subplan 65 (Skill package and Catalog foundation) from latest `main`.
- Preserve AgentLoop/ConversationLog, permission policy defaults, public events and Stage 4/5 behavior.