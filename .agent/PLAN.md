# Stage 4 Durable Task, Session, Artifact, and Recovery Plan

> Status: active — production implementation
> Active subplan: 36 — Operational Store and migration foundation
> Production implementation: authorized; no production adapter has landed at activation
> Stage 3 production baseline: `003dbdaab652520ca5cadf451ebca7a13bcba36d`
> Stage 4 accepted contract baseline: `20fb43e`
> Scope: durable foreground-agent operation, recovery, artifacts, context checkpoints,
> conversation fork, auditable grants, and Full Access Manual

## Objective

Turn the current process-local Code Agent into a durable personal agent whose foreground work can
survive a clean exit or crash without inventing history, replaying unknown side effects, or silently
restoring authority.

The Stage 4 product loop is:

```text
open workspace
→ create or resume a Session
→ continue one foreground TaskRun across Turns
→ freeze one AgentRun's non-secret configuration and permissions
→ persist messages, approvals, and tool intent before side effects
→ close or reconcile each tool execution
→ create bounded, traceable Artifacts and TaskOutcome versions
→ exit or crash
→ reopen in a safe health mode
→ resume, reconcile, fork, archive, or accept through one application boundary
```

This is a durable local product, not a demo schema and not a distributed enterprise scheduler.
It must be strong enough for daily use, migration, backup, diagnosis, and crash recovery, while
deliberately avoiding background orchestration, multi-device coordination, and speculative
infrastructure.

## Authority and source policy

Precedence for this plan is:

1. The current user request and later explicit decisions.
2. Current code and validation just run.
3. This plan and its one active subplan.
4. Accepted Stage 4 ADRs under `docs/decisions/`.
5. `docs/roadmap/stage-4-task-session-and-persistence.md`.
6. `docs/reviews/stage-4-plan-review.md` as the accepted remediation input.
7. The three Stage 4 research documents under `docs/research/` as superseded decision input, not competing
   implementation specifications.

The accepted reference decision is semantic/failure-mode adoption with zero direct upstream code,
schema, fixture, or asset reuse. The local Hermes snapshot without Git provenance is research
evidence only. Any future direct reuse is a new hold point requiring repository/commit/path pinning,
license review, adoption log, and notices when distribution actually requires them.

Accepted Stage 4 decisions:

- `docs/decisions/stage-4-operational-store.md`;
- `docs/decisions/stage-4-domain-and-conversation.md`;
- `docs/decisions/stage-4-durable-execution-and-recovery.md`;
- `docs/decisions/stage-4-artifact-context-and-fork.md`;
- `docs/decisions/stage-4-permission-grants.md`;
- `docs/decisions/stage-4-reference-adoption.md`;
- `docs/decisions/stage-4-fault-matrix.md`.

## Current baseline

At the baseline commit:

- `Session` and its `ConversationLog` are process-local; restart loses the conversation.
- `ConversationLog` is the only legal message grammar and append authority. `AgentLoop.run_task()`
  owns ordinary chat writes; retained `run_turn()` delegates to that same loop and production
  composition may supply its frozen `ToolExecutor`.
- Stage 3 provides workspace-confined read/search/mutation, approval-gated non-isolated Host
  commands, native macOS sandbox execution, current-run sandbox promotion, and read-only Git.
- `WorkspaceWriterLock` is workspace-scoped and cannot serialize migration of one shared global
  operational database across different workspace processes.
- Profile and Preferences use revisioned YAML; Provider secrets remain in `CredentialStore` or the
  environment. These authorities must not migrate into the operational database.
- `ToolEffect` is a current-run policy hint with only `none`, `session_write`, and
  `persistent_write`; it is not sufficient to decide crash replay, and current Host command intent
  is incorrectly marked `NONE` despite possible external effects.
- Conversation positions, public-event sequence, client-message receipts, AgentRun identity,
  process identity, expected-after write evidence, durable approvals, and recovery records do not
  exist. Tool facts, ChangeSets, sandbox snapshots, and metrics are process-local.
- `turn.started` currently precedes `ConversationLog.begin_turn()`; `/new` resets memory and
  `/exit` speaks about discarding unsaved history. These are Stage 37 migration obligations, not
  durable behavior already present.
- the public event lifecycle and bundled policy defaults remain unchanged until their specific
  Stage 4 hold points are authorized.

## Locked route

### Persistence shape

- Use one data-root SQLite operational database through Python's standard `sqlite3`, plus a
  data-root filesystem Artifact Store. Accepted layout and SQLite settings:
  [`docs/decisions/stage-4-operational-store.md`](../docs/decisions/stage-4-operational-store.md).
- Keep Profile, Preferences, Provider non-secret configuration, credential references, and
  credentials in their current authorities. An AgentRun stores a bounded resolved non-secret
  snapshot plus source revisions/hashes so historical evidence survives later YAML overwrites.
- Exact reserved paths under the existing data root (`--state-root` or `~/.morrow`):
  `store/operational.sqlite`, `artifacts/` + `artifacts/tmp/`, `backups/operational/`, and
  `locks/operational-store.lock`. Workspace isolation is a stored `workspace_id`, not one database
  per repository.
- Connection factory: stdlib `sqlite3`, `isolation_level=None`, `BEGIN IMMEDIATE` writes,
  `foreign_keys=ON`, `journal_mode=WAL`, `synchronous=FULL`, `busy_timeout=250ms`,
  `trusted_schema=OFF`, `application_id=0x4D4F5257`, matching `user_version` and `store_identity`.
- Use explicit schema metadata, forward-version refusal, foreign keys, crash-tested journaling and
  synchronous settings, short transactions, at most 8 injected-backoff busy retries, and a global
  operational-store maintenance lock for initialization, migration, backup, and diagnose/quarantine
  transitions. Ordinary writes do not take that lock; `WorkspaceWriterLock` remains YAML/REPL only.
- SQLite is the authority for operational records. Artifact bytes live in the Artifact Store;
  SQLite stores identity, integrity, provenance, retention, and bounded excerpts.
- Online backup uses `Connection.backup()` into `backups/operational/` and never copies live
  `-wal`/`-shm` files. Empty, foreign, corrupt, or future files are refused and left intact.
- The S4.35.2 spike proves the exercised WAL/transaction/contention/maintenance-lock/backup cases,
  not migration, WAL/SHM modes, thread affinity, or every error class. Those are explicit Subplan 36
  gates. Schema versions v1–v9 are reserved by owning subplan in the Operational Store ADR.
- Do not introduce an ORM, service daemon, FTS5, distributed lease, background worker, or second
  database unless measured evidence and a separately approved ADR require one.

### Application and storage boundaries

Do not build one `OperationalStore` god interface. Define narrow Core ports implemented by one
SQLite adapter and shared transaction helpers, initially along these responsibilities:

- session/task/run lifecycle;
- conversation journal;
- tool execution and approval journal;
- Artifact metadata;
- grant and permission snapshots;
- command receipt/idempotency;
- query and application-event projection.

Application services own commands and transactions. CLI, REPL, AgentLoop, recovery, and future GUI
clients call those services; they do not issue SQL or duplicate transition rules.

### Conversation ownership

- `ConversationLog` remains the sole message-order and ToolCycle grammar authority.
- `AgentLoop` requests durable ConversationLog appends for ordinary chat. `TaskService` coordinates
  TaskRun and command idempotency but never direct-writes chat messages.
- The commit protocol is candidate construction → ConversationLog validation → one
  `BEGIN IMMEDIATE` transaction for records/companion rows → COMMIT → projection refresh from
  committed rows. Failure before COMMIT leaves the projection unchanged.
- Recovery may call only a narrow ConversationLog API that appends ordered interrupted/error
  ToolMessages and a truthful non-success terminal for an already-recorded ToolCycle. It may not
  append User/Assistant or synthesize a successful tool result.
- `conversation_position` (per Session), `runtime_event_sequence` (per Turn stream), and
  `application_event_cursor` (durable audit stream) are independent namespaces.
- Subplan 37 commits Turn + UserMessage before emitting the existing `turn.started` event and before
  invoking the Provider. Event type/payload/cardinality remain unchanged.

### Session, TaskRun, Turn, and AgentRun semantics

Store health, Session lifecycle, and Session health are independent:

```text
store_health:      ok | read_only | needs_repair | future_schema
session_lifecycle: active | archived | deleted
session_health:    ok | needs_recovery | quarantined | read_only
```

`deleted` is a tombstone. Quarantine changes Session health, never rewrites lifecycle.

Locked foreground semantics:

- The first ordinary input in a Session creates an `open` TaskRun if none is current.
- Subplan 37 persists only the open current-task pointer. Subplan 40 owns the complete state machine:
  `open → ready_for_acceptance → accepted`, with ordinary follow-up returning
  `ready_for_acceptance → open`; cancel/fail/abandon are explicit truthful exits.
- A final assistant answer closes its Turn and, only after Subplan 40, moves TaskRun to the
  non-terminal `ready_for_acceptance`. It is neither acceptance nor a Stage 5 learning trigger.
- `/accept` records explicit acceptance; `/task new` creates a new TaskRun; `/new` creates a new
  Session.
- A crash resume with no new user input creates a new AgentRun with `resume_of_agent_run_id` inside
  the same open Turn and appends no UserMessage. New user input creates a new Turn only after
  blocking recovery is resolved/closed.
- Failed, cancelled, or interrupted records retain known side effects and never pretend to roll
  them back.
- `/new` creates/selects a new Session without resetting, deleting, or auto-archiving the old one;
  session-scoped Preferences do not carry. `/exit` never asks to discard persisted history and
  leaves unproved work as `needs_recovery`.

The accepted domain/conversation ADR owns the exact transitions, receipt table, commit protocol,
sequence namespaces, and `/new`/`/exit` semantics.

### Tool execution journal and recovery

Every durable tool execution records enough information to distinguish handler completion from
conversation closure. The minimum protocol is:

```text
intent persisted
→ approval pending/resolved when required
→ executing
→ handler_completed with bounded redacted result evidence
→ ToolMessage persisted and ToolCycle closed
```

An independent durable `EffectClass`/recovery policy is required; the current `ToolEffect` remains
a runtime permission hint. Recovery classification must support at least:

```text
never_started | safe_to_retry | requires_reconciliation | outcome_unknown | completed
```

Rules:

- read-only operations may be retried only when their declared contract permits it;
- a structured idempotent operation may use its idempotency key and persisted result;
- file mutation reconciliation uses captured before hash, expected after hash, expected size, and
  required parent/auxiliary conditions rather than volatile mtime-based revision equality;
- every Host process is externally effectful; without `handler_completed` it is
  `outcome_unknown` and never automatically replayed;
- native sandbox execution also becomes `outcome_unknown` without `handler_completed` in Stage 4
  v1 because PID/PGID, temp-root ownership, and snapshot evidence are not durable;
- sandbox promotion is reconciled per file from pre-effect hashes and is never retried as a sandbox;
- all side-effecting handlers are forbidden until their intent transaction commits.

Recovery is classification and user-guided reconciliation, not automatic rewriting of business
history.

### Approval and idempotency

- `client_message_id` is a turn-submit command field, not UserMessage content, and is unique in its
  Session. It guarantees one Turn/User acceptance, not one model invocation forever.
- A duplicate open/interrupted submission returns its receipt plus recovery disposition; explicit
  recovery may create another AgentRun in the same Turn. A duplicate closed submission returns the
  committed result with no new run. Same key with a different canonical request is a conflict.
- Use command receipts only for retry-sensitive mutation commands such as turn submission,
  approval resolution, recovery resolution, grant creation/revocation, and Session/Task lifecycle
  mutation. Do not burden every read or local helper with generic exactly-once machinery.
- A durable Approval initially contains an opaque `approval_id`, intent hash, tool-schema digest,
  effective Stage 3 permission-context digest, requested and granted scope, optimistic
  `row_version`, expiry, resolution, and `consumed_at`; Subplan 44 adds the full PermissionSnapshot
  link.
- Resolve and consume an approval atomically with the transition to executing. The initial local
  design does not need a separate approval nonce.

### Artifact and payload safety

- Artifact paths are addressed by opaque Artifact ID and verified by content hash; content-addressed
  deduplication is not a Stage 4 requirement.
- Publication is temp write → file fsync → atomic rename → parent fsync → metadata commit, with
  deterministic orphan handling.
- The first durable command-output Artifact contains only bounded, redacted content. Persisting a
  complete/raw stream is forbidden until a streaming redactor and its fault tests prove the same
  secret boundary as current terminal/tool output.
- Tool arguments, results, errors, events, and snapshots each have explicit byte/record limits.
  Provider reasoning, SDK objects, credentials, unfiltered environments, full tracebacks, and
  unbounded command output never become durable payloads.
- Initial row ceilings and Artifact limits are locked in the execution and Artifact ADRs. Existing
  tighter Stage 3 limits continue to win; storage limits do not enlarge model/tool policy.

### Context checkpoints and fork

- Deterministic compaction is required before any optional LLM summary path.
- A checkpoint references immutable source record IDs/ranges, the compaction method/version, and
  Artifact references. It does not duplicate a second `retained_tail_json` conversation authority.
- Raw conversation records remain available under retention rules; summaries are projections, not
  new project facts.
- Stage 4 supports conversation/session fork with parent provenance. It does not restore project
  files, run arbitrary code rewind, or delete user changes.

### Events, diagnostics, and backup

- Beginning in Subplan 43, business mutations append a sanitized, versioned `application_events`
  row in the same SQLite transaction. Cursor replay is sufficient for Stage 4.
- Do not add a delivery outbox, acknowledgement state, event worker, or background queue; those
  belong with Stage 9 reliability needs.
- Changing the existing public event lifecycle is an explicit Subplan 43 hold point. Until approved,
  existing `turn.started`, `tool.status`, and `turn.completed` behavior stays intact.
- Public runtime events remain an ephemeral UI stream with `runtime_event_sequence` and token
  deltas. Durable `application_events` contain sanitized lifecycle/audit facts only, use
  `application_event_cursor`, and never reconstruct ConversationLog or share a consumer contract.
- State doctor is read-only. It may diagnose, export, quarantine, and identify deterministic orphan
  cleanup candidates; it does not invent or repair conversation/tool history.
- Backup uses SQLite's online backup mechanism plus an integrity-checked Artifact manifest and
  never reads credentials into the bundle.

### Permission grants and Full Access

- Only an explicit trusted local interface command can create or elevate a `CapabilityGrant`;
  Morrow has no local authentication subsystem. Model output, tools,
  Profile, Preferences, Memory, Skill, project files, and restored history cannot do so.
- Grants are workspace/task/run bound, time bounded, revocable, and frozen into an immutable
  AgentRun `PermissionSnapshot`. Unprovable or expired grants fail closed after restart.
- Crash resume creates a new AgentRun and PermissionSnapshot and never inherits the old run-bound
  grant; the user must explicitly grant again.
- Stage 4 delivers the grant substrate and **Full Access Manual** only. Its sole elevated capability
  family is an explicitly approved `unconfined_host_process`; it does not add general outside-file,
  browser, MCP, Git-write, or network-specific tools.
- Opaque approved Host commands are not OS-isolated. The product must label this
  `unconfined_host`, explain that the process may reach user files, network, credentials, and Morrow
  state, and never imply that string classification is confinement.
- Structured direct tools continue to enforce their protected-resource rules. Those rules cannot
  honestly guarantee protection against an approved unconfined shell, so the warning and approval
  boundary are part of the contract.
- Controlled Full Access Auto is deferred until Morrow has useful structured elevated capabilities
  whose effects can be enforced without an opaque shell. Raw arbitrary-host auto is outside Stage 4.

## Ordered subplans

Only one subplan may be active. A later subplan may refine an earlier internal design through an ADR,
but must preserve accepted public and safety contracts or explicitly reopen the affected gate.

| Order | Subplan | Status | Exit result |
|---|---|---|---|
| 35 | Contract activation and design spikes | completed | ADRs, source lock, fault matrix, executable contracts |
| 36 | Operational Store and migration foundation | active | safe SQLite foundation, schema/migration/backup primitives |
| 37 | Durable Session/Task/Turn/AgentRun and no-tool conversation | pending | restart-safe multi-turn Session without tools |
| 38 | Tool execution journal and durable Approval | pending | intent-before-effect and closed ToolCycle protocol |
| 39 | Recovery, reconciliation, and crash harness | pending | no blind replay; classified interrupted real tools |
| 40 | TaskRun lifecycle and versioned TaskOutcome | pending | continuation/correction/acceptance semantics and evidence |
| 41 | Artifact Store and durable payload budgets | pending | integrity-checked bounded artifacts and missing-file behavior |
| 42 | Context checkpoint and conversation fork | pending | budgeted context with immutable provenance and isolated fork |
| 43 | Command/Query/Event, CLI, doctor, and backup | pending | one application API and operable recovery UX |
| 44 | CapabilityGrant and Full Access Manual | pending | auditable run-bound manual elevation; no Full Access Auto |
| 45 | End-to-end acceptance and Stage 4 closeout | pending | crash-tested packaged durable personal agent |

Detailed executable tasks and gates are in `.agent/subplans/36-*.md` through
`.agent/subplans/45-*.md`. Completed Subplan 35 is preserved in Git history at `20fb43e`.

## Dependency route

```text
35 contracts
  → 36 SQLite/migrations
  → 37 durable no-tool history
  → 38 tool/approval journal
  → 39 recovery and crash proof
  → 40 TaskOutcome
  → 41 artifacts
  → 42 checkpoints/fork
  → 43 API/CLI/doctor/backup
  → 44 grants/Full Access Manual
  → 45 acceptance/closeout
```

The route is intentionally serial because every later layer depends on the durability and recovery
invariants established before it. Focused test implementation may proceed inside one active subplan,
but production work from a later subplan must not leak forward.

## Cross-cutting implementation rules

- No new third-party dependency without explicit user approval.
- All identifiers, timestamps, versions, and serialized payloads are typed and validated at Core
  boundaries; persistence models do not leak `sqlite3.Row` or SDK objects upward.
- Time-dependent tests use injected clocks. Concurrency and crash tests use barriers, subprocesses,
  pipes, fault points, and bounded polling—not timing assertions or wall-clock sleeps.
- Crash tests include both logical exceptions and subprocess `os._exit` at committed fault points.
- SQL migrations and destructive maintenance always operate on validated, backed-up targets and
  refuse future/corrupt formats.
- No secret, reasoning, complete tool argument/result, traceback, or unredacted process stream in
  SQL, artifacts, events, YAML, terminal output, or tests.
- Stage 3 security and tool behavior remain regression gates throughout Stage 4.
- Public event changes and bundled `agent-policy.toml` default changes remain explicit hold points.

## Validation policy

Each subplan must run its focused tests plus:

```bash
uv run ruff format --check .
uv run ruff check .
uv run python -m compileall -q src tests
git diff --check
```

Subplan 45 additionally runs:

```bash
uv run pytest -m 'not live'
uv run morrow --help
```

It also runs the relevant real current-host security gates and a built-wheel smoke test. Live
Provider/network tests are excluded unless separately requested with compatible credentials.

## Stage 4 definition of done

Stage 4 is complete only when all of the following are evidenced:

1. A user can create, list, resume, archive, and fork a workspace-isolated Session after restart.
2. Persisted messages restore in exactly one legal ConversationLog order with complete ToolCycles.
3. Turn submission is idempotent by `client_message_id` and produces exactly one accepted Turn/
   UserMessage. An interrupted Turn may create a linked new AgentRun only through recovery.
4. Every side-effecting tool intent commits before handler execution.
5. Interrupted reads and structured writes are classified from persisted evidence; Host and native
   sandbox executions without committed completion are unknown and never automatically replayed.
6. Store health, Session lifecycle, and Session health/quarantine remain independent; corrupt or
   future storage is never silently replaced.
7. A foreground TaskRun preserves continuation, correction, acceptance, cancellation, and versioned
   deterministic TaskOutcome evidence across restarts.
8. Artifacts are bounded, redacted, hash-verified, provenance-linked, and fail visibly when missing
   or corrupt.
9. Context compaction preserves complete cycle boundaries and immutable source provenance; fork does
   not mutate its parent or rewind project files.
10. One Command/Query boundary powers CLI/REPL and future clients; application events are ordered and
    replayable without introducing a background outbox.
11. Online backup, read-only doctor, recovery decisions, migration failure, lock contention, disk/
    filesystem failure, and Artifact orphan cases have deterministic tests.
12. CapabilityGrant can be explicitly created, queried, frozen, expired, and revoked for one
    foreground AgentRun; a crash-created AgentRun never inherits it.
13. Full Access Manual clearly exposes unconfined Host risk and keeps every elevated effect under
    explicit approval. Controlled Full Access Auto remains unavailable.
14. Stage 3 product stories and security gates still pass, and the installed package can operate on
    and recover an isolated Stage 4 fixture.

## Explicitly deferred

- long-term Preference/Knowledge learning and automatic memory promotion;
- FTS5, embeddings, semantic retrieval, and vector databases;
- LLM-generated summaries as a completion requirement;
- background execution, queue workers, event delivery outbox, schedules, and notifications;
- multi-agent workflows, in-flight steering, run leases/claims, and distributed coordination;
- automatic history/state repair, business-record rewriting, and silent database reconstruction;
- workspace/code rewind or restoration of user files;
- full/raw command-output retention without a proven streaming redactor;
- Controlled Full Access Auto and arbitrary unapproved Host execution;
- complete export/delete product lifecycle, multi-device sync, team sharing, and GUI.

## Active gate

Subplan 35 was accepted on 2026-08-19 after the review remediation and planning/spike gates passed.
Subplan 36 is now the only active work and may implement only the v1 Operational Store foundation
defined by its subplan and ADR. Conversation/business schemas v2–v9, public-event changes, Full
Access, and all later Stage 4 runtime behavior remain inactive until their own subplans and hold
points are explicitly activated.
