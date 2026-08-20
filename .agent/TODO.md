# TODO

## Current stage

Stage 5 implementation is authorized; Subplan 50 implementation is complete pending its final gate
and independent review.

## Active subplan

Subplan 50 — Accepted Outcome to Candidate pipeline (closeout).

## Tasks

- [x] S50.1 Add the application-level accepted Outcome hook and idempotent explicit Review request.
- [x] S50.2 Implement bounded Review claim/lease/retry/finalize lifecycle and typed errors.
- [x] S50.3 Extract safe Evidence and build the bounded LearningContext without full history/tools.
- [x] S50.4 Validate Fake Reviewer drafts, compute deterministic confidence/fingerprints, and persist
  only eligible Candidates.
- [x] S50.5 Implement duplicate/conflict/suppression/re-review behavior and evidence aggregation.
- [x] S50.6 Add one-shot foreground/headless runner and truthful composition/UI hints without a worker.
- [>] S50.7 Reconcile docs, run the complete Subplan 50 gate, commit verified work, fast-forward it
  to `main`, retire the branch, and prepare Subplan 51.

## Start condition

The branch `feat/stage5-review-pipeline` is active; S50.7 is in progress. Preserve the two
untracked `docs/research/stage5-overview-*.md` user files unless the user explicitly asks to adopt
or commit them.
