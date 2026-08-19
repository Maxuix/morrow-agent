# Subplan 43 — Command/Query/Event, CLI, Doctor, and Backup

> Status: pending
> Prerequisite: Subplan 42 accepted
> Owns: one client boundary and daily-operability surfaces
> Hold point: any change to the existing public event lifecycle requires explicit authorization

## Objective

Expose durable Stage 4 behavior through one application API shared by CLI/REPL and future clients,
with replayable sanitized events, read-only diagnosis, explicit recovery decisions, and verified
backup—without adding a worker or second business implementation.

## In scope

- Typed Command and Query application services over existing narrow ports.
- Retry-sensitive command receipts, optimistic versions, workspace authorization, and stable errors.
- Sanitized versioned `application_events` appended in the same business transaction and queried by
  monotonic cursor.
- CLI/REPL Session, Task, Artifact, status, recovery, fork, archive, and acceptance flows.
- Read-only state doctor: schema/integrity/foreign-key/history/Artifact/reference/permission checks,
  health classification, and bounded report/export.
- Online backup command combining SQLite backup, Artifact manifest/copy, hashes, and restore
  verification in a separate target.
- Quarantine and explicit deterministic orphan-cleanup command only where the accepted ADR permits.

## Out of scope

- Event delivery outbox, acknowledgements, worker, push subscription, scheduler, or background work.
- Direct SQL/filesystem access from UI code.
- Automatic repair/rewrite of messages, ToolExecutions, approvals, outcomes, or grants.
- In-flight `agent.steer`, full GUI, remote API server, or network transport.
- Physical secure delete/export completeness promised by Stage 10.

## Tasks

- [ ] S4.43.1 Define stable Command/Query DTOs, error mapping, command receipts, cursor pagination,
  and workspace isolation for the completed Stage 4 domains.
- [ ] S4.43.2 Implement application events in the same business transaction with schema version,
  sanitized bounded payload, ordering, and cursor replay.
- [ ] S4.43.3 Reach the public-event hold point: prove whether existing runtime events can remain
  unchanged; request explicit authorization before changing lifecycle/cardinality/payload.
- [ ] S4.43.4 Implement CLI/REPL create/list/resume/status/archive/fork, Task show/new/accept/cancel,
  Artifact list/show, and recovery show/resolve through the same services.
- [ ] S4.43.5 Implement read-only doctor and health/quarantine reporting for database, conversation,
  execution, Artifact, and grant invariants.
- [ ] S4.43.6 Implement online backup plus Artifact manifest/copy and isolated restore verification;
  guarantee credentials are excluded.
- [ ] S4.43.7 Add deterministic orphan-cleanup only for proven unreferenced managed temp/orphan files,
  with inspected targets and dry-run; never repair business history.
- [ ] S4.43.8 Run application/CLI/crash/backup/security regressions and update user/architecture docs.

## Minimum application surface

Commands include Session create/resume/archive/fork, Task new/accept/cancel/resume, turn submit,
approval/recovery resolve, Artifact pin/release, and health quarantine acknowledgement as applicable.
Queries include workspace current, Session/Task/Run/Artifact/recovery get/list, outcome versions,
doctor report, and application-event cursor listing.

Names may be adjusted for a coherent CLI, but every entry point must delegate to the same application
service and return the same domain errors.

## Locked contracts

- Business state and its application event commit together. Cursor replay is observation, not an
  asynchronous reliability protocol.
- Existing public runtime event behavior remains unchanged unless the hold point is explicitly
  authorized and all consumers/tests update atomically.
- Doctor never edits operational history. Quarantine is a health state and does not archive/delete.
- Backup is created at an explicit validated target, includes a manifest and integrity proof, and
  does not read/copy CredentialStore secrets.
- Destructive cleanup enumerates and validates exact managed targets and never follows links.

## Tests and faults

- duplicate/conflicting commands, stale row versions, pagination/cursor boundaries, and
  cross-workspace access;
- transaction rollback proves no state-without-event or event-without-state;
- unknown event version/field tolerance and payload redaction/budgets;
- CLI/REPL parity and stable exit/error behavior;
- doctor on healthy/future/corrupt/inconsistent/missing Artifact states remains read-only;
- backup during bounded writes, crash at each backup phase, missing Artifact, changed source, and
  restored fixture end-to-end verification;
- cleanup dry-run, exact target count/type/link checks, and referenced-item refusal.

## Completion gate

A user can operate and diagnose all completed Stage 4 features after restart through CLI/REPL, future
clients have one typed boundary, application events replay in order without a worker, and a verified
backup can restore an isolated fixture without credentials or hidden repairs.

## Deliverables

- Command/Query/Event application boundary and client DTOs.
- Stage 4 CLI/REPL flows.
- Read-only doctor, quarantine UX, online backup, and restore verification.

