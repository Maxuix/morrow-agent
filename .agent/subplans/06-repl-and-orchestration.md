# Subplan 06 — REPL and Orchestration

> Stage: 1A  
> Status: completed
> Parent: [Stage 1 implementation plan](../PLAN.md)

## Objective

Connect CLI input, local commands, context construction, single-turn execution, event rendering, cancellation, and exit through a thin orchestration layer.

## Prerequisites

- Subplans 03 through 05 are complete.

## Tasks

1. Implement `morrow [--dir PATH]` startup, path validation, workspace resolution, Provider readiness checks, and the terminal REPL. Before a new workspace ID or index entry is published, show the inferred name and normalized path and require interface confirmation. When no valid `active_model` exists, the interface collects the secret without echo and drives Subplan 04's Provider-add use case before entering the REPL.
2. Define the in-process session object with `session_id`, messages, session Preferences, loaded Handoff revision, and dirty state.
3. Implement Stage 1A `SessionOrchestrator` dispatch with exactly two paths: slash commands first; every other input goes directly through `ContextBuilder` to ordinary streaming. No configuration intent gate or configuration `complete()` call exists before Subplan 08 integrates it.
4. Introduce `CommandService` as the owner of local command use cases and register the Stage 1A surface: `/workspace`, `/handoff`, `/status`, startup-only/minimal `/continue`, and `/exit`. Orchestrator dispatches and transitions; it does not directly edit Profile, Handoff, or Preferences.
5. Ensure read-only commands never call a model or mutate dirty state.
6. On startup, display an available Handoff summary without adding it to context; load exactly the displayed revision only after explicit continuation. Render moved-workspace candidates as suggestions only, with no implicit inheritance or mutation.
7. Keep interface/help/error text in Simplified Chinese; select the initial model reply language from terminal locale without adding a required onboarding question. The later `preferences.language` field affects model replies only, not interface localization.
8. Connect event rendering and one sanitizer-backed diagnostic boundary without exposing SDK objects, raw tracebacks, reasoning, secrets, or unsanitized file logs.
9. Apply the terminal pattern proven in Subplan 01 so first `Ctrl+C` cancels the current generation and `Ctrl+D` enters normal exit orchestration.
10. Define stable process exits, including code `0` for normal completion or a successful deterministic Handoff fallback and code `2` when final Handoff preservation fails; keep usage/configuration failures distinct.
11. Make `/status` an offline view of loaded-Handoff status, dirty/unhanded content, and relevant revision.
12. Expose one new-workspace onboarding hook that Subplan 07 fills; do not duplicate startup orchestration there.
13. Test orchestration directly with injected input/events; retain only focused real-terminal smoke tests.

## Verification

- `--dir` and current-directory startup use the same resolver path.
- Local commands work with the network guard enabled and do not enter chat history.
- All non-command input makes zero configuration-extraction calls in Stage 1A.
- Direct chat after Handoff discovery remains independent; explicit continuation includes the selected revision.
- Cancellation completes the current turn as cancelled and the next turn still works.
- Fatal model failure emits error then completion(error), with no later events.
- CLI/interface modules do not read YAML or credentials directly.
- Moved-workspace candidates are displayed without being selected, written, or loaded.
- Rejecting a new-workspace confirmation leaves the index and workspace state unchanged.

## Completion criteria

- The application has one understandable input-dispatch path and one composition root.
- Real terminal behavior matches the spike without moving business decisions into rendering code.
- All orchestration integration and focused terminal smoke tests pass.

## Deliverables

- CLI/REPL interface.
- Session orchestrator, Stage 1A `CommandService`, and minimal command routing.
- Typed event renderer and exit-code mapping.
