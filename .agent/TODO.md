# TODO

## Current stage

Stage 5 implementation is authorized; Subplan 52 implementation is complete on its dedicated
branch and awaits the required one-time Grok review-fix pass.

## Active subplan

Subplan 52 — Profile/Preferences Promotion Saga.

## Tasks

- [x] S52.1 Publish the bounded prepared configuration API, exact revision/digest apply semantics,
  and truthful Session/source-revision projections.
- [x] S52.2 Add v11 PromotionOperation/ConfigurationActivation repositories and the SQLite A/YAML/
  SQLite promotion Saga with replay-safe finalization.
- [x] S52.3 Add Preference/Profile whitelist, explicit evidence/scope checks, activation provenance,
  and CLI/REPL preview-confirmation routing.
- [x] S52.4 Add foreground recovery actions, drift/unknown-state handling, and crash/replay tests.
- [x] S52.5 Add safe inverse previews, activation reversal provenance, and stale-current refusal.
- [>] S52.6 Run the required Grok review-fix pass, reconcile docs, commit the closeout, and prepare
  Subplan 53 activation.

## Validation evidence

The full offline gate currently passes: 749 passed, 2 skipped, 1 deselected. Ruff format/check,
compileall, root/Learning/Memory CLI help, and `git diff --check` also pass. The two nested macOS
Seatbelt tests remain skipped by the sandbox.

## Start condition

Subplan 51 is merged into local `main`; Subplan 52 is implemented on `feat/stage5-config-promotion`.
Preserve the two untracked `docs/research/stage5-overview-*.md` user files unless the user explicitly
asks to adopt or commit them.
