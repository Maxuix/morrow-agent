# Progress Tracker

## Current status

Stage 4 Durable Task, Session, Artifact, and Recovery implementation is active at Subplan 39. A
bounded Session now survives restart with persist-before-effect tool intents and one-shot durable
approvals. Recovery classification, artifacts, grants, and Full Access remain inactive.

## Last completed task

Subplan 38 closed after S4.38.1–S4.38.7: ToolExecution/Approval/EffectClass contracts, v3 journal,
Assistant+intent commit before dispatch, consume+executing as one transaction, handler_completed
then ToolCycle close, production-tool declarations, and fault/redaction tests. Offline suite:
511 passed, 1 deselected.

## Active task

S4.39.1 — define RecoveryReport/items/decisions and legal resolution transitions.

## Next action

Read the durable-execution ADR recovery classifier and current journal rows, then implement only
S4.39.1 with focused tests.

## Blockers

None. Artifacts and later persistence remain gated by Subplans 40–45.

## Active boundary

- Only recovery classification and the crash harness in Subplan 39 are active.
- ConversationLog remains the sole chat-history authority; ordinary chat stays on
  `AgentLoop.run_task()`.
- Current YAML/CredentialStore authorities and Stage 3 runtime/security behavior remain unchanged.
- Public event lifecycle and bundled policy-default changes remain explicit hold points.
