# Progress Tracker

## Current status

Subplan 64 (Per-AgentRun Runtime Preparation) complete and merged to local `main`. Provider, Model,
capabilities, RunPolicy, ContextBuilder and ToolExecutor are now per-AgentRun dependencies in the
production path; Stage 4/5 behavior and all recovery paths keep passing.

## Last completed work

- Added core contracts `core/agent_runs.py`: `ProviderCapabilities`/`ModelCapabilities`/
  `ExactModelCapabilities` + `exact_model_capabilities()` merge, sanitized `ProviderRuntimeSnapshot`
  and immutable `PreparedAgentRunSpec`. Moved `RunPolicy`/`ProviderToolSupport` into `core/models.py`
  (policy.py re-exports; no import-site churn).
- Added `application/agent_runs/preparation.py`: `AgentRunPreparationService.prepare_new()` reads the
  current global config once per run (credential resolved, never frozen); `rehydrate()` rebuilds from
  stored evidence only (frozen CredentialRef unresolvable -> `ProviderUnavailableError`, no fallback;
  tool-schema drift -> error; pre-Stage-6 snapshots -> boot legacy runtime); `PreparedAgentRunRuntime`
  owns bounded idempotent cleanup.
- `TurnSubmissionCoordinator.probe()` classifies new/closed-replay/recovery/conflict read-only; the
  in-transaction receipt recheck wins concurrent duplicates (exactly one AgentRun persisted, unused
  runtime closed by the run consumer). `AgentRunSnapshot` gained optional `provider_runtime` +
  `run_policy` evidence with a dedicated strict redaction for the provider subtree (the generic
  "credential" needle scan would reject the typed CredentialRef name).
- `AgentLoop.run_task()` builds its runner/tool cycle per invocation from a prepared run; orchestrator
  probes before preparing, lets admission errors surface as ordered events, and rehydrates on recovery
  resume; bootstrap wires the preparation service, legacy runtime and orchestrator.
- Validation: `tests/test_agent_run_preparation.py` 14 tests (prepare evidence, active-model change
  affects the next run only, closed replay performs no provider construction, open-receipt recovery,
  snapshot freeze, exact rehydrate without current-config reads, no-credential fallback refusal,
  legacy decode, tool drift, concurrent duplicate, runtime close); full offline suite
  `961 passed, 1 skipped (mcp spike), 2 deselected`; ruff format/check, compileall,
  `git diff --check` passed; `pyproject.toml`/`uv.lock` unchanged; budget re-measured
  (AgentRunSnapshot base 4 367 B with Stage 6 evidence, 5 997 B with 9 refs, of 64 KiB).

## Active task

None in progress. Next: activate Subplan 65 (Skill package and Catalog foundation) from latest `main`.

## Next action

Create `codex/feat/stage6-skill-foundation` for Subplan 65 when the user asks to continue.

## Dependency gate

No dependency added by Subplans 63–64. Before Subplan 72, ask the user to approve the exact change:
`mcp >= 2.0.0, < 3` and `jsonschema >= 4.20, < 5` (rationale and versions in the ADR). If denied,
complete Subplans 65–71 normally and mark 72–73 blocked.

## Blockers

None for Subplans 65–71. MCP implementation Subplans 72–73 are conditional on the recorded
dependency decision and explicit approval if new packages are recommended.

## Notes

- `main` is two local commits ahead of `origin/main` after the Subplan 63/64 merges; remote
  publication was not placed in scope, so no push was made.
- The spike test file skips cleanly in the default dev env (`importorskip("mcp")`).

## Preserved history

Detailed Stage 4/5 evidence remains in Git history and completed subplans; Subplan 63 evidence is in
the ADR and `tests/spikes/`; Subplan 64 evidence is in `tests/test_agent_run_preparation.py`.
It is not duplicated in this active tracker.