# Progress Tracker

## Current status

Stage 4 Durable Task, Session, Artifact, and Recovery implementation is active at Subplan 41.
Interrupted tool work is classified on restart without blind replay, and TaskRun/TaskOutcome are
durable. Checkpoints, grants, and Full Access remain inactive.

## Last completed task

Subplan 40 closed after S4.40.1–S4.40.7: TaskRun lifecycle/current-task invariants, continuation and
explicit command services, immutable versioned TaskOutcome evidence, v4→v5 migration preservation,
and restart-safe multi-turn acceptance. Offline suite: 536 passed, 2 skipped, 1 deselected.

## Active task

S4.41.1 — define strict Artifact and durable payload-budget contracts.

## Next action

Read the Artifact/context ADR and Subplan 41 contract, then implement only S4.41.1 with focused
model and budget tests.

## Blockers

None. Artifacts and later persistence remain gated by Subplans 41–45.

## Active boundary

- Only the Artifact Store and payload budgets in Subplan 41 are active.
- ConversationLog remains the sole chat-history authority; ordinary chat stays on
  `AgentLoop.run_task()`.
- Current YAML/CredentialStore authorities and Stage 3 runtime/security behavior remain unchanged.
- Public event lifecycle and bundled policy-default changes remain explicit hold points.
