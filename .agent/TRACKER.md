# Progress Tracker

## Current status

Stage 4 Durable Task, Session, Artifact, and Recovery implementation is active at Subplan 37. The
v1 Operational Store foundation can create, reopen, migrate, classify, lock, and online-back up a
data-root SQLite file. Conversation, Task, tool, Artifact, event, grant, and Full Access schemas
remain inactive.

## Last completed task

S4.37.2 added schema v2 and a SQLite journal adapter: workspace-scoped Session/Task/Turn/AgentRun
rows, monotonic conversation positions, turn-submit receipts, and foreign-key/uniqueness checks.
AgentLoop is still process-local.

## Active task

S4.37.3 — refactor ConversationLog behind a durable append boundary that validates first, commits
atomically, and updates its in-memory projection only from the committed record.

## Next action

Add a ConversationLog candidate/validate/apply API so a failed persist cannot leave a memory-only
record, then wire a journal-backed append helper without changing AgentLoop production paths yet.

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
