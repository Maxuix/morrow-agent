# Subplan 10 — Provider, Concurrency, and Recovery

> Stage: 1B  
> Status: pending  
> Parent: [Stage 1 implementation plan](../PLAN.md)

## Objective

Finish the Stage 1 local management surface and harden configuration/workspace state against concurrent writers, damaged files, future schemas, moves, and failed credential changes.

This is a Stage 1B hardening package. Validate Provider transactions, writer locking, schema/recovery UX, and relink independently as they are completed; do not defer all feedback until the end of the subplan.

## Prerequisites

- Subplan 09 is complete.

## Tasks

1. Complete `morrow provider add [--preset]`, `list`, `show`, `configure`, and `test` using the dynamic adapter registry. `provider add` must call Subplan 04's existing add/test/publish use case and activation policy; it must not implement a second credential/configuration transaction.
2. Complete `morrow model list [--provider]` and `model current`; do not add model switching, synchronization, or removal.
3. Keep `list`, `show`, `model list`, and `model current` offline under the network guard.
4. For reconfiguration, stage a new versioned credential reference, test it, then update Provider fields through Subplan 03's `GlobalConfigStore` whole-document operation while preserving global Preferences; only after publication make the old reference unreachable. Never expose a half-configuration.
5. Preserve the existing active model when adding another Provider unless no active model exists.
6. Hold a workspace single-writer lock for the REPL lifetime and fail a second writer with actionable occupancy information.
7. Use short independent transaction locks for global configuration and workspace index writes.
8. Enforce expected-revision conflicts after rereading within the lock.
9. Consume Subplan 03's typed load outcomes to implement explicit behavior for corrupt state and unknown future schemas: prevent unsafe normal startup or allow only the roadmap's object-level read-only downgrade; never reinterpret or overwrite incompatible data.
10. Implement `morrow workspace relink <workspace-id> --dir PATH` from an explicit ID or displayed candidate, with confirmation, target-occupancy checks, and one atomic index update.
11. Verify `.bak` recovery support without turning it into user-level `/undo`.
12. Add multiprocess/concurrency, credential-transition, corruption, schema-version, offline-command, and relink tests.

## Verification

- A second writer for the same workspace is rejected; different workspaces can run independently.
- Stale revisions never overwrite newer disk state.
- Damaged or future-schema documents remain byte-preserved after attempted mutations.
- A failed Provider configuration/test leaves the old active connection and credential reference usable.
- Local Provider/model inspection generates no network call.
- Relink changes only index path metadata while retaining the original workspace ID and state.
- Credential sentinels remain absent from all visible/persisted surfaces.

## Completion criteria

- `S1B-04` and `S1B-05` pass; the relink portion of `S1B-06` has evidence ready for Subplan 11 aggregation.
- Provider management stays within the Stage 1 command boundary.
- Concurrency and recovery behavior matches the architecture and roadmap contracts.

## Deliverables

- Stage 1 Provider/model local management commands.
- Single-writer and optimistic-concurrency enforcement.
- Schema/corruption handling and workspace relink flow.
