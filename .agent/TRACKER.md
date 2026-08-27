# Progress Tracker

## Current status

Subplan 87 is committed at `63236ac` and fast-forward integrated into local `main`. Subplan 85 is
now active on `codex/feat/s7p-06-pi-parity`, based at `6a43be5`, and follows pinned Pi Agent 0.84.2
behavior: no default cumulative task caps or no-progress kill, token-window LLM compaction, Pi-style
retry/truncation, v1 resume compatibility and an external-only evaluation watchdog. Phase A is
freezing executable reference evidence and the Morrow compatibility map; the three research
documents remain untouched until the documented checklist-update phase.

## Active task

S7P-06 implementation is active; Phase A is the current task on the dedicated topic branch.

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

## Next action

Complete the pinned parity table, scripted black-box fixtures and v1/v2 field inventory, then add
the first failing policy/loop tests before production behavior changes.

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
