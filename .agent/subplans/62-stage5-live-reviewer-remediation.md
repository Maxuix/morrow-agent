# Subplan 62 — Stage 5 Live Reviewer Quality Remediation

> Status: active
> Branch: `fix/stage5-live-reviewer-quality`
> Baseline: `3493c88` on local `main`

## Evidence and objective

The first complete Preference v2 live corpus on 2026-08-23 produced `8/12` correct positive
operations, `10/16` proposal precision (`0.625`), `7/8` correct targets (`0.875`), zero safety
negative Active writes, and `10/10` next-AgentRun adherence. The frozen aggregate report contains
no user text, raw model output, reasoning, or credential. Stage 5 acceptance remains open.

Improve the no-tool semantic Reviewer to meet the frozen live thresholds without changing Writer,
Inbox, YAML authority, Review durability, capability policy, or public event contracts.

## Tasks

1. Extend the opt-in report with bounded per-case diagnostics: case ID, expected/actual operation
   signatures, operation count, and match status only. Never record statements or raw output.
2. Reproduce prompt/schema behavior with scripted Providers and identify the smallest semantic
   instruction or context-shape defect responsible for missed/extra operations.
3. Improve the Reviewer prompt/schema contract without keyword classification, automatic writes,
   extra Provider calls, or a new dependency.
4. Run focused Reviewer/evaluation/worker tests and the complete non-live repository gate.
5. Run the frozen real-Provider corpus once after the fix and record sanitized aggregate evidence.
6. Run exactly one `$grok-delegate` review of this subplan, independently adjudicate/fix confirmed
   findings without a second review, rerun gates, and merge only verified work.

## Acceptance

- positive operation recall at least `11/12`;
- proposal precision at least `0.90`;
- replace/remove target accuracy at least `6/7`;
- safety-negative Active writes exactly `0`;
- next-AgentRun adherence at least `9/10`;
- credentials remain Keychain-only and all reports remain bounded and sanitized.
