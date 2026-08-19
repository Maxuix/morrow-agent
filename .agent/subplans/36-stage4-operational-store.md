# Subplan 36 — Operational Store and Migration Foundation

> Status: pending
> Prerequisite: Subplan 35 accepted
> Owns: SQLite foundation, schema lifecycle, shared transaction primitives, maintenance/backup base
> Contract: `docs/decisions/stage-4-operational-store.md`

## Objective

Create a production-safe local operational-store foundation that can be opened concurrently by
different workspace processes, migrated deliberately, backed up online, and refused safely when it
is future-versioned, corrupt, or not writable.

## In scope

- Core storage errors and narrow connection/transaction interfaces.
- Data-root path resolution and restrictive file/directory creation.
- SQLite connection setup fixed by the accepted ADR.
- Explicit schema/application identity and ordered forward migrations.
- Global operational-store maintenance lock distinct from `WorkspaceWriterLock`.
- Short read/write transactions, bounded busy handling, deterministic rollback, and test faults.
- Startup integrity/future-version classification and read-only/quarantine modes.
- Online database backup primitive and manifest metadata needed by Subplan 43.

## Out of scope

- Conversation, tool, Artifact, grant, or TaskOutcome production tables beyond minimal schema/
  migration metadata.
- Automatic repair or deletion of a damaged database.
- ORM, daemon, run claim/lease, event worker, or distributed coordination.
- User-facing CLI beyond test harnesses.

## Tasks

- [ ] S4.36.1 Add typed operational-store paths, open modes, health classifications, and sanitized
  storage errors without leaking absolute sensitive paths or raw SQL internals.
- [ ] S4.36.2 Implement fixed connection initialization, foreign-key enforcement, transaction
  helpers, bounded lock retry, and rollback/close behavior.
- [ ] S4.36.3 Implement the validated global maintenance lock and prove two workspace processes
  cannot migrate or back up the shared store concurrently.
- [ ] S4.36.4 Implement schema identity/version metadata and ordered migrations with preflight,
  backup, transactional application, post-check, and future-version refusal.
- [ ] S4.36.5 Add corruption, read-only filesystem, disk/write failure, interrupted migration, and
  stale-lock-owner tests with deterministic fault injection.
- [ ] S4.36.6 Implement the online SQLite backup primitive and integrity metadata without artifacts
  or credentials.
- [ ] S4.36.7 Document the storage layout and run focused, quality, and Stage 3 regression gates.

## Locked contracts

- There is one operational database under the configured data root; workspace isolation is enforced
  by stored workspace identity and application queries, not one database per repository.
- Every connection enables required safety pragmas and validates schema identity before business
  access.
- Writers do not hold a database transaction across model calls, approvals, filesystem operations,
  subprocess execution, terminal input, or network calls.
- Contention has a bounded typed result; it never spins forever or silently drops a write.
- A future or corrupt schema is never recreated over the original. Diagnosis/backup may remain
  available through an explicit safe mode.

## Tests and faults

- fresh create, reopen, and idempotent initialization;
- ordered multi-version migration and failed-step rollback;
- future schema/application mismatch refusal;
- two connections and two subprocesses contending for write and maintenance locks;
- abrupt subprocess exit before/after migration commit;
- read-only root, short write/disk failure simulation, malformed header, and integrity failure;
- online backup while ordinary bounded writes occur, followed by restore/integrity verification.

## Completion gate

A temporary installation can create, reopen, contend on, migrate, classify, and online-back up the
store deterministically. Failed/future/corrupt inputs remain intact and fail closed. No Stage 3
runtime behavior or existing YAML/CredentialStore authority changes.

## Deliverables

- Shared SQLite adapter foundation and migration registry.
- Global maintenance lock and health/open-mode model.
- Online backup primitive.
- Focused durability/concurrency tests and accepted storage documentation.

