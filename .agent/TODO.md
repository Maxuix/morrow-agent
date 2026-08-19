# TODO

## Current stage

Stage 4 production implementation is active at the TaskRun lifecycle. Recovery classification has
landed; Full Access remains inactive.

## Active subplan

Subplan 40 — TaskRun Lifecycle and Versioned TaskOutcome.

## Tasks

- [>] S4.40.1 Lock and implement legal TaskRun states/transitions, terminality, current-task
  selection, and invariants for one foreground task per Session.
- [ ] S4.40.2 Route ordinary post-answer follow-up from `ready_for_acceptance` back to `open` in the
  same TaskRun as continuation/correction;
  implement explicit `/task new`, `/accept`, cancel, resume/retry, and abandon commands.
- [ ] S4.40.3 Define a versioned TaskOutcome schema whose fields are typed references to durable
  records and bounded user-facing summaries.
- [ ] S4.40.4 Create deterministic immutable outcome versions only for explicit acceptance,
  explicit outcome snapshot, or terminal close; later correction supersedes rather than mutates an
  existing version.
- [ ] S4.40.5 Add idempotency, stale-version, cross-workspace, crash, and invalid-transition tests.
- [ ] S4.40.6 Prove a multi-Turn task with clarification, tool work, completion, user correction,
  re-completion, and explicit acceptance across restarts.
- [ ] S4.40.7 Document exact product semantics and run focused/quality/Stage 3 regressions.

Only Subplan 40 may be executed. Artifacts, grants, and Full Access remain inactive.
