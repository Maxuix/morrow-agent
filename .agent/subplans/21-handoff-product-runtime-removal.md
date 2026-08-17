# Subplan 21 — Handoff Product and Runtime Removal

> Status: completed
> Depends on: accepted Stage 2 baseline (`831c4ea`)
> Scope boundary: remove every production caller now; only uncalled definitions may survive to 22

## Goal

Remove every user-visible and runtime-active Handoff path while leaving ordinary chat,
tool execution, Profile, Preferences, natural-language configuration, and the process-local
Session product usable. At completion, no startup, command, context, configuration, exit,
reset, inspection, onboarding, or bootstrap path can load, generate, inject, edit, clear,
or publish Handoff state.

Only the uncalled `Decision`, `Handoff`, `HandoffDocument`, `ConfigPatch.target` literal,
ProjectStateStore methods, and YAML methods may remain until Subplan 22. They are not a
temporary compatibility surface: no production caller may reach them.

## Post-subplan product behavior

```text
Startup
→ resolve workspace
→ inspect Profile and workspace Preferences
→ onboard Profile only
→ construct process-local SessionApplication
→ enter REPL

Dirty /new
→ show discard warning
→ explicit confirmation
→ discard_new resets Session, or cancellation keeps it

Dirty /exit
→ show the same discard warning
→ confirmed discard exits 0, cancellation remains in the REPL
```

`/handoff` and `/continue` return exactly the existing ordinary
`CommandResult([f"未知命令：{command}"])` with no action/value. No “removed” alias, model
completion, workspace write, or special read-only branch occurs during `/new`, `/exit`, or
input EOF.

## Executable tasks

### HR.21.1 — Freeze the baseline and encode removal contracts

- Record that product/source/test behavior comes from `831c4ea` and that pre-implementation
  worktree changes are restricted to `.agent/` planning files.
- Run the current baseline tests, including the pre-split
  `tests/test_structured_and_handoff.py`, before production edits.
- Capture exact source, test, documentation, and `build_session_application()` caller
  inventories with `rg`.
- Add initially failing contracts for:
  - no `WorkspaceInspection.handoff`, startup Handoff display, or context
    Handoff/`current_goal` projection;
  - ordinary unknown-command behavior for `/handoff` and `/continue`;
  - exact dirty `/new`, dirty `/exit`, clean EOF, and confirmation-EOF outcomes;
  - no `provider.complete()` call during reset/exit, using a Provider that fails if called;
  - valid, corrupt, and future-version legacy primary/backup sentinel files remaining
    byte-identical and not affecting read-only state or supported writes;
  - no startup/chat/config/reset/exit read attempt, using a ProjectStateStore spy whose
    legacy Handoff methods fail if invoked, so byte preservation is not the only evidence.

### HR.21.2 — Simplify Session and structured context

- Remove `loaded_handoff`, `handoff_source_revision`, and `is_continuation` from Session,
  including reset behavior and model imports.
- Keep `dirty` solely as process-local discard protection and document that meaning.
- Remove `handoff_fallback` from `ContextPurpose` and delete `_fallback_messages()`.
- Make `_non_chat` structured-only with explicit supported-purpose dispatch; remove the
  fallback `else` that can silently route a future purpose.
- Limit system-state JSON to supported Profile/Preferences state; remove Handoff,
  `current_goal`, and continuation fields.
- Replace Handoff/continuity promises in `SYSTEM_BOUNDARY` with the truthful process-local
  boundary.
- Preserve chat and structured projections, legal ToolCycle pairing, context budgets,
  pure snapshots, and Structured/config exclusion of ToolMessage envelopes.

### HR.21.3 — Remove commands and unify terminal lifecycle behavior

- Remove `handoff_service` from `CommandService`; delete `load_handoff()`,
  `handoff_revision()`, and `clear_handoff()`.
- Delete `/handoff`, `/continue`, their action values, and all continuation/update/clear
  terminal branches. Both commands must fall through to exactly
  `CommandResult([f"未知命令：{command}"])` with no action/value and no deprecated or
  removal-specific alias.
- Change `/status` from “有未交接内容” semantics to “有未保存的进程内对话”.
- Replace `switch_new` with the single action `discard_new`; delete Handoff
  save/discard/switch branches and Handoff-generation helpers.
- Make `/new` reset only after explicit confirmation; cancellation leaves Session and
  ConversationLog unchanged.
- Unify former independent, continuation, and read-only exit paths into one contract:

  | State/input | Result |
  |---|---|
  | clean `/exit` or clean input EOF | exit `0` |
  | dirty `/exit`, confirmed discard | exit `0` |
  | dirty `/exit`, cancelled | remain in REPL |
  | EOF during dirty-discard confirmation | exit `2`; no reset/write |

- Ensure `/new`, `/exit`, and confirmation EOF never call the Provider or write workspace
  state. Reduce the public terminal entry point to
  `run_repl(orchestrator, *, session=None)`; it must no longer receive a Handoff service,
  project store, or workspace ID.

### HR.21.4 — Close configuration and natural-language write routes

- Remove `("workspace", "handoff")` from `ALLOWED_PATHS` and remove `交接` from
  `ConfigIntentGate.scope_words`.
- While the target literal temporarily survives for Subplan 22, make validation reject
  `target="handoff"` and make `ConfigPatchService.apply()` explicitly dispatch only
  Preferences/Profile. Do not leave a fallback `else` that loads or writes Handoff.
- Remove runtime Session-update behavior for an applied Handoff target.
- Update natural-language and direct patch tests to prove unsupported Handoff targets are
  rejected without state access, while supported scalar/list/remove semantics stay green.

### HR.21.5 — Remove inspection, onboarding, startup, and positional composition

- Remove `WorkspaceInspection.handoff`, the `inspect()` read, and Handoff's contribution
  to workspace read-only mode.
- Remove `current_goal` from `WorkspaceStateService.onboard()`, stop initial Handoff
  publication, and define the return as the written Profile revision or `None` when no
  Profile write occurs.
- Lock degraded behavior:
  - corrupt/future Profile makes the workspace read-only and blocks Profile/workspace-
    Preferences writes;
  - corrupt/future legacy Handoff is ignored and cannot block those writes;
  - corrupt Preferences isolates only that Preferences layer.
- Remove onboarding goal prompts, startup Handoff display/revision/instructions, and
  Handoff wording from read-only notices.
- Remove `HandoffService` construction and injection from bootstrap.
- Replace the five-element positional return of `build_session_application()` with a
  named `SessionApplication` composition object containing `session`, `context_builder`,
  `commands`, and `orchestrator`; update every source/test caller to named fields.
- Remove Handoff arguments from `run_repl()` and all callers.
- Replace Handoff/continuity claims in the Typer help, terminal greeting, and
  `src/morrow/__init__.py` tagline with neutral current-product wording.

### HR.21.6 — Delete the active service and split generic structured coverage

- Delete `src/morrow/services/handoff.py` after the last caller is gone.
- Remove application/package imports and references.
- Keep `runtime/structured.py` and its generic repair/deadline behavior.
- Move generic structured completion coverage from
  `tests/test_structured_and_handoff.py` into `tests/test_structured.py` with a test-local
  schema; delete generation/fallback tests owned only by Handoff.
- Retain generic structured overflow/error, repair, deadline, and context-projection
  coverage.

### HR.21.7 — Rebaseline the integrated product and pass the slice gate

- Replace Handoff exit/switch tests with exact dirty process-local discard contracts.
- Replace `/new`/`/continue` product cases with `/new` reset and explicit no-resume
  behavior.
- In `tests/test_preferences_and_orchestration.py`:
  - delete `test_handoff_decision_remove_...` when `apply()` stops accepting the target;
  - retain the supported config/workspace cases of
    `test_command_service_routes_deterministic_edits_to_one_patch_path` and remove its
    `/handoff edit` branch;
  - split `test_profile_or_handoff_failure_enforces_workspace_wide_degraded_mode` into
    Profile corruption/future-version read-only and ignored legacy-Handoff contracts;
  - keep Preferences isolation in
    `test_corrupt_workspace_preferences_is_an_isolated_non_overwritable_empty_layer` while
    removing `/continue`/`load_handoff` assertions.
- Rewrite `test_context_excludes_handoff_until_explicit_load` to prove system/context JSON
  never contains the `handoff` key or `current_goal`; retain only the structured half of
  `test_explicit_projections_keep_tool_data_out_of_structured_and_fallback_views`.
- Rewrite the ConversationLog reset assertion that currently mentions an untouched
  Handoff, and replace Stage-boundary five-tuple/Handoff assertions with named composition
  fields and the two remaining workspace document types.
- Update all composition-root consumers and the onboarding signature/return assertions.
- Explicitly cover `tests/test_stage_boundary.py`, `tests/test_conversation_and_loop.py`,
  `tests/test_preferences_and_orchestration.py`, and
  `tests/test_stage2_product_acceptance.py`, in addition to terminal, context, structured,
  workspace, and E2E suites.
- Run the complete offline suite and all quality gates; do not hand a red tree to Subplan
  22.

## Completion criteria

- No user-visible Handoff command, prompt, status, action, startup message, or product
  tagline remains.
- No runtime Session/Context field or production caller loads, writes, or dispatches to
  Handoff, including direct and natural-language configuration.
- `/handoff` and `/continue` have ordinary unknown-command behavior.
- Exact `/new`, `/exit`, clean EOF, and confirmation EOF contracts pass without model calls
  or workspace writes.
- Legacy primary/backup files cannot affect availability or supported writes and remain
  byte-identical.
- `HandoffService` and its source file are deleted; generic structured completion remains.
- The integrated full offline tree, not only focused tests, is green.
- Remaining `src/` references are limited to the uncalled definitions explicitly assigned
  to Subplan 22.

## Validation

Run focused tests while iterating, then run the complete gate:

```bash
uv run pytest -q tests/test_cli_commands.py tests/test_terminal.py \
  tests/test_preferences_and_orchestration.py tests/test_context_runtime.py \
  tests/test_context_projections.py tests/test_structured.py \
  tests/test_state_and_workspace.py tests/test_conversation_and_loop.py \
  tests/test_stage_boundary.py tests/test_stage2_e2e.py \
  tests/test_stage2_product_acceptance.py
uv run pytest -m 'not live'
uv run ruff format --check .
uv run ruff check .
uv run python -m compileall -q src tests
uv run morrow --help
git diff --check
```
