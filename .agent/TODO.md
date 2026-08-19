# TODO

## Current stage

Stage 4 is active for contract planning. Production persistence and Full Access behavior have not
started.

## Active subplan

Subplan 35 — Stage 4 Contract Activation and Design Spikes.

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
  crash-resume regrant, revocation, Full Access Manual, honest unconfined Host warning, and deferred
  Auto.
- [x] S4.35.7 Mark the research drafts as superseded decision input and accept zero direct upstream
  reuse; make any future copy a new pin/license/notice hold point.
- [>] S4.35.8 Finish the fault matrix, review every later subplan against the ADRs and external
  review, and run the Subplan gate. Keep Subplan 36 inactive until this remediation is accepted.

Only Subplan 35 may be executed. Subplans 36–45 are revised but inactive; this review turn does not
activate Subplan 36.
