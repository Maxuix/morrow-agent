# Subplan 03 — Workspace Identity and State Storage

> Stage: 1A  
> Status: completed
> Parent: [Stage 1 implementation plan](../PLAN.md)

## Objective

Implement stable workspace identity and safe, human-readable local state without reading or modifying project content.

## Prerequisites

- Subplan 02 is complete.

## Tasks

1. Implement injectable resolution of the Morrow data root and its config, index, workspace, lock, and log locations.
2. Implement the exact workspace resolution algorithm: expand user, absolute path, symlink resolution, trailing-separator normalization, direct index reuse, in-process `.git` directory/gitfile search, nearest registered parent fallback, and confirmed random ID creation.
3. Use filesystem-native case behavior and `samefile` for existing-path alias equivalence; keep worktrees separate and exclude Git branches from identity.
4. When a new path misses the index, detect invalid old paths with similar display names and return them only as relink candidates; never inherit, create, or overwrite state from similarity alone. Return an uncommitted workspace candidate and do not generate/publish its ID or index entry until the interface confirms creation.
5. Define one versioned/revisioned global configuration document containing the roadmap-defined global Preferences, Provider collection, and `active_model`, plus separate schemas for workspace index, Profile, and Handoff. The global document has one schema version, revision, timestamp, and transaction lock; workspace/session preference merging remains out of scope until Subplan 08.
6. Put normalized uniqueness of `handoff.decisions[].decision` into the initial Handoff model. Duplicate normalized decision text is a schema validation failure before any 1A write.
7. Implement typed state outcomes: YAML loads distinguish `ok`, `corrupt`, and `unsupported_schema`; checked writes can additionally return `revision_conflict`. None may overwrite the source on failure, and Subplan 10 owns startup/read-only UX for non-`ok` load results.
8. Implement `GlobalConfigStore` as locked whole-document read-modify-validate-write operations that preserve fields owned by other services. Implement `WorkspaceIndexStore` separately, and require all `ProjectStateStore` operations to carry `workspace_id`.
9. Implement checked writes: lock the relevant transaction, reread revision, validate the new value, write a same-directory temporary file, flush/`fsync`, atomically replace, and retain one last-valid `.bak`.
10. Implement workspace creation confirmation data without fabricating skipped Profile or Handoff values.
11. Establish the `logs/` location, but do not emit unsanitized file diagnostics; the single sanitizing diagnostic boundary is connected in Subplan 06.
12. Add failure injection at validation, temporary write, `fsync`, replacement, and revision-check boundaries.
13. Add filesystem access sentinels proving only permitted path metadata and `.git` existence checks occur in the selected workspace.

## Verification

- Direct paths, symlink aliases, Git roots, nested paths, non-Git directories, later `git init`, and two worktrees resolve according to the documented algorithm.
- Two workspaces cannot observe each other's Profile or Handoff.
- No workspace ID is recalculated from a path after registration.
- A moved-workspace candidate is displayed as a candidate only; it is never silently selected or mutated.
- Rejecting new-workspace confirmation creates no ID, index entry, Profile, or Handoff.
- Every failed write leaves the previous document parseable and semantically unchanged, or byte-identical where required.
- Corrupt, future-schema, and revision-conflict outcomes remain distinguishable to application code and preserve source bytes.
- Interleaved global Preferences and Provider updates preserve each other's fields and advance one shared revision.
- A Handoff with duplicate normalized decision text is rejected before publication.
- An unavailable Morrow data root returns an actionable error and never falls back into the project directory.
- No subprocess is invoked and no project-content file is read.

## Completion criteria

- Workspace identity and state ownership match the Stage 1 roadmap exactly.
- Atomic publication and recovery primitives are reusable by all remaining Stage 1 state.
- All workspace/state unit and integration tests pass.

## Deliverables

- Workspace resolver and index service.
- Versioned global aggregate, index, Profile, and Handoff models.
- Separate global-config, workspace-index, and project-state adapters/ports over the shared safe-write primitive.
- YAML state adapter with safe-write and backup behavior.

## Deferred behavior

- Interactive relink, application UX for unsupported/corrupt state, and the full same-workspace REPL lifetime lock are completed in Subplan 10; the underlying result types, index transaction, and lock primitives must already support them.
