# TODO

## Current stage

Pre-Stage 5 boundary refactor; Stage 5 remains inactive.

## Active subplan

48 — runtime, persistence, storage, and application boundaries.

## Tasks

- [x] S48.1 Define and adopt the explicit durable runtime contract.
- [x] S48.2 Extract AgentLoop run state and tool-cycle execution.
- [x] S48.3 Decompose SessionPersistence behind its compatible facade.
- [x] S48.4 Partition the SQLite journal behind one transaction context.
- [x] S48.5 Decouple application collaborators and centralize operational composition.
- [>] S48.6 Remove stale abstractions, reconcile docs, and pass final gates.
