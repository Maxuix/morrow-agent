# Subplan 12 — Workspace Identity and Durable State Remediation

> Stage: 1B remediation
> Status: completed
> Parent: [Stage 1 implementation plan](../PLAN.md)

## Objective

Restore the two foundational invariants that later Agent work depends on: one canonical workspace identity per effective project root, and a monotonic revision history that remains concurrency-safe across writes and clears.

This subplan owns confirmed workspace-identity defects, clear/revision rollback, and the state-publication durability portion of the P2 findings. It must not add project-content reads, Git subprocesses, Stage 2 tools, or unrelated state features.

## Required design decisions

1. Treat a `WorkspaceResolution` candidate as stale, untrusted input. Confirmation must re-check the current index while holding its transaction lock. Repeated or concurrent confirmation of the same canonical identity is idempotent and returns the one authoritative existing/new entry; it must never publish a second ID.
2. Use the same canonical identity rules for resolve, confirm, and relink:
   - an already registered exact path remains authoritative if that directory later becomes a Git repository;
   - otherwise the nearest Git root is the identity boundary;
   - non-Git paths use the nearest registered parent or their normalized candidate path;
   - separate Git worktree roots remain separate identities;
   - existing paths use `samefile` semantics without manual case folding.
3. Relink persists the same canonical identity path that confirmation would publish: the nearest Git root when present, otherwise the normalized requested directory. It rejects a second ID if that canonical identity is already owned, with the decision made inside the index transaction. It must never partially update the index or project state.
4. Workspace Preferences, Profile, and Handoff use a version-2 workspace-document envelope:
   - `schema_version: 2`, monotonic `revision`, aware `updated_at`, and `state: present | cleared` are always present;
   - `state: present` carries the existing typed payload; `state: cleared` carries no domain payload and is not an empty/placeholder Preferences, Profile, or Handoff;
   - legacy version-1 workspace documents without `state` are read as `present` and are upgraded only on the next successful mutation; reads never rewrite state;
   - config and workspace-index schemas remain independently versioned and are not changed merely by this envelope decision;
   - the load-result shape is fixed: `StateLoadStatus` remains exactly `ok | corrupt | unsupported_schema`; successful workspace-document loads add a separate `presence=missing | cleared | present` discriminator. A missing never-created file loads as `status=ok`, `presence=missing`, `value=None`, revision 0; a cleared primary loads as `status=ok`, `presence=cleared`, `value=None`, and its persisted revision; a present primary loads as `status=ok`, `presence=present`, its typed value, and revision. Read-only/degraded decisions are based on corrupt/unsupported status, never merely on missing/cleared presence;
   - clear of a present document publishes `state=cleared` at revision N+1 and backs up the previous valid primary; clearing missing/already-cleared state is idempotent and does not invent another revision;
   - recreation requires the cleared revision and publishes `state=present` at N+1, so a stale revision 0 writer conflicts.
5. Cleared-state command behavior is fixed: `/handoff` and `/status` report that no Handoff is available while retaining the cleared revision for concurrency; `/continue` refuses because there is no present Handoff; cleared Profile is treated as absent for explicit onboarding/edit creation using its persisted revision; cleared workspace Preferences act as an empty layer and the next patch uses the cleared revision.
6. Clear and ordinary writes must validate before publication, write and `fsync` a same-directory temporary file, atomically replace the primary, preserve one recoverable last-valid backup, and synchronize the containing directory where supported. A failure at any injection point must preserve a deterministic valid primary/backup state.

## Executable tasks

1. Add regression tests that reproduce sequential duplicate confirmation from one stale candidate, concurrent claims from separate processes, and relinking a second ID to a subdirectory of an already registered Git root.
2. Add positive identity tests for exact-path priority after `git init`, aliases/symlinks, non-Git registered parents, nested repositories, and distinct worktrees.
3. Refactor workspace-index mutation so claim/relink uniqueness is decided under the index lock, returns the authoritative identity, and never relies only on a pre-lock snapshot.
4. Add regression tests for write rev1 → write rev2 → clear rev3 → stale `expected_revision=0`, requiring the stale write to conflict and the next valid write to advance beyond rev3.
5. Before product-code changes, update the Stage 1 roadmap and `docs/ARCHITECTURE.md` with the version-2 envelope, compatibility rules, and cleared command behavior above.
6. Introduce and validate the cleared-state representation for Preferences, Profile, and Handoff. Ensure loads expose explicit presence plus `value=None` and the persisted revision while corrupt/future schemas remain distinguishable and byte-preserved.
7. Route clear through the normal atomic publisher, add parent-directory durability, and extend failure injection to temporary write, file `fsync`, replacement, directory `fsync`, and revision checks.
8. Review protocol/type annotations and recovery inspection so deletion, missing-never-created, backup, corrupt, and unsupported-schema states are not conflated.
9. Run targeted state/workspace tests, genuine multiprocess claim/writer-lock/relink tests, the complete non-Live suite, Ruff format/check, and compile checks.

## Verification

- Repeated and concurrent confirmation of one canonical target produce exactly one workspace entry and one authoritative ID.
- Relink cannot give two IDs the same effective Git root; failed relink preserves index bytes and all project state.
- Distinct worktrees and nested repositories retain the roadmap-defined identities.
- Every actual publication that changes present/cleared state advances a persisted monotonic revision. Idempotent clear of missing/already-cleared state performs no publication and returns the existing revision (0 for missing); stale revisions never recreate or overwrite cleared state.
- Clear remains recoverable through one valid backup and survives injected failures without a missing/partial primary.
- All workspace inspection stays metadata-only and produces no project writes or subprocess calls.

## Completion criteria

- All Subplan 12 regression and positive tests pass in isolated temporary roots, including genuine multiprocess cases.
- Existing Stage 1 identity and recovery tests remain green.
- The version-2 workspace envelope and cleared-state UX are documented in `docs/ARCHITECTURE.md` and the Stage 1 roadmap before implementation begins.

## Deliverables

- Transactionally unique workspace claim/relink behavior.
- Monotonic clear/recreate revisions and durable atomic deletion semantics.
- Regression evidence for the confirmed identity, revision, and durability defects.
