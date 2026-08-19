# Progress Tracker

## Current status

Stage 4 Durable Task, Session, Artifact, and Recovery implementation is active at Subplan 37. The
v1 Operational Store foundation can create, reopen, migrate, classify, lock, and online-back up a
data-root SQLite file. Conversation, Task, tool, Artifact, event, grant, and Full Access schemas
remain inactive.

## Last completed task

S4.36.7 closed Subplan 36. Focused operational-store tests, Ruff format/check, compileall,
`git diff --check`, and the offline suite (`465 passed, 1 deselected`) all passed. Stage 3 YAML and
CredentialStore authorities are unchanged; production startup still does not open the store.

## Active task

S4.37.1 — add typed domain models, separate lifecycle/health axes, immutable IDs, the three order
namespaces, base AgentRun source snapshots, and payload validators.

## Next action

Read the domain/conversation ADR and current Session/ConversationLog/AgentLoop types, then implement
only S4.37.1 with focused tests before adding the v2 schema.

## Blockers

None. Tool journals and later persistence remain gated by Subplans 38–45.

## Active boundary

- Only the v2 durable no-tool Session/Task/Turn/AgentRun/conversation work in Subplan 37 is active.
- ConversationLog remains the sole chat-history authority; ordinary chat stays on
  `AgentLoop.run_task()`.
- Current YAML/CredentialStore authorities and Stage 3 runtime/security behavior remain unchanged.
- Full Access Manual is planned; Controlled Full Access Auto, raw auto, code rewind, background
  work, outbox workers, automatic repair, in-flight steering, FTS/embeddings, and Stage 5 learning
  are deferred.
- Public event lifecycle and bundled policy-default changes remain explicit hold points.
