# TODO

## Current stage

Stage 5 implementation is authorized; Subplan 51 is active on its dedicated branch from verified
local `main` at `4d47be8`.

## Active subplan

Subplan 51 — Learning Inbox, Decisions, and Project Knowledge.

## Tasks

- [x] S51.1 Add v11 decision/Knowledge/memory-state models, ports, migration, codecs, repositories,
  and upgrade/corruption/constraint tests.
- [>] S51.2 Add bounded Review/Candidate/Knowledge query and pure decision-preview services.
- [ ] S51.3 Add immutable candidate decisions, rejection/suppression, expiry, receipts, events,
  replay, and concurrency behavior.
- [ ] S51.4 Add Project Knowledge promotion, conflict resolution, evidence provenance, and
  acknowledged-only acceptance for future candidate types.
- [ ] S51.5 Add Knowledge list/show/timeline and disable/enable/dispute/delete lifecycle.
- [ ] S51.6 Add `/learn`/`/memory` REPL and Typer surfaces with preview/confirmation and no direct
  adapter imports.
- [ ] S51.7 Reconcile docs, run the complete Subplan 51 gate, commit, complete the required Grok
  review-fix pass, and prepare Subplan 52 activation.

## Start condition

The branch `feat/stage5-inbox-knowledge` is active from verified local `main` at `4d47be8`.
Preserve the two untracked `docs/research/stage5-overview-*.md` user files unless the user explicitly
asks to adopt or commit them.
