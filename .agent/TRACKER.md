# Progress Tracker

## Current status

Stage 7 is complete and remediated (full offline gate: 1538 passed, 2 skipped, 2 Live deselected;
Ruff format/check, compileall and diff checks passed). Stage 8 planning is done: the stage-8
roadmap was revised on 2026-09-03 (risk-tiered Replan autonomy aligned with mainstream harness
practice; runtime-kernel-first ordering), the Stage 7 subplans are archived under
`.agent/archive/subplans/stage7-workflow-runtime/`, and the Stage 8 master plan plus eleven child
plans are drafted under `.agent/`.

## Active task

Subplan 1 (`1-pause-drain-runtime`) on branch `feat/stage8-pause-drain`: Pause/Drain/Resume
runtime — migration framework generalization, schema v26, core model states, single admission
transaction, Pause/Resume semantics, CLI commands, deterministic tests.

## Next action

Study the contracts doc and relevant source (migrations in `operational.py`,
`core/workflows/runs.py`, `workflow_journal.py`, scheduler admission path), then implement the
migration framework generalization and schema v26.

## Blockers

No local implementation blocker. Additive `ApplicationEvent` types remain deferred pending
explicit authorization (now scheduled as a Subplan 3 precondition; Query/CLI polling remains the
complete path). Remote publication was authorized by the current user request and verified after
the push: `main` and `origin/main` both point to `32d5964` with no commits ahead or behind.
