# TODO

## Current stage

Stage 4 production implementation is active at the Artifact Store. TaskRun/TaskOutcome and recovery
classification have landed; Full Access remains inactive.

## Active subplan

Subplan 41 — Artifact Store and Durable Payload Budgets.

## Tasks

- [>] S4.41.1 Define Artifact/budget models, allowed kinds, sensitivity/retention states, and
  canonical metadata with strict validation.
- [ ] S4.41.2 Implement Artifact metadata storage and opaque path layout confined under the validated
  state root.
- [ ] S4.41.3 Add temp-write/fsync/atomic-rename/parent-fsync/metadata publication and safe
  rollback/orphan discovery at every fault point.
- [ ] S4.41.4 Add hash/size verification, bounded streaming reads, excerpt generation, and visible
  missing/corrupt states.
- [ ] S4.41.5 Integrate bounded redacted command output, diff/report production, ToolExecution links,
  and TaskOutcome references without duplicating complete bytes in conversation rows.
- [ ] S4.41.6 Implement pin/reference-aware retention checks and read-only orphan candidate reports;
  do not implement automatic deletion or heuristic retention.
- [ ] S4.41.7 Add redaction, symlink/path-escape, permission, disk failure, crash, and payload
  exact-boundary tests; update storage/security docs.

Only Subplan 41 may be executed. Checkpoints, grants, and Full Access remain inactive.
