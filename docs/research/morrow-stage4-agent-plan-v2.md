# Stage 4 — Task, Session, Artifact and Persistence

> Status: active; opened by the user on 2026-08-19  
> Active subplan: 35 — contracts, ADRs and reference lock  
> Code baseline: replace with `git rev-parse HEAD` when committed  
> Detailed design: `docs/implementation/stage-4-complete-plan.md`  
> Reference review: `docs/research/stage-4-mature-agent-reference-review.md`

## Objective

Upgrade Morrow from a process-local Code Agent to a durable runtime:

```text
create/resume Session and TaskRun
→ accept an idempotent User Turn
→ create a frozen AgentRun
→ persist legal conversation/tool intent/approval
→ execute side effects only after durable gates
→ persist result and close ToolCycle
→ complete, pause, fail, cancel or interrupt
→ restart
→ verify, reconcile, resume or ask the user
→ produce versioned TaskOutcome
```

Stage 4 records **what happened**. Stage 5 decides **what deserves long-term learning**.

## Scope

Included:

- Session, TaskRun, Turn, AgentRun and ToolCycle persistence.
- Durable ConversationLog and ToolExecution journal.
- Approval, CapabilityGrant and immutable PermissionSnapshot.
- Recovery classification, reconciliation and crash testing.
- Artifact Store, ContextCheckpoint, managed WorkspaceCheckpoint and Fork.
- Versioned TaskOutcome.
- Typed Command / Query / Event boundaries.
- Backup, migration, doctor/repair and controlled Full Access.

Excluded:

- automatic long-term learning or Skill creation;
- Multi-Agent Workflow and background/scheduled work;
- vector database as a default dependency;
- full GUI or multi-device sync;
- persistent global Full Access or raw auto Shell;
- arbitrary user Hook execution in the Stage 4 core.

## Authority

1. Current user decisions.
2. Current code and validation just run.
3. This active plan and its one active subplan.
4. `docs/roadmap/stage-4-task-session-and-persistence.md`.
5. `docs/ARCHITECTURE.md` and accepted ADRs.
6. Detailed plan and reference review.

When sources disagree, reconcile them before implementation. Never weaken recovery or permission invariants merely to make a test pass.

## Current baseline

- `AgentLoop.run_task()` is the only normal chat state machine and history writer.
- Session-owned `ConversationLog` enforces Turn/ToolCycle grammar but is process-local.
- `ContextBuilder` is a pure projection.
- `ToolRegistry` freezes the ToolSet; `ToolExecutor` owns validation, policy, approval and handler execution.
- File mutations already expose before/after revisions; Host command outcomes are bounded but may be unknown after interruption.
- Session, ToolFacts and ChangeSets are not yet durable.
- `CapabilityPolicy` currently rejects `FULL_ACCESS`.
- Bootstrap creates a fresh Session for each process.

## Mature-reference policy

| Reference | Adopt | Do not import |
|---|---|---|
| Hermes Agent | SQLite/WAL/migration/write-contention patterns | its full SessionDB or message-centric schema |
| Pi | lineage, self-contained compaction and branch algorithms | JSONL as Morrow operational authority |
| Codex | stable client IDs, optimistic concurrency, Item lifecycle | rollout/index dual authority or full Queue system |
| Claude Code | checkpoint/rewind behavior and runtime permission principles | proprietary implementation or false full-workspace rewind claims |

Rules:

- Morrow's domain invariants remain authoritative.
- Semantic port is the default; no upstream Agent runtime becomes a production dependency.
- Direct code reuse requires a pinned commit, license check, `THIRD_PARTY_NOTICES.md` and adoption log.
- Claude Code is behavior-reference only.
- Upstream failures become named Morrow regression tests.
- Do not add FTS, arbitrary Hooks, Queueing or classifier auto merely because a reference project has them.

## Non-negotiable contracts

1. **One chat writer** — only the leaf `AgentLoop` appends normal conversation history.
2. **Complete ToolCycle** — Assistant calls and ordered ToolMessages are never split.
3. **Persist before side effect** — intent, approval consumption and `executing` must commit first.
4. **No blind replay** — unknown writes and Host commands are never automatically rerun.
5. **One authority per state class** — YAML/config, SQLite operational state and Artifact bytes remain separate.
6. **No event sourcing** — events are ordered projections, not the state-rebuild source.
7. **No ORM by default** — use stdlib `sqlite3` unless an ADR proves it inadequate.
8. **No transaction across await** — Provider, approval wait, handler, Shell and large Artifact writes stay outside DB transactions.
9. **Resume Task, not stale Run** — reconcile the old AgentRun, then create a new frozen AgentRun.
10. **User-only Grant** — model, Tool, Skill, Memory, project files and Provider output cannot elevate permission.
11. **Deny wins** — effective permission is frozen policy ∩ granted subset ∩ valid Grant ∩ hard deny.
12. **No sensitive persistence** — no credentials, private reasoning, raw environment, SDK objects, raw traceback or unbounded output.
13. **Workspace scope is mandatory** — every entity, command and query is workspace-bound.
14. **External writes are idempotent** — `command_id` and `client_message_id` use request hashes and durable uniqueness.
15. **Old mutations are rejected** — approval/recovery/steer/revoke carry expected revision or expected Run identity.
16. **Approval is single-use** — bound to ToolExecution, intent hash, schema digest, permission snapshot, nonce and expiry.
17. **PermissionSnapshot replaces, never partially merges** — Resume and mode switches produce a complete new snapshot.
18. **Operational history is not prompt history** — prompt-visible, display-only and operational records are explicit.
19. **All durable payloads are bounded** — oversized content becomes Artifact or explicit truncation; queries are paginated.
20. **Shutdown drains or fails explicitly** — no successful append may disappear during teardown.
21. **Checkpoint claims are narrow** — only Morrow-managed, checkpointable file edits are restorable.
22. **Corruption is isolated** — one malformed Session cannot hide or damage healthy Sessions.

## Storage authority

```text
Versioned YAML / CredentialStore
- Preferences, Workspace Profile
- Provider non-secret configuration
- Credential references and credentials

SQLite Operational Store
- Session / TaskRun / Turn / AgentRun
- ConversationRecord / ToolExecution / Approval
- CommandReceipt / RunClaim
- TaskOutcome / RecoveryReport / RepairPlan
- CapabilityGrant / PermissionSnapshot
- Artifact metadata / ContextCheckpoint / WorkspaceCheckpoint
- ApplicationEvent / outbox

Filesystem Artifact Store
- command output, patch, diff and test reports
- summaries and checkpoint data
- permitted before-images/reversible patches
- backup bundles
```

SQLite requirements:

- application ID, schema version and migration checksum;
- foreign keys and workspace-scoped relationships;
- session-local monotonic sequence;
- WAL with crash-tested synchronous policy;
- `BEGIN IMMEDIATE` plus bounded jitter retry for short writes;
- SQLite online backup, not copies of active WAL files;
- user-private state/artifact permissions;
- no silent recreation of future or damaged stores;
- no JSONL transcript as a second runtime authority.

Suggested initial budgets, adjustable by ADR:

```text
inline text       32 KiB
single JSON row   128 KiB
durable event     32 KiB
query page        100 default / 500 max
single Artifact   64 MiB
```

## Runtime protocol

### Durable ConversationLog

```text
plan legal append
→ commit through OperationalStore
→ apply committed append to in-memory projection
```

User Turn acceptance is idempotent. Final Assistant content is committed transactionally; streaming deltas are transient or bounded diagnostics and do not automatically re-enter Provider context.

Hydration validates schema, sequence and ToolCycle grammar. Invalid Sessions enter `needs_repair` and remain read-only until diagnosed.

### ToolExecution journal

```text
recorded
→ preflighted
→ awaiting_approval
→ approved
→ approval consumed + executing (one transaction)
→ handler_completed
→ closed
```

Terminal branches:

```text
rejected | failed | cancelled | outcome_unknown | reconciled
```

Each execution stores canonical intent hash, input-schema digest, frozen permission snapshot hash and a structured result envelope with `effect_disposition`.

### Recovery classes

```text
never_started
safe_to_retry
requires_reconciliation
outcome_unknown
completed
```

- Read-only operations may retry only through a registered Recovery Contract.
- File/config writes reconcile with before/expected-after revisions.
- `handler_completed` without ToolMessage closes from the durable result and never reruns.
- Host Shell in `executing` without a durable result is `outcome_unknown`.
- Workspace/cwd/tool-schema drift blocks automatic continuation.

### Context and checkpoints

ContextCheckpoint is self-contained:

```text
source range/hash
+ summary Artifact
+ retained tail
+ active goal/constraints/unresolved items
+ file-operation ledger
+ Artifact refs
+ PromptAssembler version
```

Compaction preserves raw records and complete ToolCycles. A single oversized Turn may use a prefix summary only after large ToolResults are Artifact-backed.

Before each accepted User Turn, create only a lightweight WorkspaceCheckpoint marker. On the first managed write to each path, capture its allowed before-state lazily and durably before the side effect; never scan or copy the whole Workspace. Conversation rewind uses Fork. Managed file restore is separate and fails closed on external, concurrent, Shell, symlink or hardlink changes.

### Permissions

Each AgentRun freezes the complete effective PermissionSnapshot:

```text
workspace identity / canonical cwd
sandbox and allowed roots
network policy
tool capability map
hard denies
granted subset and Grant ids
policy/config/toolset hashes
```

Full Access Manual remains per-operation approved. Controlled Auto is limited to structured, reconcilable Tool intents with registered recovery contracts. Opaque Shell, script interpreters and wrappers do not enter raw auto allowlists.

## Application boundary

Commands include:

```text
session.create/resume/archive/delete/fork
task.start/resume/cancel/accept/correct/abandon
turn.submit / agent.steer
approval.resolve
artifact.pin/release
checkpoint.restore
recovery.resolve
grant.create_full_access/revoke
state.backup / state.repair
```

Each write command carries `command_id`, actor, request version/hash and expected revision/Run where required. `CommandReceipt` makes retries deterministic.

Queries include cursor pagination for Sessions, Tasks, Runs, Events, Artifacts, Checkpoints, Recoveries, Grants and effective permissions. Interfaces never read SQLite directly.

Durable lifecycle events are written through a transactional outbox. Token/text deltas remain transient.

## Ordered subplans

| No. | Slice | Goal | Gate |
|---|---|---|---|
| 35 | Activation | ADRs, reference provenance and fault corpus | no behavior change; sources pinned |
| 36 | 4A | SQLite, migration, CommandReceipt, RunClaim, backup | kill-safe commits and private storage |
| 37 | 4A | Durable no-tool Session/ConversationLog | idempotent exact restore; quarantine works |
| 38 | 4B | Tool journal, one-time Approval, result envelope | no handler before durable gates |
| 39 | 4B | Recovery, shutdown drain and fault injection | no blind duplicate or tail loss |
| 40 | 4C | TaskRun and versioned TaskOutcome | correction remains in one TaskRun |
| 41 | 4D | Artifact Store and budgets | missing/corrupt/orphan/quota are explicit |
| 42 | 4D | Self-contained compaction/checkpoint | repeated compaction remains bounded |
| 43 | 4D | Fork, common ancestor and managed rewind | parent immutable; file conflicts fail closed |
| 44 | 4E | Command/Query/Event, concurrency and outbox | retries idempotent; events replayable |
| 45 | 4E | Doctor/repair, recovery UX and backup | damaged Session isolated; backup consistent |
| 46 | 4F | Full PermissionSnapshot and Manual Full Access | user-only, run-bound, explainable rights |
| 47 | 4F | Controlled Full Access Auto | structured intents only; Shell still approved |
| 48 | Closeout | E2E, regression and docs | all DoD and Stage 3 gates pass |

Only one subplan is active at a time. `.agent/TODO.md` contains tasks for that subplan only.

## Active Subplan 35

Deliverables:

- [ ] Pin current Morrow HEAD.
- [ ] Archive the closed Stage 3 plan and activate this file.
- [ ] Reconcile PLAN / ROADMAP / ARCHITECTURE / README status.
- [ ] Pin Pi, Hermes Agent and Codex reference commits; record Claude Code documentation date.
- [ ] Accept the “semantic port first; no upstream runtime dependency” ADR.
- [ ] Create `docs/references/stage4-reference-lock.yaml`.
- [ ] Create `docs/references/stage4-adoption-log.md`.
- [ ] Create/update `THIRD_PARTY_NOTICES.md`; decide Morrow's own license before direct reuse.
- [ ] Lock IDs, state machines, ownership and storage authority.
- [ ] Lock CommandReceipt/input-idempotency and optimistic-concurrency contracts.
- [ ] Lock ToolExecution journal, one-time Approval and Recovery contracts.
- [ ] Lock immutable PermissionSnapshot and Resume replacement semantics.
- [ ] Lock self-contained ContextCheckpoint and managed rewind limits.
- [ ] Lock Event outbox, storage budgets, shutdown drain, doctor/repair and Full Access ADRs.
- [ ] Convert selected Hermes/Codex failure reports into named fault-test specifications.
- [ ] Index Subplans 36–48.

Gate:

- no Stage 3 runtime behavior changes;
- documents agree on scope and ownership;
- no direct upstream code copied without provenance;
- no Multi-Agent, background worker, arbitrary Hook or raw auto scope creep;
- only after this gate may Subplan 36 add production SQLite code.

## Validation

Focused subplan:

```bash
uv run pytest -q <touched-tests>
uv run ruff format --check .
uv run ruff check .
uv run python -m compileall -q src tests
git diff --check
```

Stage closeout:

```bash
uv run pytest -m 'not live'
uv run ruff format --check .
uv run ruff check .
uv run python -m compileall -q src tests
uv run morrow --help
git diff --check
```

Never claim a gate passed unless it was run.

## Stage 4 completion

Stage 4 is complete only when the detailed plan's DoD passes, including:

- exact Session/Task resume and legal ToolCycles;
- fail-closed side-effect persistence and no blind replay;
- idempotent inputs and one-time approvals;
- immutable, explainable permission snapshots without resume drift;
- traceable TaskOutcome, Artifact and self-contained ContextCheckpoint;
- bounded storage, pagination and repeated-compaction tests;
- managed rewind conflict safety and parent/child Fork isolation;
- shutdown-tail, corruption, migration, online-backup and doctor/repair tests;
- Full Access Manual and controlled Auto separation;
- all upstream-derived failure regressions;
- a real Stage 3 task passing crash/recovery acceptance;
- README, ARCHITECTURE, ROADMAP, data boundaries and acceptance evidence updated.
