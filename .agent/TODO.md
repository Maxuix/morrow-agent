# TODO

## Current stage

Subplan 58 — Semantic Preference Reviewer and Inbox.

## Active subplan

`feat/stage5-preference-reviewer`

## Tasks

- `[x]` S58.1 Build the frozen bounded Review context and persist reproducible source evidence.
- `[x]` S58.2 Add the minimal no-tool Preference Reviewer and deterministic scripted test double.
- `[x]` S58.3 Validate and persist independent Preference proposals with exact OCC/safety rules.
- `[x]` S58.4 Add Preference Inbox list/show/preview and accept/edit/reject decision flows through S57.
- `[x]` S58.5 Disable new fixed-field Preference emission from the broad legacy Reviewer while preserving
  non-Preference and historical behavior.
- `[>]` Run the focused S58 validation and implementation checkpoint.
- `[ ]` Run exactly one Grok `/review`, independently adjudicate findings, and rerun the affected gates.
- `[ ]` Commit closeout, fast-forward `main`, retire the branch, and activate S59.

## Start condition

- Start from the latest verified local `main` after S57 merged and its offline gates passed; v13
  DDL/checksum and S57 Writer authority are frozen.
- Preserve the untracked user files `docs/research/stage5-overview-pipeline.md` and
  `docs/research/stage5-overview-review.md`; do not stage or modify them without explicit request.
- Do not run a real Provider or access credentials during implementation subplans. Use scripted
  Reviewer/Provider doubles for offline validation.

## Required execution discipline

Use one logical S58 task at a time on its dedicated branch. Keep S57 Writer authority and v13 DDL
immutable, run focused tests after each behavior change, commit one verified implementation
checkpoint, perform exactly one `$grok-delegate` `/review`, independently fix confirmed/valuable
findings without a second review, run final gates, commit closeout, fast-forward merge, retire the
branch, and activate S59.
