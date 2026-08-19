# Progress Tracker

## Current status

Stage 4 Durable Task, Session, Artifact, and Recovery implementation is ready to close Subplan 44.
Interrupted tool work is classified on restart without blind replay, TaskRun/TaskOutcome and
bounded Artifacts, application events, and backup/doctor operation are durable. Full Access remains
inactive until the active grant gate completes.

## Last completed task

Subplan 44 is the current completion candidate after S4.44.1–S4.44.8: v9 typed CapabilityGrant/
PermissionSnapshot evidence, Full Access Manual's single unconfined Host capability, explicit
warning/approval, revocation, crash-resume isolation, doctor checks, and boundary tests. Offline
suite: 600 passed, 2 skipped, 1 deselected; Ruff, compileall, CLI help, and diff checks pass.

Subplan 43 closed after S4.43.1–S4.43.8: v8 typed Command/Query/Event boundary, same-transaction
sanitized application events and receipts, CLI/REPL adapters, read-only doctor, online backup with
Artifact manifest/restore verification, and exact-target dry-run cleanup. Offline suite: 570 passed,
2 skipped, 1 deselected; Ruff, compileall, CLI help, and diff checks passed.

## Active task

Subplan 44 completion commit; then activate Subplan 45.

## Next action

S4.44.1–44.8 are implemented and locally validated. The successful Grok review found no blocker;
its actionable suggestions were fixed, including durable resume snapshots, post-revoke cleanup and
doctor detection, canonical warning digest, unknown Host cancellation disposition, fresh grant
checks, and typed grant errors. Commit this subplan before activating S4.45.1.

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
