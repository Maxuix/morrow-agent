# Subplan 15 — Context and Domain-Invariant Remediation

> Stage: 1B remediation
> Status: completed
> Parent: [Stage 1 implementation plan](../PLAN.md)
> Depends on: Subplan 14

## Objective

Prevent malformed persisted continuity state and ensure context-budget pruning never creates misleading orphaned assistant messages.

## Required design decisions

1. History admission and history selection are separate contracts. User messages retained after cancellation/error remain valid lone history items, but an assistant message may enter a context only with its corresponding preceding user message as one atomic completed turn.
2. Context selection proceeds from newest atomic history units backward and never skips a newer oversized unit to admit older units. Fixed system/state context and the current user message remain mandatory; oversized mandatory content fails before Provider invocation.
3. `Handoff.current_goal` is trimmed and non-empty on every `state: present` publication path: model generation/repair, deterministic edit, fallback, onboarding, and present-state write. Continuation fallback copies the last valid present Handoff, preserves its existing project goal/decisions/open items, and only adds the bounded `recovery_note`; it fills `current_goal` only if the copied payload would otherwise publish an empty/whitespace goal. Any explicitly saved independent session (no Handoff loaded into that session) derives `current_goal` from the sanitized last user request when non-empty and otherwise uses the fixed safe goal `继续推进当前工作`; this remains true when a different present Handoff exists on disk but was only discovered/displayed, as explicit continuity forbids copying an unloaded document. Missing/cleared disk state follows the same independent-save rule. A `state: cleared` publication remains an envelope-only tombstone and never constructs a placeholder Handoff or invents a goal. No other domain text field is made newly required by this remediation.
4. Load-time policy is explicit: an existing Handoff whose `current_goal` is empty/whitespace is corrupt, is never injected or overwritten, and triggers the documented workspace degraded mode; it is not silently assigned an invented goal. Persisted document revisions must be non-negative integers and persisted `updated_at` values timezone-aware; existing documents violating either rule are likewise corrupt and recoverable only through the documented backup/recovery path. Existing documents satisfying these exact rules remain readable.

## Executable tasks

1. Add context-budget tests where an assistant fits but its paired user does not, where the newest full turn does not fit, where cancelled/error users are unmatched, and where fixed/current content exceeds budget.
2. Refactor context pruning around explicit atomic history units while preserving original order, current-message uniqueness, Handoff explicit-load rules, and three-layer preference state.
3. Add domain validation tests for empty/whitespace Handoff goals on publish and load, deterministic fallback after sanitization, negative revisions, naive timestamps, valid aware timestamps, degraded-mode startup, and existing valid on-disk documents.
4. Implement validators and normalization without using unchecked `model_copy` paths to bypass publication validation.
5. Verify ConfigPatch, structured completion, continuation-copy fallback, independent missing/cleared fallback, onboarding, backup loading, and recreate-to-present all use the strengthened present-payload models. Separately verify clear uses only the version-2 envelope validator and cannot construct or require a Handoff payload.
6. Run targeted context/model/state tests, the complete non-Live suite, Ruff format/check, and compile checks.

## Verification

- No context contains an assistant message without its paired user message.
- Cancellation/error user messages remain available according to the Stage 1 history contract.
- Context budgeting is deterministic and never silently truncates mandatory content or the current user input.
- Empty Handoff goals, negative revisions, and naive persisted timestamps fail typed validation before publication.
- Continuation fallback preserves the copied valid goal, independent missing/cleared fallback always derives a non-empty safe goal, and cleared tombstones remain payload-free.
- Existing valid state remains loadable and recovery behavior remains byte-preserving.

## Completion criteria

- Confirmed orphan-assistant and domain-invariant defects have direct regression tests.
- All paths producing Handoff/state documents pass the strengthened models.
- No summarization, persistent chat history, or other Stage 4 capability is introduced.

## Deliverables

- Atomic-turn context pruning.
- Strengthened continuity and persistence invariants.
