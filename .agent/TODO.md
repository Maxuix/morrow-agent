# TODO

## Current stage

Subplan 60 — Fresh Preference Context and Observability.

## Active subplan

`fix/stage5-preference-context-refresh`

## Tasks

- `[x]` S60.1 Reload current Preference documents at each new AgentRun admission.
- `[x]` S60.2 Persist and recover the exact frozen per-run Preference projection.
- `[x]` S60.3 Add deterministic bounded lower-authority context rendering and safety tests.
- `[x]` S60.4 Add typed Preference refresh/omission status and doctor diagnostics.
- `[x]` Run the focused S60 validation and implementation checkpoint.
- `[x]` Run exactly one Grok `/review`, independently adjudicate findings, and rerun affected gates.
- `[>]` Commit closeout, fast-forward `main`, retire the branch, and activate S61.

## Start condition

- Start from verified local `main` at `f855c64` after S59 merged and its final offline gates passed;
  v13 Review persistence, S57 Writer authority, and S58 Reviewer/Inbox contracts are frozen.
- Preserve the untracked user files `docs/research/stage5-overview-pipeline.md` and
  `docs/research/stage5-overview-review.md`; do not stage or modify them without explicit request.
- Do not run a real Provider or access credentials during implementation subplans. Use injected
  scripted Providers/Reviewers, clocks, futures, and schedulers.

## Required execution discipline

Use one logical S60 task at a time on its dedicated branch. Reload Preferences only at a new
AgentRun boundary, preserve same-Run freeze/recovery, keep Preference injection distinct from
Project Knowledge, and do not change capability authority. Run focused tests after each behavior
change, commit one verified implementation checkpoint, perform exactly one `$grok-delegate`
`/review`, independently fix confirmed/valuable findings without a second review, run final gates,
commit closeout, fast-forward merge, retire the branch, and activate S61.
