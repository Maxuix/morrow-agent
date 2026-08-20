# TODO

## Current stage

Stage 5 implementation is authorized; Subplan 50 implementation and its independent review-fix
pass are complete pending the fast-forward merge and Subplan 51 activation.

## Active subplan

Subplan 50 — Accepted Outcome to Candidate pipeline (closed; merge/activate Subplan 51 next).

## Tasks

- [x] S50.1 Add the application-level accepted Outcome hook and idempotent explicit Review request.
- [x] S50.2 Implement bounded Review claim/lease/retry/finalize lifecycle and typed errors.
- [x] S50.3 Extract safe Evidence and build the bounded LearningContext without full history/tools.
- [x] S50.4 Validate Fake Reviewer drafts, compute deterministic confidence/fingerprints, and persist
  only eligible Candidates.
- [x] S50.5 Implement duplicate/conflict/suppression/re-review behavior and evidence aggregation.
- [x] S50.6 Add one-shot foreground/headless runner and truthful composition/UI hints without a worker.
- [x] S50.7 Reconcile docs, run the complete Subplan 50 gate, commit verified work, complete the
  required Grok review-fix pass, and prepare Subplan 51 activation.

## Start condition

The branch `feat/stage5-review-pipeline` contains the verified S50 implementation and review fixes;
fast-forward it to local `main` before creating the S51 branch. Preserve the two
untracked `docs/research/stage5-overview-*.md` user files unless the user explicitly asks to adopt
or commit them.
