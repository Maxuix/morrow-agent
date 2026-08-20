# TODO

## Current stage

Stage 5 implementation is authorized and fully planned. No production implementation task has
started yet.

## Active subplan

Subplan 49 — Learning domain and v10 persistence (ready to start).

## Tasks

- [ ] S49.1 Record the accepted Stage 5 governance/authority decision and add architecture guards
  for TaskService, AgentLoop, Reviewer, Promotion, YAML, and SQLite ownership.
- [ ] S49.2 Implement bounded Core Learning models, discriminated Candidate payloads, policy,
  Evidence authority, suppression, fingerprints, confidence basis, and safety budgets.
- [ ] S49.3 Define narrow Learning/Knowledge/Selection journal ports and the asynchronous no-tool
  `LearningReviewerPort`; add deterministic test builders/Fake Reviewer outside production
  composition.
- [ ] S49.4 Reserve and implement Operational Store v10 for LearningPolicy, Review, Evidence,
  Candidate links, and suppressions, including v9 upgrade/future-schema/rollback coverage.
- [ ] S49.5 Implement bounded v10 SQLite codecs/repositories over the shared journal transaction
  backend, with workspace isolation, row-version, uniqueness, and corruption classification tests.
- [ ] S49.6 Implement the workspace LearningPolicy command/query foundation with default
  `review_only`, public `off | review_only`, optimistic concurrency, receipts, and sanitized events.
- [ ] S49.7 Reconcile foundation docs, run the complete Subplan 49 gate, commit verified work,
  fast-forward it to `main`, retire the branch, and prepare Subplan 50 without starting it early.

## Start condition

Create `feat/stage5-learning-foundation` from the latest verified `main`, then mark only S49.1
`[>]`. Preserve the two untracked `docs/research/stage5-overview-*.md` user files unless the user
explicitly asks to adopt or commit them.
