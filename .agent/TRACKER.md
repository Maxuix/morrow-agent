# Progress Tracker

## Current status

Stage 4 remains accepted. User-authorized Subplan 46 is refactoring ownership and dependency
boundaries without changing schemas, public behavior, security defaults, or Stage 5 scope.

## Last completed task

Stage 4 final Grok review and the single review-fix cycle completed after Subplan 45 closeout. The
review report is `docs/reviews/stage-4-final-grok-review.md`; the final offline and quality gates are
recorded in `docs/acceptance/stage-4-durable-agent-evidence.md`.

Subplan 43 closed after S4.43.1–S4.43.8: v8 typed Command/Query/Event boundary, same-transaction
sanitized application events and receipts, CLI/REPL adapters, read-only doctor, online backup with
Artifact manifest/restore verification, and exact-target dry-run cleanup. Offline suite: 570 passed,
2 skipped, 1 deselected; Ruff, compileall, CLI help, and diff checks passed.

## Active task

S4.46.2 — extract domain application handlers behind the compatible operational API facade.

## Next action

Extract cohesive command/query collaborators without changing the public
`OperationalApplicationService` surface or transaction boundaries.

## Blockers

None.

## Active boundary

- Stage 4 product scope remains closed; Subplan 46 is architecture-only remediation.
- ConversationLog remains the sole chat-history authority; ordinary chat stays on
  `AgentLoop.run_task()`.
- Current YAML/CredentialStore authorities and Stage 3 runtime/security behavior remain unchanged.
- Public event lifecycle and bundled policy-default changes remain explicit hold points; Subplan 43
  left them unchanged.
