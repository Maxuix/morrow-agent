# TODO

## Current stage

Stage 5 complete and accepted.

## Active subplan

None.

## Tasks

- `[x]` Persist the compatible Provider credential in macOS Keychain and verify connectivity without
  exposing the secret.
- `[x]` Repair the stale live adherence harness and add a non-live regression test.
- `[x]` Run the complete frozen live corpus and record its bounded failing aggregate score.
- `[x]` Add sanitized per-case diagnostics and identify the Reviewer semantic failure modes.
- `[x]` Improve the no-tool Reviewer prompt/schema contract and add offline regressions.
- `[x]` Run focused and complete non-live gates.
- `[x]` Replay Reviewer v3 once and record its bounded failing evidence.
- `[x]` Apply the three evidence-driven Reviewer v4 semantic corrections and run non-live gates.
- `[x]` Run the one Subplan 62 Grok review; do not run a second review.
- `[x]` Independently apply confirmed report-contract, sanitization-test, anti-overfit-test, and
  per-case error-containment suggestions, then rerun non-live gates.
- `[x]` Run one reviewed final live replay and record its passing bounded evidence.
- `[x]` Run final non-live and repository quality gates.
- `[x]` Commit evidence, fast-forward merge, and retire the branch.

## Boundaries

- Preserve Writer, Inbox, YAML authority, durable Review, ContextBuilder, capability policy, and
  public event contracts.
- Do not persist credentials, raw Provider output, reasoning, statements, or user text in reports.
- Do not replace semantic Review with keyword classification or add Provider calls.
- Preserve the two untracked user research files.
