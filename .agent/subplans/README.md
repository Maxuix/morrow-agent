# Stage 7 current-plan subplans

This directory contains only child plans owned by the active Stage 7 master plan. Production
implementation is active; Subplans 1–2 are completed and integrated. Subplan 3 is ready but not
active, and all later children are pending. The sequence
was revised on 2026-08-31 per the conditional-GO plan review
(`docs/acceptance/stage-7-plan-review-revision-2026-08-31.md`); the superseded children are archived
under `.agent/archive/subplans/stage7-workflow-runtime-v1/`.

| Order | File | Status |
|---|---|---|
| 1 | `1-stage7-agent-definition-foundation.md` | completed and integrated |
| 2 | `2-stage7-workflow-revision-artifacts.md` | completed and integrated |
| 3 | `3-stage7-workflow-compiler.md` | ready; not active |
| 4 | `4-stage7-isolated-workflow-slice.md` | pending Subplan 3 |
| 5 | `5-stage7-serial-dag-scheduler.md` | pending Subplan 4 |
| 6 | `6-stage7-multi-agent-pipeline.md` | pending Subplan 5 |
| 7 | `7-stage7-direct-adapter.md` | pending Subplan 6 |
| 8 | `8-stage7-management-templates.md` | pending Subplan 7 |
| 9 | `9-stage7-acceptance-closeout.md` | pending Subplan 8 |

## Lifecycle

- Keep only this `README.md` and child plans owned by the current master plan here.
- Number the first child plan of every new master plan `1-<slug>.md`, then continue contiguously.
- Do not inherit sequence numbers from an earlier master plan.
- Before replacing `.agent/PLAN.md`, move all numbered files here into a plan-specific directory
  under `.agent/archive/subplans/` and preserve their names and contents.
- Never reactivate or renumber an archived child plan. Use Git history and the archive for recovery.
- Keep at most one child plan active at a time; record its state in `.agent/TODO.md` and
  `.agent/TRACKER.md`.
- Start every production child from the latest verified `main`; do not implement later interfaces
  or schemas early.
- A child closes only after its declared validation, coherent commits,
  fast-forward integration, ancestry verification and clean branch/worktree retirement.
- New hard gates must meet the proportionality test in `.agent/PLAN.md`; quality, cost and temporary
  availability facts must not be promoted into safety blockers.

The retired global sequence 36–100 is archived under
`.agent/archive/subplans/legacy-sequence-36-100/`, and the superseded first Stage 7 nine-subplan
revision is archived under `.agent/archive/subplans/stage7-workflow-runtime-v1/`.
