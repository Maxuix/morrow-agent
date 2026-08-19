# Subplan 40 — TaskRun Lifecycle and Versioned TaskOutcome

> Status: pending
> Prerequisite: Subplan 39 accepted
> Owns: foreground task semantics, explicit feedback, and deterministic outcome evidence

## Objective

Represent a user's continuing foreground goal independently from individual Turns, preserve its
status across restarts, and generate immutable versioned outcomes that Stage 5 can later review
without treating summaries as truth.

## In scope

- Complete TaskRun state machine and transition service.
- Current-task selection inside a Session.
- Continuation/correction/new-task/accept/cancel/abandon semantics.
- Immutable TaskOutcome versions derived deterministically from durable records.
- Outcome evidence for changed paths, validation facts, side effects, artifacts, unresolved items,
  completion basis, and explicit feedback.
- Retry-sensitive Task commands with command receipts and optimistic concurrency.
- Query projections needed for tests and later CLI.

## Out of scope

- Long-term learning, preference/knowledge mutation, reward scoring, or automatic acceptance.
- Multi-agent Workflow tasks, background jobs, and cross-Session task merging.
- LLM-written TaskOutcome as the sole truth source.

## Tasks

- [ ] S4.40.1 Lock and implement legal TaskRun states/transitions, terminality, current-task
  selection, and invariants for one foreground task per Session.
- [ ] S4.40.2 Route ordinary post-completion follow-up to continuation/correction in the same TaskRun;
  implement explicit `/task new`, `/accept`, cancel, resume/retry, and abandon commands.
- [ ] S4.40.3 Define a versioned TaskOutcome schema whose fields are typed references to durable
  records and bounded user-facing summaries.
- [ ] S4.40.4 Implement deterministic outcome projection/version creation and immutable supersession
  when a completed TaskRun is corrected.
- [ ] S4.40.5 Add idempotency, stale-version, cross-workspace, crash, and invalid-transition tests.
- [ ] S4.40.6 Prove a multi-Turn task with clarification, tool work, completion, user correction,
  re-completion, and explicit acceptance across restarts.
- [ ] S4.40.7 Document exact product semantics and run focused/quality/Stage 3 regressions.

## Locked product semantics

- First ordinary input starts a TaskRun only when no current TaskRun exists.
- Final assistant response means `completed`, not `accepted`.
- Ordinary input after completion remains the same TaskRun unless the user explicitly starts a new
  task; it records correction/continuation evidence and produces a later outcome version.
- `/new` creates a Session. `/task new` creates a TaskRun. `/accept` records explicit user acceptance.
- Failed/cancelled work keeps its prior side effects and may be explicitly resumed/retried as a new
  attempt, never edited into a fictitious uninterrupted run.

## Tests and faults

- all legal/illegal transitions and optimistic-version conflicts;
- duplicate accept/cancel/new-task commands and same receipt with mismatched payload;
- multi-turn continuation versus explicit new task/session;
- crash before/after transition and outcome commit;
- deterministic repeated projection produces the same canonical evidence;
- corrected outcomes supersede rather than mutate old versions;
- no secrets, reasoning, or unbounded tool payloads in outcomes.

## Completion gate

A task can span clarification and corrections across process restarts, yields deterministic immutable
TaskOutcome versions, and becomes accepted only through explicit product semantics. Stage 5 can
consume references without gaining write authority over Stage 4 history.

## Deliverables

- Complete TaskRun transition/application service.
- Versioned TaskOutcome model, store, and projection.
- Foreground product-story and crash evidence.

