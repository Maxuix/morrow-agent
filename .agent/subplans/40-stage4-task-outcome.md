# Subplan 40 — TaskRun Lifecycle and Versioned TaskOutcome

> Status: completed
> Prerequisite: Subplan 39 accepted
> Owns: foreground task semantics, explicit feedback, and deterministic outcome evidence
> Schema: v5 complete TaskRun state machine and TaskOutcome versions

## Objective

Represent a user's continuing foreground goal independently from individual Turns, preserve its
status across restarts, and generate immutable versioned outcomes that Stage 5 can later review
without treating summaries as truth.

## In scope

- Complete TaskRun state machine and transition service.
- Current-task selection inside a Session.
- Continuation/correction/new-task/accept/cancel/abandon semantics.
- Immutable TaskOutcome versions derived deterministically from durable records.
- Outcome evidence for changed paths, validation facts, side effects, unresolved items, completion
  basis, and explicit feedback from durable structured facts; Artifact links are added in 41.
- Retry-sensitive Task commands with command receipts and optimistic concurrency.
- Query projections needed for tests and later CLI.

## Out of scope

- Long-term learning, preference/knowledge mutation, reward scoring, or automatic acceptance.
- Multi-agent Workflow tasks, background jobs, and cross-Session task merging.
- LLM-written TaskOutcome as the sole truth source.

## Tasks

- [x] S4.40.1 Lock and implement legal TaskRun states/transitions, terminality, current-task
  selection, and invariants for one foreground task per Session.
- [x] S4.40.2 Route ordinary post-answer follow-up from `ready_for_acceptance` back to `open` in the
  same TaskRun as continuation/correction;
  implement explicit `/task new`, `/accept`, cancel, resume/retry, and abandon commands.
- [x] S4.40.3 Define a versioned TaskOutcome schema whose fields are typed references to durable
  records and bounded user-facing summaries.
- [x] S4.40.4 Create deterministic immutable outcome versions only for explicit acceptance,
  explicit outcome snapshot, or terminal close; later correction supersedes rather than mutates an
  existing version.
- [x] S4.40.5 Add idempotency, stale-version, cross-workspace, crash, and invalid-transition tests.
- [x] S4.40.6 Prove a multi-Turn task with clarification, tool work, completion, user correction,
  re-completion, and explicit acceptance across restarts.
- [x] S4.40.7 Document exact product semantics and run focused/quality/Stage 3 regressions.

## Locked product semantics

- First ordinary input starts a TaskRun only when no current TaskRun exists.
- A final Assistant response closes its Turn and may move `open` to the non-terminal
  `ready_for_acceptance`; it never means accepted and does not by itself require an Outcome version.
- Ordinary input after `ready_for_acceptance` returns the same TaskRun to `open` unless the user
  explicitly starts a new task; it records correction/continuation evidence without rewriting an
  old Outcome.
- `/new` creates a Session. `/task new` creates a TaskRun. `/accept` records explicit user acceptance.
- `waiting_approval` is ToolExecution/Approval state and never a TaskRun state.
- Failed/cancelled work keeps its prior side effects and may be explicitly resumed/retried as a new
  attempt, never edited into a fictitious uninterrupted run.

## Tests and faults

- all legal/illegal transitions and optimistic-version conflicts;
- duplicate accept/cancel/new-task commands and same receipt with mismatched payload;
- multi-turn continuation versus explicit new task/session;
- crash before/after `ready_for_acceptance`/accept/terminal transition and outcome commit;
- ordinary final responses do not emit mandatory Outcome rows or Artifact references;
- deterministic repeated projection produces the same canonical evidence;
- corrected outcomes supersede rather than mutate old versions;
- no secrets, reasoning, or unbounded tool payloads in outcomes.

## Completion gate

A task can span clarification and corrections across process restarts, re-enter `open` after an
answer, yields deterministic immutable TaskOutcome versions only at explicit milestones, and becomes
accepted only through explicit product semantics. Stage 5 can consume references without gaining
write authority over Stage 4 history.

## Deliverables

- Complete TaskRun transition/application service.
- Versioned TaskOutcome model, store, and projection.
- Foreground product-story and crash evidence.
