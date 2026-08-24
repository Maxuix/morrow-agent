# Subplan 70 — Provider and Model Control Plane

> Status: in progress
> Branch: `codex/feat/stage6-provider-control`
> Prerequisite: Subplan 69 complete; per-run preparation from Subplan 64

## Objective

Complete Adapter/Model capability metadata and implement provider/model add/show/list/test/sync/use/
remove through versioned configuration and per-run resolution, without branching AgentLoop, Session
or Task code and without silent model fallback.

## Ownership

- focused contracts under `src/morrow/core/providers.py` or the Subplan 64 capability module
- `src/morrow/application/providers/` configuration, query, test and sync services
- bounded changes to `adapters/registry.py` and existing provider adapters
- `src/morrow/interfaces/providers_cli.py` and thin CLI registration
- `tests/test_provider_control.py`, adapter contract and per-run refresh tests

## Tasks

1. Finalize Adapter default and exact Model capability models: streaming, tool protocol, multiple
   tool calls, structured output, safe request/context limit, input modalities and cost metadata
   source/time.
2. Extend Adapter registration without breaking current presets. Unknown fields resolve
   conservatively; exact Model overrides may narrow but cannot invent unsupported Adapter protocol.
3. Implement Provider add/show/list/remove/test with ProviderConfig validation, CredentialRef only,
   URL/adapter checks and current configuration OCC/backup semantics.
4. Implement Model add/show/list/sync/use/remove. `sync` uses the adapter's explicit discovery port,
   updates one Provider projection and never changes active model automatically.
5. Make `use` update the existing `active_model`; do not introduce default Provider. Remove must
   refuse the active model and must preserve historical AgentRun rehydration evidence.
6. Ensure provider tests and sync sanitize errors and never store or display credentials, raw
   response bodies or tracebacks.
7. Add a complete Fake second Adapter proving registry-only extension and capability snapshots.
8. Verify same-process next-new-run changes through Subplan 64; current/replay/recovery runs stay
   frozen and there is no fallback on construction/call failure.

## Validation

- CRUD/OCC/replay, CredentialRef isolation, adapter/model capability combinations, unknown ability,
  sync drift/failure, active removal refusal and sanitized test output.
- Second Adapter contract without edits to AgentLoop/Session/TaskStore.
- CLI helps and projections; affected configuration/preference migration compatibility.
- Focused provider tests, standard quality commands and full non-live suite.

## Exit criteria

- Roadmap capability fields are complete and frozen per exact ModelRef.
- All control-plane actions use application services and current config authority.
- Model changes affect exactly the next new AgentRun with no silent fallback.
