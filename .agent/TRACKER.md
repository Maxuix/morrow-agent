# Progress Tracker

## Current status

Stage 4 Durable Task, Session, Artifact, and Recovery implementation is active at Subplan 43.
Interrupted tool work is classified on restart without blind replay, TaskRun/TaskOutcome and
bounded Artifacts are durable. Grants, application events, and Full Access remain inactive.

## Last completed task

Subplan 42 closed after S4.42.1–S4.42.7: v7 ContextCheckpoint metadata and deterministic bounded
projection, checkpoint Artifact references, parent-prefix Session lineage, restart-safe effective
history, ContextBuilder checkpoint selection, and isolated closed-boundary fork. Offline suite:
560 passed, 2 skipped, 1 deselected.

## Active task

S4.43.1 — define stable Command/Query DTOs, error mapping, receipts, cursor pagination, and
workspace isolation for completed domains.

## Next action

Read the Subplan 43 contract and existing application/CLI surfaces, then define only S4.43.1 with
focused DTO, error, receipt, cursor, and workspace-isolation tests.

## Blockers

None. Application events, grants, and Full Access remain gated by Subplans 43–45.

## Active boundary

- Only Command/Query/Event, CLI, doctor, and backup work in Subplan 43 is active.
- ConversationLog remains the sole chat-history authority; ordinary chat stays on
  `AgentLoop.run_task()`.
- Current YAML/CredentialStore authorities and Stage 3 runtime/security behavior remain unchanged.
- Public event lifecycle and bundled policy-default changes remain explicit hold points.
