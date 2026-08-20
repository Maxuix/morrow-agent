# TODO

## Current stage

Stage 5 implementation is authorized; Subplan 53 is active on `feat/stage5-memory-selection`,
based on verified local `main` at `7dfe5af`.

## Active subplan

Subplan 53 — MemorySelection and ContextBuilder Integration.

## Tasks

- [x] S53.1 Implement v12 MemorySelection/Item/query models, ports, migration, repository, and
  upgrade/future/corruption tests.
- [x] S53.2 Implement deterministic lexical token projection and rebuildable Knowledge terms.
- [x] S53.3 Implement bounded deterministic selection, ranking, diversity, reasons, and digests.
- [x] S53.4 Freeze selection and effective configuration into new/recovery AgentRun admission.
- [x] S53.5 Make ContextBuilder consume the frozen RunContextProjection for every cycle.
- [>] S53.6 Add selection inspection, doctor/backup invariants, CLI/REPL surfaces, and docs.
- [ ] S53.7 Run focused/full gates, merge verified work, and prepare Subplan 54.

## Validation evidence

S52's final gate passed: 752 passed, 2 skipped, 1 deselected. S53.1 validation passed: 83 focused
tests, Ruff format/check, compileall, and diff check. S53.2 validation passed: 760 passed, 2
skipped, 1 deselected, repository-wide Ruff format/check, compileall, and diff check. S53.3
validation passed: 763 passed, 2 skipped, 1 deselected, with the same quality gates. S53.4
validation passed: 767 passed, 2 skipped, 1 deselected; Ruff format/check, compileall, root/Learning/
Memory CLI help, and diff check also passed. S53.5 validation passed: 771 tests, 2 skips, 1
deselected; the same quality gates passed. S53.6 is now active.

## Start condition

Subplan 52 is fast-forward merged into local `main` at `7dfe5af`; Subplan 53 is on its dedicated
branch. Preserve the two untracked `docs/research/stage5-overview-*.md` user files unless the user
explicitly asks to adopt or commit them.
