# TODO

## Current stage

Stage 5 automated offline gates are complete, but the simulated-user evaluation at `5cfb99f`
confirmed two P1 blockers and one P2 preview defect. A bounded remediation plan now exists; no
production fix has started. The optional Live model-quality hold remains pending separately.

## Active subplan

None. Proposed Subplan 55 — Stage 5 Simulated-User Remediation — awaits an implementation request.

## Tasks

No implementation task is active. On authorization, copy S55.1–S55.5 from
`.agent/subplans/55-stage5-simulated-user-remediation.md` into this active-task list and start only
S55.1.

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
fast-forward merged into local `main` at `613ffdb`, and its topic branch was retired. S54 was then
activated.

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
`6f77940`; S54.5 was then activated.

S54.5 validation passed: the doctor/backup acceptance set passed 23 tests; the complete offline gate
passed 808 tests, 2 skips, and 1 deselected. Ruff format/check, compileall, root/Learning/Memory CLI
help, and `git diff --check` passed. Learning doctor checks are split by Review/Evidence,
Candidate/Suppression, and Promotion/Knowledge domains; acceptance evidence is in
`docs/acceptance/stage5-acceptance.md`. S54.6 was then activated; the Live hold is pending because no
explicit Live authorization or compatible credential was supplied.

S54.6 hold-point evidence is recorded in `docs/acceptance/stage5-live-evaluation-hold.md` and linked
from the Stage 5 acceptance report. No live Provider, network request, or `pytest -m live` execution
was attempted; real-model quality targets remain pending. S54.7 then completed the required single
Grok review/fix pass and final gates. Grok reported one confirmed CLI failure-status bug, six
suggestions, and one nit. The confirmed bug and feasible suggestions were independently verified
and fixed once; no second Grok review was run. The final non-live gate passed `816 passed, 2 skipped,
2 deselected`; Ruff format/check, compileall, root/Learning/Memory CLI help, and `git diff --check`
also passed. The final evaluator report is 27/27 with 5 safety-negative cases; the pure evaluator
performs zero writes, and the scripted real-runner safety integration observes zero Candidate,
Knowledge, or Memory Active writes.

The later isolated simulated-user evaluation committed at `5cfb99f` supersedes the user-usability
claim without invalidating those deterministic safety results. It reproduced headless Candidate
accept/reject failure, first Project Knowledge Promotion rollback under a non-zero microsecond
clock, and an incorrect reject preview kind. Static adjudication also confirmed that headless edit
shares the typed-view bug and that Project Knowledge's dedicated edit guard is inverted.

## Proposed Subplan 55 start condition

- Branch from local `main` at `5cfb99f` as `fix/stage5-simulated-user-remediation`.
- Start with failing regression tests; do not modify production code before S55.1 evidence exists.
- Preserve the two untracked `docs/research/stage5-overview-*.md` user files.
- Do not run Live Provider/network tests or access credentials without a separate explicit request.
