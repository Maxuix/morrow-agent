# TODO

## Current stage

Stage 4 production implementation is active at the Operational Store foundation. No production
adapter or schema has landed yet; Full Access remains inactive.

## Active subplan

Subplan 36 — Operational Store and Migration Foundation.

## Tasks

- [>] S4.36.1 Add typed operational-store paths, open modes, health classifications, and sanitized
  storage errors without leaking absolute sensitive paths or raw SQL internals.
- [ ] S4.36.2 Implement fixed connection initialization on the event-loop owner thread,
  `check_same_thread=True`, required pragmas, transaction helpers, BUSY/LOCKED-only bounded retry,
  and deterministic rollback/close behavior.
- [ ] S4.36.3 Implement the validated global maintenance lock and prove two workspace processes
  cannot migrate or back up the shared store concurrently.
- [ ] S4.36.4 Implement schema v1 identity/version metadata and the reserved v1–v9 ordered migration
  registry with checksums, preflight, backup, transactional application, post-check, future-version
  refusal, and the Stage 3-to-v1 upgrade fixture.
- [ ] S4.36.5 Add identity/version mismatch, valid-header corruption, sidecar permission,
  read-only/disk/write failure, interrupted migration, concurrent writer, migration-versus-writer,
  and stale-lock-owner tests.
- [ ] S4.36.6 Implement the online SQLite backup primitive and integrity metadata without Artifacts
  or credentials.
- [ ] S4.36.7 Document the storage layout and run focused, quality, and Stage 3 regression gates.

Only Subplan 36 may be executed. Business schemas and behavior owned by Subplans 37–45 remain
inactive.
