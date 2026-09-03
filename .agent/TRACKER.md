# Progress Tracker

## Current status

Stage 7 is complete and remediated (full offline gate: 1538 passed, 2 skipped, 2 Live deselected;
Ruff format/check, compileall and diff checks passed). Stage 8 planning is done: the stage-8
roadmap was revised on 2026-09-03 (risk-tiered Replan autonomy aligned with mainstream harness
practice; runtime-kernel-first ordering), the Stage 7 subplans are archived under
`.agent/archive/subplans/stage7-workflow-runtime/`, and the Stage 8 master plan plus eleven child
plans are drafted under `.agent/`.

## Active task

Stage 8 Subplan 2 review remediation is active on `fix/stage8-continuation-review`. Four confirmed
bugs are repaired: detached Revision number isolation, multi-hop Past derivation, inherited
ReviewReport empty-set finalization, and atomic idle RUNNING pause completion. Two useful review
suggestions are also implemented for admission-reason normalization and Query effective-output
projection. Review-focused matrix: 56 passed; full offline gate: 1566 passed / 2 deselected in
283.88 seconds; static gates and CLI help smoke are green. Only local Git integration remains.

## Next action

Run the full offline gate, record final evidence, commit the remediation and fast-forward it into
local `main`. Subplan 3 starts only on explicit activation and additionally needs authorization for the additive
`ApplicationEvent` lifecycle extension and Python web-framework dependency; Subplan 4 needs
frontend toolchain authorization.

## Blockers

Remote publication is blocked: the safety approval rejected `git push origin main` because the
current request was not accepted as explicit authorization to send these commits to the configured
GitHub destination. Local `main` is three closeout commits ahead after recording this blocker.
Additive `ApplicationEvent` types remain deferred pending separate Subplan 3 authorization;
Query/CLI polling remains the complete path.
