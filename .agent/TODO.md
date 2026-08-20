# TODO

## Current stage

Stage 5 implementation is authorized; Subplan 53 is active on `feat/stage5-memory-selection`,
based on verified local `main` at `7dfe5af`.

## Active subplan

Subplan 53 — MemorySelection and ContextBuilder Integration.

## Tasks

- [>] S53.1 Implement v12 MemorySelection/Item/query models, ports, migration, repository, and
  upgrade/future/corruption tests.
- [ ] S53.2 Implement deterministic lexical token projection and rebuildable Knowledge terms.
- [ ] S53.3 Implement bounded deterministic selection, ranking, diversity, reasons, and digests.
- [ ] S53.4 Freeze selection and effective configuration into new/recovery AgentRun admission.
- [ ] S53.5 Make ContextBuilder consume the frozen RunContextProjection for every cycle.
- [ ] S53.6 Add selection inspection, doctor/backup invariants, CLI/REPL surfaces, and docs.
- [ ] S53.7 Run focused/full gates, merge verified work, and prepare Subplan 54.

## Validation evidence

S52's final gate passed: 752 passed, 2 skipped, 1 deselected. S53 validation is pending.

## Start condition

Subplan 52 is fast-forward merged into local `main` at `7dfe5af`; Subplan 53 is on its dedicated
branch. Preserve the two untracked `docs/research/stage5-overview-*.md` user files unless the user
explicitly asks to adopt or commit them.
