# Progress Tracker

## Current status

Stage 7 is complete and remediated (full offline gate: 1538 passed, 2 skipped, 2 Live deselected;
Ruff format/check, compileall and diff checks passed). Stage 8 planning is done: the stage-8
roadmap was revised on 2026-09-03 (risk-tiered Replan autonomy aligned with mainstream harness
practice; runtime-kernel-first ordering), the Stage 7 subplans are archived under
`.agent/archive/subplans/stage7-workflow-runtime/`, and the Stage 8 master plan plus eleven child
plans are drafted under `.agent/`.

## Active task

Stage 8 Subplan 2 implementation is complete and locally integrated: exact
FutureGraphPatch validation, detached run-local Revisions, atomic continuation handoff, execution
sets, Artifact imports, effective-output resolution, lineage budget/deadline enforcement, rerun
semantics and CLI/query visibility. Full offline gate: 1560 passed / 2 deselected; all static gates
green. Only remote publication closeout remains.

## Next action

Subplan 3 starts only on explicit activation and additionally needs authorization for the additive
`ApplicationEvent` lifecycle extension and Python web-framework dependency; Subplan 4 needs
frontend toolchain authorization.

## Blockers

Remote publication is blocked: the safety approval rejected `git push origin main` because the
current request was not accepted as explicit authorization to send these commits to the configured
GitHub destination. Local `main` is three closeout commits ahead after recording this blocker.
Additive `ApplicationEvent` types remain deferred pending separate Subplan 3 authorization;
Query/CLI polling remains the complete path.
