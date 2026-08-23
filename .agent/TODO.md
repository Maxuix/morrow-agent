# TODO

## Current stage

Stage 5 Preference v2 live Reviewer quality remediation.

## Active subplan

Subplan 62 on `fix/stage5-live-reviewer-quality`.

## Tasks

- `[x]` Persist the compatible Provider credential in macOS Keychain and verify connectivity without
  exposing the secret.
- `[x]` Repair the stale live adherence harness and add a non-live regression test.
- `[x]` Run the complete frozen live corpus and record its bounded failing aggregate score.
- `[x]` Add sanitized per-case diagnostics and identify the Reviewer semantic failure modes.
- `[x]` Improve the no-tool Reviewer prompt/schema contract and add offline regressions.
- `[x]` Run focused and complete non-live gates.
- `[>]` Replay the frozen live corpus once and record bounded evidence.
- `[ ]` Run the one Subplan 62 Grok review/fix cycle, rerun gates, merge, and retire the branch.

## Boundaries

- Preserve Writer, Inbox, YAML authority, durable Review, ContextBuilder, capability policy, and
  public event contracts.
- Do not persist credentials, raw Provider output, reasoning, statements, or user text in reports.
- Do not replace semantic Review with keyword classification or add Provider calls.
- Preserve the two untracked user research files.
