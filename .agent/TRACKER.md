# Progress Tracker

## Current status

Subplan 85 and its prepared Subplan 88 candidate are integrated on local `main` at `7b52f5f`.
Subplan 88 is complete, approved and fast-forward integrated into local `main` at `67aa785`.
Durable bounded steering and follow-up delivery preserve S7P-06 behavior and ConversationLog
ownership. Final Luna Max review returned APPROVE with no P0-P3 findings.

## Active task

None. The ordered Stage 7 sequence is paused before S7P-08 pending explicit user direction.

## Completed evidence

- `CommandToolFact` and scoped recognized `ValidationFact` are separate; utility and ambiguous
  shell success cannot become validation.
- Frozen Outcome Contract, no-follow workspace baseline, net diff/path/attribution checks,
  required validation, unresolved-tool, known-failure and optional-verifier gates are integrated.
- Final claims are buffered; one fact-only correction is allowed; Session-owned
  `ConversationLog` remains the sole chat writer; stop/result codes are exact and bounded.
- Focused gates passed: `18`, `34`, `63`, `28`, `24`, `30`, `37`, and `3` tests respectively.
- Full offline fallback passed `1266 passed, 2 skipped, 2 deselected in 56.24s`; Ruff format/check,
  compileall, both CLI help commands, import proof and `git diff --check` passed.
- The implementation task performed no live Provider/model/Pi/MCP/network/credential test,
  dependency installation, push or merge; the root task later performed only the recorded local
  fast-forward integration.
- Root integration verification passed the S7P-05 matrix (`30 passed`) and adjacent
  terminal/preparation/capability suite (`43 passed`), confirmed the topic import path, clean diff
  and fast-forward ancestry, then integrated the branch locally.
- Subplan 86 replaced lexical/regex OutcomeContract inference with one strict no-tool semantic
  resolver; ambiguous or invalid output fails closed, and explicit trusted contracts remain the
  only bypass. Schema v19 records request purpose, exact prompt projection evidence and the resolved
  contract append-only without rewriting the immutable AgentRun snapshot.
- The first persistent write into a newly discovered scope is durably deferred until nested project
  instructions are loaded; refresh failures stop effects. Validator declarations and runtime
  recognition share one registry; failure obligations are tool-family plus independent path scopes;
  all terminal paths release cached plans.
- Git baselines now represent clean tracked state through HEAD and retain only bounded dirty-path
  evidence, restoring the locked 64 KiB AgentRun snapshot ceiling. Completion obtains exact HEAD
  blobs lazily for changed-path attribution, and corrected-request telemetry uses the exact validated
  request size.
- Focused remediation/adjacent gates passed `79 passed`; migration/backup/recovery gates passed
  `98 passed`; the final focused gate after obligation hardening passed `73 passed`.
- Final offline regression passed `1282 passed, 2 skipped, 2 deselected in 61.15s`. Ruff
  format/check, compileall, both CLI help entrypoints and `git diff --check` passed. No live or
  network test ran.
- S7P-06 final implementation passed the dedicated parity/observability and migration/file
  continuation regressions; the formal Kuhn read-only review returned `REQUEST CHANGES` with no
  P0, and all one P1/three P2 findings were reproduced and closed in `d230126`.
- Final S7P-06 offline gate passed `1272 passed, 2 deselected`; Ruff format/check, compileall,
  `uv sync`, both CLI help entrypoints and `git diff --check` passed. v21 retry-progress migration
  and v20 read-only observation compatibility are covered. No live Provider/model/Pi/MCP/network/
  credential test ran.

## Next action

Await explicit user direction. Do not start S7P-08 automatically.

## Subplan 88 evidence

- Schema v22 persists a bounded FIFO runtime-control queue. Queue consumption and durable Turn
  admission share one transaction and each entry owns an idempotent `client_message_id`.
- AgentLoop polls steering at loop top, before final STOP and after admitted-batch closure. An
  admitted tool batch is never interrupted; follow-ups drain only after normal STOP.
- Terminal Enter while streaming maps to steering and Alt+Enter maps to follow-up, matching the
  pinned Pi 0.84.2 mapping; Ctrl+C cancellation remains unchanged.
- The initial Luna Max review found three confirmed issues; `dd2090e` closed the ConversationLog
  and loop-top defects. Follow-up review found two P2 crash/replay gaps. `b4ec3e6` persists
  receipt-scoped terminal `turn_id` and ERROR stop code and emits the STEERED replay status event.
- Focused S7P-07/adjacent matrix passed `166 passed`; full offline passed
  `1297 passed, 2 deselected` in `84.82s`. `uv sync`, Ruff format/check, compileall, both CLI help
  entrypoints and `git diff --check` passed. No live or network test ran.
- Herschel (`gpt-5.6-luna`, reasoning `max`) returned
  `APPROVE — no confirmed P0-P3 findings` over `7b52f5f..b4ec3e6`; its final targeted checks passed
  `7 passed`, including a process-rebuild ERROR replay proof.

## Subplan 87 evidence

- Production no longer constructs `CompletionChecker`, issues OutcomeIntent requests, prepares
  workspace baselines, injects completion feedback or rejects a valid tool-free model `stop`.
- Removed active contract/baseline/verifier parameters from AgentLoop, AgentRuntime, Session
  persistence and Turn submission. PreparedAgentRunSpec no longer exposes completion evidence.
- Deleted the semantic resolver and runtime completion service. Validation facts remain independent
  execution telemetry and dynamic project-instruction write gates remain unchanged.
- Old AgentRunSnapshot fields and existing schema-v18/v19 columns remain read-compatible but are
  ignored and never populated by new runs; no destructive migration or state rewrite was added.
- Focused runtime/preparation/observability/migration matrix passed `154 passed`; final full offline
  regression passed `1254 passed, 2 skipped, 2 deselected in 63.16s`.
- Ruff format/check, compileall, both CLI help entrypoints and `git diff --check` passed.

## Blockers

- None. No dependency or live-provider authorization is required for the offline implementation.
