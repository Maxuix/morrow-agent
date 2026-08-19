# TODO

## Current stage

Stage 4 production implementation is active at recovery and crash classification. The v3 tool
execution journal and durable Approval have landed; Full Access remains inactive.

## Active subplan

Subplan 39 — Recovery, Reconciliation, and Crash Harness.

## Tasks

- [>] S4.39.1 Define RecoveryReport/items/decisions and legal resolution transitions with sanitized
  user-facing evidence.
- [ ] S4.39.2 Implement startup discovery and a pure classifier for `never_started`,
  `safe_to_retry`, `requires_reconciliation`, `outcome_unknown`, and `completed`.
- [ ] S4.39.3 Implement file reconciliation from before hash, expected-after hash, expected size,
  parent/auxiliary conditions, and mutation result evidence—never volatile mtime equality alone.
- [ ] S4.39.4 Classify every Host or native-sandbox execution lacking committed
  `handler_completed` as `outcome_unknown`; never infer safe retry from missing PID, missing temp
  root, elapsed time, cleanup state, or current process visibility. Reconcile promotion per file.
- [ ] S4.39.5 Add the narrow validated ConversationLog recovery closure and idempotent recovery
  command path without ordinary-message direct writes: append only ordered interrupted/error
  ToolMessages and a truthful non-success terminal, never a success envelope/User/Assistant.
- [ ] S4.39.6 Build subprocess fixtures that crash at every committed fault point and report via
  pipes/files rather than timing sleeps.
- [ ] S4.39.7 Add clean shutdown/restart stories, operator explanations, and focused/full regression
  validation.

Only Subplan 39 may be executed. Artifacts, grants, and Full Access remain inactive.
