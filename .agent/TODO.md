# TODO

## Current stage

Subplan 59 — Durable Asynchronous Review Worker.

## Active subplan

`feat/stage5-review-worker`

## Tasks

- `[x]` S59.1 Add the atomic terminal-Turn Preference Review enqueue hook.
- `[x]` S59.2 Add the lease-based process-local Review Worker lifecycle.
- `[>]` S59.3 Add bounded retry, timeout, model fallback, and sanitized terminal failure handling.
- `[ ]` S59.4 Move accepted-Task Learning Review execution off the foreground path while preserving
  legacy non-Preference behavior.
- `[ ]` S59.5 Add truthful job/run-pending status and non-blocking notification surfaces.
- `[x]` Run the focused S59 validation and implementation checkpoint.
- `[ ]` Run exactly one Grok `/review`, independently adjudicate findings, and rerun the affected gates.
- `[ ]` Commit closeout, fast-forward `main`, retire the branch, and activate S60.

## Start condition

- Start from the verified local `main` after S58 merged and its final offline gates passed; v13
  DDL/checksum, S57 Writer authority, and S58 Reviewer/Inbox contracts are frozen.
- Preserve the untracked user files `docs/research/stage5-overview-pipeline.md` and
  `docs/research/stage5-overview-review.md`; do not stage or modify them without explicit request.
- Do not run a real Provider or access credentials during implementation subplans. Use injected
  scripted Providers/Reviewers, clocks, futures, and schedulers.

## Required execution discipline

Use one logical S59 task at a time on its dedicated branch. Keep ConversationLog as the only history
writer, keep PreferenceReviewJob as the SQLite queue authority, and do not add a daemon or external
scheduler. Run focused tests after each behavior change, commit one verified implementation
checkpoint, perform exactly one `$grok-delegate` `/review`, independently fix confirmed/valuable
findings without a second review, run final gates, commit closeout, fast-forward merge, retire the
branch, and activate S60.
