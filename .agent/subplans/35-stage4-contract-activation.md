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
- Close every P0/P1 contract issue accepted from `docs/reviews/stage-4-plan-review.md` and revise
  later subplans in their own text, not only in LOG.
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
- [x] S4.35.3 Write the domain/ownership ADR: identifiers, Session lifecycle versus health,
  TaskRun continuation rules, AgentRun snapshots, ConversationLog single-writer protocol, and
  targeted command idempotency.
- [x] S4.35.4 Write the durable execution ADR: payload budgets, ToolExecution/Approval transitions,
  EffectClass, persist-before-effect transaction boundary, and recovery classifications.
- [x] S4.35.5 Write the Artifact/context/fork ADR, including atomic publication, hash verification,
  deterministic checkpoints, provenance ranges, and the no-workspace-rewind boundary.
- [x] S4.35.6 Write the permissions ADR: local-interface-only grants, run-bound snapshots,
  crash-resume regrant, revocation, Full
  Access Manual, protected direct tools, honest unconfined Host warning, and deferred Auto.
- [x] S4.35.7 Mark all research drafts as superseded decision input and accept zero direct upstream
  code/schema/fixture/asset reuse; any future direct reuse is a new pin/license/notice hold point.
- [>] S4.35.8 Finish the fault matrix, revise every later subplan against the ADRs/review, run the
  Subplan gate, and keep Subplan 36 inactive until the remediation is explicitly accepted.

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

## Review-remediation blockers

Subplan 36 cannot activate until all five accepted P0 findings are closed in authority documents
and later gates:

1. `client_message_id` means one accepted Turn/UserMessage; crash recovery may create a linked new
   AgentRun in that same Turn.
2. Subplan 37 owns only an `open` current TaskRun pointer; Subplan 40 owns
   `ready_for_acceptance`/acceptance/terminal transitions and outcome creation.
3. ConversationLog uses validate → one COMMIT → projection refresh; recovery may append only ordered
   interrupted/error ToolMessages and a truthful non-success terminal.
4. All three research drafts are visibly superseded and cannot reactivate Auto/Rewind/Outbox/
   RunClaim/nonce or old numbering.
5. Host and native sandbox execution without committed completion are `outcome_unknown` in Stage 4
   v1; only structured pre-effect evidence supports reconciliation.

The final review also verifies the v1–v9 schema map, three sequence namespaces, durable `/new`/
`/exit` semantics, system-prompt/test migration, event-loop SQLite ownership, grant reissue after
crash, explicit payload numbers, and subplan ownership boundaries.

## Validation

- ADR consistency/link check and `git diff --check`.
- Any spike tests run from task-private temporary directories and leave no persistent state.
- Existing focused ConversationLog, tool-policy, workspace-lock, and Stage 3 security tests still
  pass if spike support code touches test utilities.
- Ruff format/check and compileall if Python spike/test files are added.

## Completion gate

Subplan 35 is complete only when every required decision and review blocker is explicit, later
subplans contain no contradictory ownership or scope, the fault matrix maps every Stage 4
completion criterion to a test layer, source governance is recorded, validation passes, and
production behavior still matches the Stage 3 baseline. Completion does not itself activate
Subplan 36.

Planning validation passed on 2026-08-19: Operational Store spike `15 passed`, Ruff format/check,
compileall, local Markdown-link validation, and `git diff --check`. The remediation is ready for
explicit acceptance; Subplan 35 stays active and Subplan 36 stays pending until that decision.

## Deliverables

- Accepted Stage 4 ADR set and fault matrix.
- Reconciled plan/roadmap/architecture documents.
- Evidence for SQLite feasibility and selected durability/concurrency settings.
- Activation-ready Subplan 36 with no unresolved schema-level ambiguity.
