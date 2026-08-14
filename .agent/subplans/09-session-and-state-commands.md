# Subplan 09 — Session and State Commands

> Stage: 1B  
> Status: completed
> Parent: [Stage 1 implementation plan](../PLAN.md)

## Objective

Complete the minimal session, Profile, and Handoff control surface without introducing persistent chat sessions or silently discarding dirty in-process context.

## Prerequisites

- Subplan 08 is complete.

## Tasks

1. Implement `/new` to create a fresh session ID, clear messages and session Preferences, and load no Handoff.
2. Extend `/continue` for safe in-session switching to a currently valid Handoff revision.
3. Before switching a dirty continuation, save a new Handoff through the generated-or-deterministic path; switch only after success.
4. Before switching a dirty independent session, offer explicit save-as-Handoff, discard-in-memory, or cancel choices.
5. On ordinary exit from a dirty independent session, explain that it will not replace the old Handoff and require explicit confirmation.
6. Preserve the original session ID, messages, Preferences, dirty state, and Handoff source if saving fails.
7. Extend the Subplan 06 `CommandService`; Orchestrator only dispatches commands and applies returned session transitions.
8. Implement `/workspace edit` and `/workspace reset` by constructing field-whitelisted `ConfigPatch` transactions through Subplan 08's validator/application path; reset only the Profile.
9. Implement `/handoff update` by obtaining a complete typed Handoff through Subplan 05's `ContextBuilder`-backed structured helper and publishing it through the same complete-Handoff replacement service used by exit preservation. `Ctrl+C` cancels the command with no fallback and no write; only timeout, model error, or invalid Schema uses the explicit-update deterministic fallback. Implement `/handoff edit` and `/handoff clear` through the authoritative ConfigPatch path; clear only Handoff state and unload it from the current session.
10. When an independent session is explicitly saved, use the most recent user request as the deterministic fallback `current_goal`, publish a new Handoff, and set its revision as the session source only after successful publication.
11. Require preview/confirmation for reset and clear. Complete Handoff replacement and field-level ConfigPatch are the only two write paths; both require full schema validation, expected revision, and the same atomic project-state adapter.
12. Reuse the initial Handoff schema's normalized decision uniqueness and require exact normalized-text matching for removal; zero or multiple matches refuse the edit.
13. Ensure read-only commands do not mark the session dirty and successful mutations refresh the next-turn context snapshot.
14. Test command behavior through `SessionOrchestrator`, with only focused confirmation/terminal smoke coverage at the interface layer.

## Verification

- Dirty contexts are never silently discarded by `/new`, `/continue`, clear/reset, or exit.
- Every failed preservation attempt leaves the entire original in-process session intact.
- Profile reset leaves workspace identity, Preferences, and Handoff unchanged.
- Handoff clear leaves Profile and Preferences unchanged and prevents future injection in the current session.
- Field-level deterministic edits traverse one ConfigPatch path; complete generated Handoffs traverse one full-document replacement path; both converge on the same revision-checked atomic adapter.
- Cancelling `/handoff update` returns to the REPL with the session and disk unchanged; non-cancel generation failures follow the deterministic fallback path.
- Read-only commands remain offline and do not mutate state/history/dirty flags.
- All session-switch cases are exercised at the orchestrator level without a general PTY harness.

## Completion criteria

- `S1B-03` passes, and the reset/clear portion of `S1B-06` has evidence ready for Subplan 11 aggregation.
- Session control remains an in-process Stage 1 mechanism, not a prematurely persistent public state machine.
- State commands honor exact ownership and atomic write boundaries.

## Deliverables

- Minimal safe `/new` and `/continue` behavior.
- Profile and Handoff edit/reset/update/clear use cases.
- Orchestrator-level dirty-session transition suite.
