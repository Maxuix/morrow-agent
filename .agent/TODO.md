# TODO

## Current stage

Stage 5 implementation is authorized; Subplan 52 is next on a dedicated branch from verified
local `main` after Subplan 51.

## Active subplan

Subplan 52 — Profile/Preferences Promotion Saga.

## Tasks

- [x] S51.1 Add v11 decision/Knowledge/memory-state models, ports, migration, codecs, repositories,
  and upgrade/corruption/constraint tests.
- [x] S51.2 Add bounded Review/Candidate/Knowledge query and pure decision-preview services.
- [x] S51.3 Add immutable candidate decisions, rejection/suppression, expiry, receipts, events,
  replay, and concurrency behavior.
- [x] S51.4 Add Project Knowledge promotion, conflict resolution, evidence provenance, and
  acknowledged-only acceptance for future candidate types.
- [x] S51.5 Add Knowledge list/show/timeline and disable/enable/dispute/delete lifecycle.
- [x] S51.6 Add `/learn`/`/memory` REPL and Typer surfaces with preview/confirmation and no direct
  adapter imports.
- [x] S51.7 Reconcile docs, run the complete Subplan 51 gate, commit, complete the required Grok
  review-fix pass, and prepare Subplan 52 activation.

## Start condition

Subplan 51 is merged into local `main`; create the Subplan 52 branch from its verified tip.
Preserve the two untracked `docs/research/stage5-overview-*.md` user files unless the user explicitly
asks to adopt or commit them.
