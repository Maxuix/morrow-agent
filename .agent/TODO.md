# TODO

## Current stage

Stage 5 implementation is authorized; Subplan 54 is active on `feat/stage5-reviewer-acceptance`,
based on verified local `main` at `613ffdb`.

## Active subplan

Subplan 54 — Production Reviewer, Evaluation, and Stage 5 Acceptance.

## Tasks

- [x] S54.1 Implement the bounded no-tool production Reviewer adapter and safe composition.
- [x] S54.2 Complete Learning policy, status, review/retry, and foreground UX surfaces.
- [x] S54.3 Validate future candidate-only behavior without activating Skills or Workflows.
- [x] S54.4 Build the versioned adversarial offline evaluation dataset and quality report.
- [x] S54.5 Complete Stage 5 doctor, backup, end-to-end acceptance, and documentation evidence.
- [>] S54.6 Record the live evaluation hold point; run it only with explicit authorization.
- [ ] S54.7 Run the final independent review/fix pass, gates, merge, and Stage 5 closeout.

## Validation evidence

S52's final gate passed: 752 passed, 2 skipped, 1 deselected. S53.1 validation passed: 83 focused
tests, Ruff format/check, compileall, and diff check. S53.2 validation passed: 760 passed, 2
skipped, 1 deselected, repository-wide Ruff format/check, compileall, and diff check. S53.3
validation passed: 763 passed, 2 skipped, 1 deselected, with the same quality gates. S53.4
validation passed: 767 passed, 2 skipped, 1 deselected; Ruff format/check, compileall, root/Learning/
Memory CLI help, and diff check also passed. S53.5 validation passed: 771 tests, 2 skips, 1
deselected; the same quality gates passed. S53.6 validation passed: 777 tests, 2 skips, 1
deselected; repository-wide Ruff format/check, compileall, root/Learning/Memory CLI help, and
diff check passed. S53.7 review-fix validation passed 785 tests, 2 skips, and 1 deselected; Ruff
format/check, compileall, root/Learning/Memory CLI help, and diff check passed. S53 was committed,
fast-forward merged into local `main` at `613ffdb`, and its topic branch was retired. S54 is now
active.

## Start condition

Subplan 53 is fast-forward merged into local `main` at `613ffdb`; Subplan 54 is on its dedicated
branch. Preserve the two untracked `docs/research/stage5-overview-*.md` user files unless the user
explicitly asks to adopt or commit them.

S54.1 validation passed: 790 tests, 2 skips, 1 deselected; Ruff format/check, compileall, root/
Learning/Memory CLI help, and diff check passed. The production Reviewer checkpoint is committed
as `c955bbc`. S54.2 validation passed: 792 tests, 2 skips, 1 deselected; the same quality and CLI
gates passed. The S54.2 checkpoint is `9ef6f01`. S54.3 validation passed: 794 tests, 2 skips, 1
deselected; the same quality and CLI gates passed. Checkpoint `4ff41a4`. S54.4 validation passed:
10 focused tests; the complete offline gate passed 804 tests, 2 skips, and 1 deselected. Ruff
format/check, compileall, root/Learning/Memory CLI help, and `git diff --check` passed. Checkpoint
`6f77940`; S54.5 is now active.

S54.5 validation passed: the doctor/backup acceptance set passed 23 tests; the complete offline gate
passed 808 tests, 2 skips, and 1 deselected. Ruff format/check, compileall, root/Learning/Memory CLI
help, and `git diff --check` passed. Learning doctor checks are split by Review/Evidence,
Candidate/Suppression, and Promotion/Knowledge domains; acceptance evidence is in
`docs/acceptance/stage5-acceptance.md`. S54.6 is now active; the Live hold is pending because no
explicit Live authorization or compatible credential was supplied.
