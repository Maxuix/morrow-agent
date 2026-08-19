# Progress Tracker

## Current status

Stage 4 Durable Task, Session, Artifact, and Recovery implementation is active at Subplan 44.
Interrupted tool work is classified on restart without blind replay, TaskRun/TaskOutcome and
bounded Artifacts, application events, and backup/doctor operation are durable. Full Access remains
inactive until the active grant gate completes.

## Last completed task

Subplan 43 closed after S4.43.1–S4.43.8: v8 typed Command/Query/Event boundary, same-transaction
sanitized application events and receipts, CLI/REPL adapters, read-only doctor, online backup with
Artifact manifest/restore verification, and exact-target dry-run cleanup. Offline suite: 570 passed,
2 skipped, 1 deselected; Ruff, compileall, CLI help, and diff checks passed.

## Active task

S4.44.1 — implement strict CapabilityGrant/PermissionSnapshot models and schema.

## Next action

Read the Subplan 44 contract and existing capability/approval composition, then define only S4.44.1
with focused grant and immutable snapshot tests.

## Blockers

None. Application events, grants, and Full Access remain gated by Subplans 43–45.

## Active boundary

- Only CapabilityGrant, PermissionSnapshot, and their directly required grant-boundary work in
  Subplan 44 is active.
- ConversationLog remains the sole chat-history authority; ordinary chat stays on
  `AgentLoop.run_task()`.
- Current YAML/CredentialStore authorities and Stage 3 runtime/security behavior remain unchanged.
- Public event lifecycle and bundled policy-default changes remain explicit hold points; Subplan 43
  left them unchanged.
