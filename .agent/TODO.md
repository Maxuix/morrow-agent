# TODO

## Current stage

Subplan 56 — Generic Preference Foundation and Migrations.

## Active subplan

`feat/stage5-preference-foundation`

## Tasks

- `[x]` S56.1 Add generic Preference domain contracts and the pure same-scope batch reducer.
- `[x]` S56.2 Add versioned YAML codecs, deterministic legacy migration, and compatibility decoders.
- `[x]` S56.3 Add and register the frozen Operational Store v13 Preference schema and ports.
- `[x]` S56.4 Add compatibility fixtures, migration matrix evidence, and foundation documentation.
- `[x]` Run the focused S56 validation and implementation checkpoint.
- `[x]` Run exactly one Grok review, independently apply confirmed fixes, and rerun gates.
- `[>]` Commit closeout, fast-forward `main`, retire the branch, and activate S57.

## Start condition

- Start from the latest verified local `main` on `feat/stage5-preference-foundation`.
- Preserve the untracked user files `docs/research/stage5-overview-pipeline.md` and
  `docs/research/stage5-overview-review.md`; do not stage or modify them without explicit request.
- Run the baseline non-live gate before production changes and record any pre-existing failure.
- Do not run a real Provider or access credentials during implementation subplans. The already
  authorized isolated real-Provider evaluation occurs only after S56–S61, all required Grok reviews,
  the integrated final review/fix, a passing full offline gate, and a clean implementation commit.

## Required execution discipline

Each S56–S61 subplan uses its dedicated branch, focused tests, one implementation checkpoint, exactly
one `$grok-delegate` `/review`, one independent adjudication/fix pass without re-review, final gates,
closeout commit, fast-forward merge, and clean branch retirement.
