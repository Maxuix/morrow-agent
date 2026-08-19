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

Only Subplan 35 may be executed. Subplans 36–45 are planned but inactive.
