# Stage 4 Operational Store decision

## Status

Accepted by S4.35.2. Production adapter work remains gated on Subplan 36.

Executable evidence for the chosen SQLite settings lives in
`tests/test_stage4_operational_store_spike.py`. That file is a disposable design spike, not a
production store.

## Fixed reference

Semantic input only: Hermes WAL / `BEGIN IMMEDIATE` / bounded busy retry / online-backup
discipline, and the Stage 4 research notes. No upstream file is copied. `sqlite3` and the
already-approved `filelock` dependency are the only implementation tools.

## Why this store exists

Morrow already has durable YAML authorities for Profile, Preferences, Provider non-secret
configuration, workspace index, and a separate CredentialStore. Those authorities stay where they
are.

What is missing is one transactional place for Session, TaskRun, Turn, AgentRun, conversation
records, tool/approval journals, Artifact metadata, grants, targeted command receipts, and
sanitized application events. That store must survive a clean exit or crash, refuse a future or
corrupt file, and allow another workspace process to keep using the same data root.

This ADR locks the file layout, connection policy, durability settings, contention behavior,
maintenance lock, and backup primitive. It does not lock business table schemas.

## Physical layout

All paths are relative to the existing data root: `--state-root` when set, otherwise `~/.morrow`.
Workspace isolation is a stored `workspace_id` plus application queries. There is not one database
per repository.

```text
{data_root}/                         # existing root; new dirs below are POSIX 0700
  config.yaml                        # unchanged YAML authority
  workspace-index.yaml               # unchanged
  locks/
    {workspace_id}.lock              # existing WorkspaceWriterLock
    operational-store.lock           # global maintenance lock
  logs/                              # unchanged
  workspaces/{workspace_id}/         # unchanged Profile / Preferences YAML
  store/
    operational.sqlite               # POSIX 0600; WAL sidecar files may appear beside it
    operational.sqlite-wal
    operational.sqlite-shm
  artifacts/
    tmp/                             # reserved; publication rules are a later ADR
  backups/
    operational/                     # online-backup destinations only
```

Rules:

- SQLite is the only operational-record authority. Artifact bytes live under `artifacts/`; SQLite
  stores identity, integrity, provenance, retention, and bounded excerpts.
- Do not invent a second database, JSONL transcript authority, ORM, or service daemon.
- Do not copy live `operational.sqlite` together with `-wal`/`-shm` and call that a backup.
- New operational directories and the database file must be created with user-only permissions and
  then verified. Do not trust umask alone. When SQLite creates `-wal`/`-shm` sidecars, production
  must apply `0600` to those files as well.
- Existing YAML directories keep their current creation behavior until a later task explicitly
  tightens them. This ADR does not authorize changing YAML or CredentialStore writes.
- Production `DataRoot` gains `store_path`, `artifacts_path`, `backups_path`, and
  `operational_lock_path` in Subplan 36. Until then the paths above are reserved names.

## Identity and schema version

Every connection that may write must establish and then honor:

| Field | Authority | Value |
|---|---|---|
| `PRAGMA application_id` | SQLite header | `0x4D4F5257` (`MORW`) |
| `PRAGMA user_version` | SQLite header | integer schema version |
| `store_identity` row | first user table | `application_name = morrow-operational-store`, matching `schema_version` |

`user_version` and `store_identity.schema_version` must agree. A mismatch is `needs_repair`, not
an invitation to rewrite the header.

Open classification:

| Condition | Result | File fate |
|---|---|---|
| Missing database, create requested | create identity + schema 1 | new file |
| Missing database, create not requested | `not_found` | unchanged |
| Empty, non-SQLite, or integrity failure | `needs_repair` | original bytes kept |
| Wrong `application_id` or application name | `identity_mismatch` | original bytes kept |
| `user_version` or identity version greater than supported | `future_schema` | original bytes kept |
| Version less than supported, migrate authorized | migrate under the maintenance lock | original preserved until commit |
| Version equal and identity matches | `ok` | reused |

A future, corrupt, empty, or foreign file is never deleted, truncated, or recreated in place.
Doctor and backup may open a diagnose/read-only path; they do not repair business history.

`schema_migrations` (checksum, applied-at, name) belongs to Subplan 36. This ADR only requires
that migrations are ordered, preflighted, backed up, applied in one maintenance transaction
sequence, and refused when the file is newer than the running binary.

## Connection policy

Use stdlib `sqlite3` only.

On every accepted Morrow connection:

```text
isolation_level = None          # explicit transactions only
check_same_thread = True
PRAGMA foreign_keys = ON        # per connection; not persistent
PRAGMA busy_timeout = 250       # milliseconds; tests may inject 0
PRAGMA synchronous = FULL       # per connection
PRAGMA trusted_schema = OFF     # per connection
PRAGMA journal_mode = WAL       # persistent; still verify on open
```

Do not change `journal_mode` until application identity is accepted. A foreign or future SQLite
file must be left byte-for-byte intact, including its original journaling mode.

Additional rules:

- The connecting owner is the only closer. Connections are not shared across threads or
  processes.
- A process may keep one ordinary operational connection for a CLI/REPL lifetime. It must not
  keep a write transaction across a model call, approval wait, filesystem publication, subprocess,
  terminal read, or network call.
- Maintenance (initialize, migrate, backup, repair-mode transition) acquires the global
  maintenance lock first, then opens a dedicated connection.
- Adapters convert rows into typed Core objects at the boundary. `sqlite3.Row` and raw SQL do
  not leak upward.
- Public errors use stable codes (`busy`, `future_schema`, `identity_mismatch`, `needs_repair`,
  `not_found`, `unavailable`). They do not include credentials, SQL, or sensitive absolute paths.
- `PRAGMA integrity_check` runs at startup open, migrate, backup, and doctor—not on every short
  statement. The spike still checks on existing-file open because those databases are tiny.

## Transactions, WAL, and crash behavior

Writers start with `BEGIN IMMEDIATE` so lock contention fails at the beginning of the transaction,
not after a partial write. Readers may use the WAL snapshot; they must not upgrade a long-lived
read into a write.

Accepted crash facts, proven by the spike:

- A child that `COMMIT`s and then `_exit`s leaves the committed row visible to a new connection.
- A child that inserts and `_exit`s before `COMMIT` leaves no row.
- WAL sidecars are an implementation detail of the live database. Recovery is "reopen SQLite",
  not "merge copied WAL files".

`synchronous=FULL` is required so a returned `COMMIT` is durable enough for restart-safe
conversation and tool journals. `NORMAL` is rejected until a later ADR and a measured power-loss
argument say otherwise. Passive WAL checkpoints are optional maintenance, never part of every
write.

## Contention

SQLite busy handling is bounded and typed.

- Connection `busy_timeout` is 250 ms.
- Application retry surrounds only the short `BEGIN IMMEDIATE` + in-transaction statements.
- At most 8 attempts. Backoff uses an injected clock and RNG; tests must not wall-clock sleep.
- Retry only `SQLITE_BUSY` / `SQLITE_LOCKED`. Do not retry constraint, integrity, programming, or
  disk errors.
- Exhaustion returns `busy`. It does not spin, block a REPL forever, or drop the write.

`WorkspaceWriterLock` remains the per-workspace REPL/YAML single-writer lock. It cannot serialize
migration or backup of the shared database. Two different workspace processes may issue ordinary
operational writes; SQLite serializes them.

## Global maintenance lock

Path: `{data_root}/locks/operational-store.lock`.

Implementation: existing `filelock.FileLock` (POSIX advisory lock). Process death releases the
OS lock; a leftover `.lock` file is not an exclusive holder.

The lock is required for initialize, migrate, online backup, and repair-mode transitions. It is
not required for ordinary Session/Task/conversation writes.

- Two maintenance operations must not overlap.
- Backup may run while ordinary short writers continue.
- Migrate/init/repair may make ordinary writers see `busy` or `unavailable`; they must not rewrite
  the file out from under a live writer.
- Stale-owner recovery is "process exited, lock is free", not a PID-file reaper.

## Backup primitive

Use `sqlite3.Connection.backup()` into a new file under `backups/operational/`.

- Destination permissions are POSIX 0600.
- After copy, run `PRAGMA integrity_check` and `PRAGMA foreign_key_check` on the destination.
- The primitive backs up the operational database only. Artifact bytes and YAML/credentials are
  out of scope here; Subplan 43 adds the user-facing bundle and Artifact manifest.
- Credentials never enter this database, so they cannot enter this backup.
- A consistent backup taken during ordinary writes is acceptable. A torn copy of
  `operational.sqlite` + `-wal` + `-shm` is not.

## Open modes and health

Store-level health is independent of a Session's `active | archived | deleted` lifecycle.

| Open mode | Use |
|---|---|
| `create` | first initialize under the maintenance lock |
| `read_write` | ordinary business access after identity checks |
| `read_only` | doctor, inspect, or a non-writable filesystem |
| `diagnose` | integrity/export of a damaged file without migration |

| Store health | Meaning |
|---|---|
| `ok` | identity matches, integrity passed |
| `needs_repair` | corrupt, identity mismatch, or FK/integrity failure |
| `read_only` | usable for inspect/backup, not for business writes |
| `future_schema` | running binary is older than the file |

Session-level `needs_recovery` is an application classification of interrupted work. It is not a
reason to recreate the database.

## What this ADR rejects

- ORM, FTS5, embeddings, or a second database as a Stage 4 requirement.
- Background backup/migrate workers, event outbox workers, or run leases.
- Automatic repair, silent rebuild, or deleting a future/corrupt file to "help" the user.
- Moving Profile, Preferences, Provider config, or credentials into SQLite.
- Treating `WorkspaceWriterLock` as the operational-store maintenance lock.
- Holding a write transaction across model, approval, process, or network waits.

## Production follow-through

Subplan 36 implements `DataRoot` paths, the connection factory, the maintenance lock, schema
identity/migrations, typed storage errors, and the online-backup primitive. Later subplans add
business tables against this contract. They may not weaken durability, future-schema refusal, or
the YAML/CredentialStore boundary without a new ADR.
