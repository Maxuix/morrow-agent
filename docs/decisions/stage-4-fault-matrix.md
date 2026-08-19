# Stage 4 Contract and Fault Matrix

## Status

Accepted as the S4.35.8 planning matrix on 2026-08-19 after validation and user confirmation.
Subplan 36 is active; later owners remain gated by their ordered subplans.

Every crash-sensitive implementation subplan provides both logical fault injection and subprocess
`os._exit` evidence at its committed boundaries. Tests use injected Clock/RNG, barriers, pipes, and
bounded polling; wall-clock sleeps do not prove ordering.

## Definition-of-done coverage

| Stage 4 outcome | Primary owner | Required fault/evidence |
|---|---|---|
| create/list/resume/archive/fork isolated Session | 37, 42, 43 | duplicate commands, cross-workspace query, crash before/after lifecycle commit |
| one legal durable ConversationLog | 37, 38, 39 | every User/Assistant/Tool/terminal commit boundary; invalid snapshot quarantine |
| idempotent turn acceptance | 37, 39 | no row, open/interrupted receipt, closed receipt, conflicting payload; one User/Turn but resumed AgentRun allowed |
| intent before every side effect | 38 | side-effect spy at validation/begin/write/commit/handler boundaries |
| no blind replay | 39 | Host/sandbox missing completion = unknown; file/config evidence matrix |
| lifecycle/health/store-health separation | 36, 37, 39 | future/corrupt/read-only store and Session recovery/quarantine combinations |
| Task continuation/correction/acceptance | 40 | full state graph, stale versions, restart at transition boundaries |
| bounded verified Artifacts | 41 | temp/rename/fsync/metadata crashes, hash mismatch, missing bytes, reference-safe cleanup |
| deterministic checkpoint and fork | 42 | budget boundaries, open-cycle refusal, crash during checkpoint/fork, parent isolation |
| one application boundary and durable audit cursor | 43 | state/event same transaction, cursor pagination, runtime-event separation |
| doctor and backup/restore | 36, 43 | migration/backup concurrency, Artifact manifest races, read-only doctor |
| run-bound CapabilityGrant | 44 | expiry/revoke/crash/cross-scope; new recovery run has no inherited grant |
| Full Access Manual only | 44 | mandatory unconfined label/warning/approval; all auto paths unsupported |
| Stage 3/package regression | 45 | full offline suite, claimed-host sandbox gates, wheel install and isolated recovery |

## Store and migration matrix — Subplan 36

- fresh v1 identity store and idempotent reopen;
- reserved schema map with checksummed ordered migrations and no renumbering;
- migration backup, success, failed-step rollback, interrupted process, and post-check;
- mismatch among `application_id`, `user_version`, and identity row;
- valid header plus integrity failure; future, foreign, empty, and malformed inputs remain intact;
- `0600` database, WAL, SHM, maintenance lock/backup files and `0700` directories;
- `check_same_thread=True` enforcement and event-loop-thread ownership;
- retry only BUSY/LOCKED; constraint, integrity, programming, and disk errors are not retried;
- two workspace processes perform ordinary short writes; maintenance/write contention is typed;
- daily read-write open performs identity/quick checks, while full integrity runs on create/migrate/
  backup/doctor;
- online backup passes integrity/FK verification and never copies live WAL/SHM.

## Conversation matrix — Subplan 37

At User append, no-tool Assistant append, and Turn terminal commit:

```text
before validation
after validation / before BEGIN
inside transaction / before COMMIT
after COMMIT / before projection refresh
after projection refresh / before public event or Provider call
```

Required assertions:

- failed commit leaves no projection-only record;
- committed User/Turn precedes `turn.started` and Provider invocation;
- duplicate open/interrupted submit returns its receipt/recovery disposition;
- duplicate closed submit returns committed result with no new AgentRun;
- recovery resume creates a new AgentRun but no User/Turn duplicate;
- system boundary and `/new`/`/exit` copy match durable behavior;
- only bounded short scripted history is claimed before Subplan 42.

## Tool and approval matrix — Subplans 38–39

Fault points cover intent commit, approval create, approval resolve/consume, handler entry/return,
handler-completed commit, ToolMessage commit, and Turn terminal commit.

| Interrupted tool state | Expected Stage 4 v1 result |
|---|---|
| no committed intent | no attempt; original command may be accepted/recovered normally |
| committed read intent, handler not completed | linked safe retry if declaration permits |
| file/config executing without completion | reconcile before/expected-after/actual state |
| expected after observed | record proven side effect; close ToolCycle with recovery interruption envelope |
| before state observed | explicit linked retry may be offered |
| neither/evidence missing | outcome unknown |
| Host or native sandbox lacks completion | outcome unknown; never auto replay |
| promotion lacks completion | reconcile each file; never resume sandbox |
| handler completed, ToolMessage missing | preserve structured facts; recovery-close in order |

Approval tests cover stale intent/schema/permission digest, wrong granted subset, expiry at each
boundary, duplicate resolution, double consume, denial, cancellation, and injected Clock.

## Task and evidence matrix — Subplans 40–42

- every legal/illegal TaskRun state transition and one-current-task invariant;
- final Assistant produces non-terminal `ready_for_acceptance`; follow-up returns to open;
- only explicit acceptance/outcome snapshot/terminal close creates an Outcome version;
- Outcome sources are durable structured facts until Artifact links are added in the next migration;
- Artifact exact byte limits, multibyte accounting, redaction, traversal/link protection, and
  reference-safe orphan reporting;
- checkpoint complete-cycle cuts, provenance regeneration, missing Artifact fallback, and open
  approval/recovery preservation;
- fork at closed Turn/checkpoint only, immutable parent prefix, no inherited Session Preferences,
  approvals, grants, or workspace mutation.

## API, backup, and grant matrix — Subplans 43–44

- parser/interface adapters never contain SQL or lifecycle rules;
- application state and `application_events` commit/rollback together;
- runtime events retain current type/cardinality and are not persisted/replayed as audit events;
- doctor is read-only for every inconsistent domain that exists by Subplan 43; grant checks are
  added only in Subplan 44;
- backup copied database plus Artifact manifest makes missing concurrent Artifact visible;
- grant creation only through local interface command, with every other source rejected;
- crash-resumed AgentRun lacks prior grant and Full Access operation requires a new user grant;
- `unconfined_host` is persisted and warned for each elevated opaque command;
- revoke before/after approval consume/handler entry retains truthful completed or unknown facts;
- Controlled Auto/raw auto stay unavailable.

## Closeout evidence — Subplan 45

Acceptance evidence maps every table row above to exact test names and commands. It may not add a
new product capability to make a story pass. Intentional unsupported cases—especially Host/sandbox
automatic retry, grant inheritance, workspace rewind, event workers, and Full Access Auto—are
asserted as negative product tests.
