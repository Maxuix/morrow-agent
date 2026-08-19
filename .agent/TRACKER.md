# Progress Tracker

## Current status

Stage 4 Durable Task, Session, Artifact, and Recovery implementation is active at Subplan 37. The
v1 Operational Store foundation can create, reopen, migrate, classify, lock, and online-back up a
data-root SQLite file. Conversation, Task, tool, Artifact, event, grant, and Full Access schemas
remain inactive.

## Last completed task

S4.37.1 added typed Session/Task/Turn/AgentRun domain models, independent lifecycle/health axes,
prefixed opaque IDs, three sequence namespaces, and a budgeted non-secret AgentRun snapshot.

## Active task

S4.37.2 — add lifecycle and conversation-journal schemas/ports with workspace-scoped queries,
foreign keys, uniqueness, and sequence constraints.

## Next action

Implement schema v2 and narrow lifecycle/conversation journal ports on the Operational Store
adapter, with focused tests, without wiring AgentLoop yet.

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
