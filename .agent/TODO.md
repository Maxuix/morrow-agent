# TODO

## Current stage

Stage 4 production implementation is active at deterministic context checkpoints and conversation
fork. TaskRun/TaskOutcome, recovery classification, and the Artifact Store have landed; Full Access
remains inactive.

## Active subplan

Subplan 42 — Context Checkpoints and Conversation Fork.

## Tasks

- [>] S4.42.1 Define checkpoint/provenance/fork models and legal complete-cycle source boundaries.
- [ ] S4.42.2 Implement deterministic compaction from durable records/Artifacts with typed omitted-
  content reasons and exact budgets.
- [ ] S4.42.3 Integrate ContextBuilder selection of fixed boundary, resolved run snapshot, active
  task state, checkpoint projection, recent complete cycles, Artifact excerpts, and current input.
- [ ] S4.42.4 Implement fork creation from a legal Turn/checkpoint in one transaction, with immutable
  parent-prefix links, exact included record IDs/cut position, reference-only Artifact sharing, and
  no copied Session Preferences, TaskRun, Approval, or CapabilityGrant.
- [ ] S4.42.5 Add interruption, regeneration, corrupt/missing Artifact, boundary, budget, and
  parent/child isolation tests.
- [ ] S4.42.6 Prove a long scripted task continues after multiple checkpoints and that source records
  remain queryable and unchanged.
- [ ] S4.42.7 Document the distinction among raw history, context projection, Artifact, TaskOutcome,
  and future Stage 5 memory; run gates.

Only Subplan 42 may be executed. Grants, Command/Query/Event, and Full Access remain inactive.
