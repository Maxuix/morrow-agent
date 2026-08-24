# Subplan 64 — Per-AgentRun Runtime Preparation

> Status: pending
> Branch: `codex/feat/stage6-run-preparation`
> Prerequisite: Subplan 63 complete

## Objective

Make Provider, Model, capabilities, RunPolicy, ContextBuilder and ToolExecutor true per-AgentRun
dependencies, while preserving receipt idempotency, recovery, ConversationLog ownership and current
tool behavior. Skills and MCP plug into this seam later; this subplan does not implement them.

## Ownership

- new `src/morrow/application/agent_runs/` package for prepared spec/runtime and preparation service
- thin edits to `application/turn_lifecycle.py`, `application/orchestrator.py`, `runtime/agent.py`,
  `runtime/session.py` and `bootstrap.py`
- capability contracts in a focused new module; registry compatibility in `adapters/registry.py`
- `tests/test_agent_run_preparation.py` plus affected loop/recovery tests

## Tasks

1. Introduce `PreparedAgentRunSpec`, `PreparedAgentRunRuntime` and cleanup ownership without storing
   SDK/provider objects in Pydantic or SQLite. Freeze a sanitized Provider runtime snapshot with
   adapter/provider/model/API-model IDs, endpoint, CredentialRef name/version, capabilities and
   config revision/digest; never store the credential value.
2. Add complete Adapter/Model capability contracts and an exact-Model resolution function. Preserve
   current tool-support defaults through a compatibility constructor.
3. Implement `TurnSubmissionCoordinator.probe()` for new/closed-replay/recovery/conflict without
   creating IDs, reading mutable extension state or performing external work.
4. Implement `prepare_new()` from current global config and existing local tools; resolve Provider,
   Model, capabilities and RunPolicy for this run.
5. Implement `rehydrate()` from stored AgentRun evidence. It must not read current active model or
   future Skill/MCP state; a missing frozen CredentialRef becomes unavailable rather than fallback.
6. Change `AgentLoop.run_task()` to consume a prepared run and construct its runner/tool cycle per
   invocation. Keep `run_turn()` a thin delegate and ConversationLog as the only history writer.
7. Retain the receipt recheck inside Turn admission. On a concurrent loser, close the unused runtime
   and return the winning receipt outcome.
8. Add bounded cleanup for success, failure, cancellation, replay and preparation failure.
9. Preserve old AgentRun snapshot decoding; add only optional/reference fields with explicit budget
   checks and doctor coverage.

## Validation

- Focused: new preparation tests, `tests/test_conversation_and_loop.py`,
  `tests/test_stage4_recovery_crash.py`, `tests/test_stage5_memory_agent_run.py`, and
  `tests/test_preference_context.py`.
- Prove: active model change affects next new AgentRun in the same process; current run stays frozen;
  closed replay performs no provider construction; recovery uses historical model/policy/tool facts;
  duplicate concurrent submit persists one run.
- Run the standard subplan quality commands and full non-live suite because this changes the main
  execution path.

## Exit criteria

- No long-lived AgentLoop field is the configuration authority for Provider/Model/RunPolicy/tools.
- Current Stage 4/5 behavior and all recovery paths remain passing.
- Skills/MCP can later contribute prepared refs and tools without modifying AgentLoop branches.
