# Stage 8 current-plan subplans

This directory contains only child plans owned by the active Stage 8 master plan
(`.agent/PLAN.md`). Subplan 2 implementation is complete, with remote publication closeout blocked
pending explicit approval. The
retired Stage 7 sequence is archived under `.agent/archive/subplans/stage7-workflow-runtime/`.

| Order | File | Roadmap slice | Status |
|---|---|---|---|
| 1 | `1-pause-drain-runtime.md` | 8C (runtime, part 1) | completed 2026-09-03 |
| 2 | `2-future-graph-patch-continuation.md` | 8C (runtime, part 2) | implementation complete; publication blocked |
| 3 | `3-core-api-local-server.md` | 8A (protocol/server) | pending activation; needs ApplicationEvent + web-framework authorization |
| 4 | `4-web-gui-observer.md` | 8A (GUI) | pending activation; needs frontend toolchain authorization |
| 5 | `5-workflow-editor-agent-inspector.md` | 8B | pending activation |
| 6 | `6-run-control-gui.md` | 8C (GUI) | pending activation |
| 7 | `7-graph-planner-draft.md` | 8D | pending activation |
| 8 | `8-global-replan.md` | 8E | pending activation |
| 9 | `9-context-learning-skill-gui.md` | 8F | pending activation; may be re-sequenced earlier |
| 10 | `10-feedback-evaluation.md` | 8G | pending activation |
| 11 | `11-read-only-parallelism.md` | 8H | pending activation; gated on its own entry conditions |

## Lifecycle

- Keep only this `README.md` and child plans owned by the current master plan here.
- Number the first child plan of every new master plan `1-<slug>.md`, then continue contiguously.
- Do not inherit sequence numbers from an earlier master plan.
- Before replacing `.agent/PLAN.md`, move all numbered files here into a plan-specific directory
  under `.agent/archive/subplans/` and preserve their names and contents.
- Never reactivate or renumber an archived child plan. Use Git history and the archive for
  recovery.
- Keep at most one child plan active at a time; record its state in `.agent/TODO.md` and
  `.agent/TRACKER.md`.
- Start every production child from the latest verified `main`; do not implement later interfaces
  or schemas early.
- A child closes only after its declared validation, coherent commits, fast-forward integration,
  ancestry verification and clean branch/worktree retirement.
- New hard gates must meet the proportionality test in `.agent/PLAN.md`; quality, cost and
  temporary availability facts must not be promoted into safety blockers.
