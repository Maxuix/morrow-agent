# TODO

## Current stage

Subplan 57 — Atomic Preference Writer and Direct Lifecycle.

## Active subplan

`feat/stage5-preference-writer`

## Tasks

- `[x]` S57.1 Implement prepare/apply/finalize for one same-scope YAML-authoritative Writer batch.
- `[x]` S57.2 Implement crash recovery, drift detection, retry, and visible resolution states.
- `[x]` S57.3 Add generic enable/disable lifecycle and bounded Preference query projections.
- `[x]` S57.4 Add approved direct management tool, CLI/REPL surfaces, and Session-local projection reset.
- `[x]` S57.5 Translate legacy Preference Candidates through the generic Writer without mutating history.
- `[x]` S57.6 Activate first-write migration, full-aggregate preservation, and legacy AgentRun decoding.
- `[x]` Run focused S57 validation and implementation checkpoint.
- `[x]` Invoke exactly one Grok `/review`; the user interrupted before its report returned, so no
  Grok finding was accepted and the independent local review/fix pass was completed instead.
- `[ ]` Commit closeout, fast-forward `main`, retire the branch, and activate S58.

## Start condition

- Start from the latest verified local `main` on `feat/stage5-preference-writer`; S56 is merged at
  `fdce537` and v13 DDL/checksum is frozen.
- Preserve the untracked user files `docs/research/stage5-overview-pipeline.md` and
  `docs/research/stage5-overview-review.md`; do not stage or modify them without explicit request.
- Do not run a real Provider or access credentials during implementation subplans. The authorized
  isolated real-Provider evaluation occurs only after S56–S61, all required reviews, the integrated
  final review/fix, a passing full offline gate, and a clean implementation commit.

## Required execution discipline

Use one logical S57 task at a time on this dedicated branch. Keep v13 migration statements immutable,
run focused tests after each behavior change, commit one verified implementation checkpoint, perform
exactly one `$grok-delegate` `/review`, independently fix confirmed/valuable findings without a
second review, run final gates, commit closeout, fast-forward merge, retire the branch, and activate
S58.
