# Progress Tracker

## Current status

Stage 4 Durable Task, Session, Artifact, and Recovery implementation is active at Subplan 37. The
v1 Operational Store foundation can create, reopen, migrate, classify, lock, and online-back up a
data-root SQLite file. Conversation, Task, tool, Artifact, event, grant, and Full Access schemas
remain inactive.

## Last completed task

S4.37.3 moved ConversationLog onto a plan/validate/apply append boundary. A journal-backed writer
persists first and replaces the live projection only from committed rows. AgentLoop still uses the
in-memory begin/append/finish helpers.

## Active task

S4.37.4 — integrate Session construction and AgentLoop no-tool begin/assistant/finish writes so
Turn/User commit precedes `turn.started` and Provider invocation.

## Next action

Give Session one append path that uses the durable writer when a journal is present, and change
AgentLoop so the Turn/User commit happens before `turn.started`.

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
