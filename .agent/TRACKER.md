# Progress Tracker

## Current status

Stage 4 Durable Task, Session, Artifact, and Recovery implementation is active at Subplan 40.
Interrupted tool work is classified on restart without blind replay. TaskOutcome, artifacts, grants,
and Full Access remain inactive.

## Last completed task

Subplan 39 closed after S4.39.1–S4.39.7: RecoveryReport/decisions, startup discovery, hash-based
file/config reconciliation, Host/sandbox `outcome_unknown`, ConversationLog recovery-close,
idempotent receipts, and subprocess crash classification. Offline suite: 531 passed, 1 deselected.

## Active task

S4.40.1 — lock and implement legal TaskRun states/transitions and current-task selection.

## Next action

Read the domain ADR TaskRun state machine and current open-only TaskRun row, then implement only
S4.40.1 with focused tests.

## Blockers

None. Artifacts and later persistence remain gated by Subplans 41–45.

## Active boundary

- Only the TaskRun lifecycle and TaskOutcome in Subplan 40 are active.
- ConversationLog remains the sole chat-history authority; ordinary chat stays on
  `AgentLoop.run_task()`.
- Current YAML/CredentialStore authorities and Stage 3 runtime/security behavior remain unchanged.
- Public event lifecycle and bundled policy-default changes remain explicit hold points.
