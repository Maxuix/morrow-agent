# Subplan 35 — Stage 4 Contract Activation and Design Spikes

> Status: active
> Kind: planning, ADRs, and bounded design-spike tests
> Production behavior change: forbidden
> Prerequisite: accepted Stage 3 baseline at `003dbdaab652520ca5cadf451ebca7a13bcba36d`

## Objective

Translate the Stage 4 research into one implementable contract that matches the existing code,
settles cross-cutting ambiguities before schema work, and provides fault matrices for Subplans
36–45.

## In scope

- Reconcile `.agent/PLAN.md`, Stage 4 roadmap, architecture status, and active execution state.
- Inspect current Session, ConversationLog, AgentLoop, ToolExecutor, configuration authorities,
  locking, CLI composition, and security boundaries.
- Write accepted Stage 4 ADRs under `docs/decisions/` for:
  - operational-store authority, location, SQLite settings, concurrency, migration, and backup;
  - identity, lifecycle, health, task continuation, and message-writer ownership;
  - durable payload envelopes, ToolExecution/Approval protocol, EffectClass, and recovery;
  - Artifact publication, context checkpoint provenance, and conversation-only fork;
  - PermissionSnapshot, CapabilityGrant, Full Access Manual, and `unconfined_host` semantics.
- Build a requirement-to-fault-to-test matrix, including logical faults and subprocess death.
- Pin and license-check mature references before any direct reuse decision.
- Use bounded disposable SQLite spikes/tests only where documentation cannot prove behavior.

## Out of scope

- Production SQLite adapter, schema, or Artifact Store.
- Persistent Session behavior or runtime integration.
- Public event lifecycle or bundled policy-default changes.
- Full Access activation.
- Direct third-party code reuse.

## Tasks

- [x] S4.35.1 Activate the reconciled master plan, subplan index, roadmap status, and execution
  state without changing production behavior.
- [x] S4.35.2 Write the Operational Store ADR and prove the chosen `sqlite3` transaction, WAL/
  synchronous, lock-contention, migration-lock, future-schema, and online-backup behavior in a
  task-private spike.
- [ ] S4.35.3 Write the domain/ownership ADR: identifiers, Session lifecycle versus health,
  TaskRun continuation rules, AgentRun snapshots, ConversationLog single-writer protocol, and
  targeted command idempotency.
- [ ] S4.35.4 Write the durable execution ADR: payload budgets, ToolExecution/Approval transitions,
  EffectClass, persist-before-effect transaction boundary, and recovery classifications.
- [ ] S4.35.5 Write the Artifact/context/fork ADR, including atomic publication, hash verification,
  deterministic checkpoints, provenance ranges, and the no-workspace-rewind boundary.
- [ ] S4.35.6 Write the permissions ADR: user-only grants, run-bound snapshots, revocation, Full
  Access Manual, protected direct tools, honest unconfined Host warning, and deferred Auto.
- [ ] S4.35.7 Pin any reference used beyond semantics, record license/provenance, and decide whether
  third-party notices are actually required.
- [ ] S4.35.8 Finish the fault matrix, review every later subplan against the ADRs, run the Subplan
  gate, and activate Subplan 36 only after acceptance.

## Required decisions

The ADR set must explicitly answer:

1. Exact state-root/database/artifact layout and file permissions.
2. SQLite schema version authority, connection policy, durability settings, bounded contention,
   global maintenance lock, future-version refusal, corruption/quarantine, and backup protocol.
3. Which record owns each identifier, sequence, state transition, and durable payload.
4. How ConversationLog validation and SQLite commit form one append boundary without dual writing.
5. Which commands require idempotency receipts and what duplicate callers receive.
6. ToolExecution states before/after approval, handler completion, ToolMessage append, and recovery.
7. Per-payload redaction and byte/record budgets, with policy-default changes called out separately.
8. Full Access Manual's exact supported capabilities and its unconfined Host threat statement.

## Validation

- ADR consistency/link check and `git diff --check`.
- Any spike tests run from task-private temporary directories and leave no persistent state.
- Existing focused ConversationLog, tool-policy, workspace-lock, and Stage 3 security tests still
  pass if spike support code touches test utilities.
- Ruff format/check and compileall if Python spike/test files are added.

## Completion gate

Subplan 35 is complete only when every required decision is explicit, later subplans contain no
contradictory ownership or scope, the fault matrix maps every Stage 4 completion criterion to a
test layer, source governance is recorded, and production behavior still matches the Stage 3
baseline.

## Deliverables

- Accepted Stage 4 ADR set and fault matrix.
- Reconciled plan/roadmap/architecture documents.
- Evidence for SQLite feasibility and selected durability/concurrency settings.
- Activation-ready Subplan 36 with no unresolved schema-level ambiguity.
