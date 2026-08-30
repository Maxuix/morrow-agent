# Current-plan subplans

This directory is reserved for child plans of the current `.agent/PLAN.md`. There is no active
subplan now, so no numbered child-plan file is present.

## Lifecycle

- Keep only this `README.md` and child plans owned by the current master plan here.
- Number the first child plan of every new master plan `1-<slug>.md`, then continue contiguously.
- Do not inherit sequence numbers from an earlier master plan.
- Before replacing `.agent/PLAN.md`, move all numbered files here into a plan-specific directory
  under `.agent/archive/subplans/` and preserve their names and contents.
- Never reactivate or renumber an archived child plan. Use Git history and the archive for recovery.
- Keep at most one child plan active at a time; record its state in `.agent/TODO.md` and
  `.agent/TRACKER.md`.

The retired global sequence 36–100 is archived under
`.agent/archive/subplans/legacy-sequence-36-100/`.
