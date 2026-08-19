# Progress Tracker

## Current status

Stage 4 Durable Task, Session, Artifact, and Recovery implementation is active at Subplan 42.
Interrupted tool work is classified on restart without blind replay, TaskRun/TaskOutcome and
bounded Artifacts are durable. Grants, application events, and Full Access remain inactive.

## Last completed task

Subplan 41 closed after S4.41.1–S4.41.7: strict Artifact/budget models, v6 metadata/reference
migration, atomic private publication, same-descriptor integrity reads, bounded redacted command
output, ToolExecution/TaskOutcome links, retention/orphan reports, and visible missing/corrupt
states. Offline suite: 544 passed, 2 skipped, 1 deselected.

## Active task

S4.42.1 — define checkpoint/provenance/fork models and legal complete-cycle source boundaries.

## Next action

Read the Artifact/context ADR and Subplan 42 contract, then implement only S4.42.1 with focused
checkpoint, provenance, and boundary tests.

## Blockers

None. Context fork, application API, grants, and Full Access remain gated by Subplans 42–45.

## Active boundary

- Only deterministic context checkpoints and conversation fork in Subplan 42 are active.
- ConversationLog remains the sole chat-history authority; ordinary chat stays on
  `AgentLoop.run_task()`.
- Current YAML/CredentialStore authorities and Stage 3 runtime/security behavior remain unchanged.
- Public event lifecycle and bundled policy-default changes remain explicit hold points.
