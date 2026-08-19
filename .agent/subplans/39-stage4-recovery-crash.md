# Subplan 39 — Recovery, Reconciliation, and Crash Harness

> Status: pending
> Prerequisite: Subplan 38 accepted
> Owns: interrupted-state classification, recovery decisions, and real crash evidence

## Objective

Recover foreground Stage 3 tool work without replaying unknown effects, fabricating success, or
rewriting history. Make every interrupted state lead to a deterministic explanation and safe next
action.

## In scope

- RecoveryReport, recovery item, decision, and reconciliation evidence models.
- Startup scan for open Turns/AgentRuns/ToolExecutions and invalid operational health.
- Pure classification engine driven by persisted state and each tool's recovery declaration.
- Narrow ConversationLog recovery close API for already-recorded interrupted ToolCycles.
- Read retry, structured file reconciliation, sandbox-process reconciliation, and Host-process
  unknown-outcome behavior.
- Idempotent user recovery decisions using command receipts.
- Logical fault injector and subprocess `os._exit` crash harness with IPC/barriers.
- Clean shutdown and cancelled-process proof required before any safe retry.

## Out of scope

- Automatic repair of database/business history.
- Automatic replay of externally effectful or unproved operations.
- Background resume, run claims/leases, in-flight steering, or remote coordination.
- Workspace/code rewind.

## Tasks

- [ ] S4.39.1 Define RecoveryReport/items/decisions and legal resolution transitions with sanitized
  user-facing evidence.
- [ ] S4.39.2 Implement startup discovery and a pure classifier for `never_started`,
  `safe_to_retry`, `requires_reconciliation`, `outcome_unknown`, and `completed`.
- [ ] S4.39.3 Implement file reconciliation from before hash, expected-after hash, expected size,
  parent/auxiliary conditions, and mutation result evidence—never volatile mtime equality alone.
- [ ] S4.39.4 Implement process reconciliation: prove termination and no promotion before eligible
  sandbox retry; classify unproved Host execution as unknown and require user resolution.
- [ ] S4.39.5 Add the narrow validated ConversationLog recovery closure and idempotent recovery
  command path without ordinary-message direct writes.
- [ ] S4.39.6 Build subprocess fixtures that crash at every committed fault point and report via
  pipes/files rather than timing sleeps.
- [ ] S4.39.7 Add clean shutdown/restart stories, operator explanations, and focused/full regression
  validation.

## Locked recovery outcomes

- **resume same open Turn:** no new user input; create a new AgentRun and continue only after every
  blocking execution is reconciled or explicitly resolved.
- **retry:** only a declared safe operation with satisfied preconditions; keep the old execution
  immutable and link the new attempt.
- **mark unknown/acknowledge:** record the user's decision and continue without inventing a result.
- **abort/cancel:** close the run/turn with truthful interrupted evidence and retain known effects.
- **quarantine:** change health to `needs_repair` or `read_only`; preserve lifecycle and source data.

## Tests and faults

- every state boundary from persisted intent through ToolCycle close under exception and
  subprocess death;
- unchanged/expected/third-party-changed/missing file after mutation interruption;
- command never started, running at parent death, exited with persisted evidence, and effect unknown;
- sandbox snapshot present/missing, process alive/dead, promotion absent/present;
- duplicate/conflicting recovery decisions and crash during recovery resolution;
- no new input resumes same open Turn; new input opens a new Turn;
- recovery snapshot always passes ConversationLog invariants or remains quarantined.

## Completion gate

The crash matrix for real Stage 3 read, file mutation, Host command, sandbox command, and promotion
paths produces deterministic recovery reports. No unknown side effect is automatically replayed,
and no recovery path bypasses ConversationLog or overwrites damaged history.

## Deliverables

- Recovery service, reports, decision commands, and reconciliation adapters.
- Narrow ConversationLog recovery API.
- Deterministic logical/subprocess crash harness and acceptance evidence.

