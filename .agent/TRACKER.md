# Progress Tracker

## Current status

Stage 4 Durable Task, Session, Artifact, and Recovery implementation is active at Subplan 38. A
bounded no-tool Session now survives restart with one Turn/UserMessage per `client_message_id`.
Tool journals, recovery, artifacts, grants, and Full Access remain inactive.

## Last completed task

Subplan 37 closed after S4.37.4–S4.37.7: AgentLoop commits Turn/User before `turn.started`,
receipts replay/conflict/recover, restart restore and quarantine work, and `/new`/`/exit` no longer
ask to discard persisted history. Offline suite: 489 passed, 1 deselected.

## Active task

S4.38.1 — define ToolExecution, Approval, EffectClass, recovery declaration, structured tool facts,
and the named test-only fault-injector port.

## Next action

Read the durable-execution ADR and current ToolExecutor/ConversationLog write path, then implement
only S4.38.1 with focused tests.

## Blockers

None. Recovery and later persistence remain gated by Subplans 39–45.

## Active boundary

- Only the v3 tool/approval journal in Subplan 38 is active.
- ConversationLog remains the sole chat-history authority; ordinary chat stays on
  `AgentLoop.run_task()`.
- Current YAML/CredentialStore authorities and Stage 3 runtime/security behavior remain unchanged.
- Public event lifecycle and bundled policy-default changes remain explicit hold points.
