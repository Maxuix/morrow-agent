# Progress Tracker

## Current status

S7P-01 through S7P-03 are verified, fast-forward integrated into local `main`, and retired. S7P-04
Subplan 83 is active for regular-file delete/move/rename, sandbox promotion and truthful recovery.
The main worktree contains only three user-owned untracked research documents; they remain outside
the plan and untouched.

## Active task

The dedicated S7P-04 implementation task is attached to
`codex/feat/s7p-04-workspace-change-lifecycle` at activation baseline
`e80c3157d3286116b04566cf76bf01fd192c6b8b`. Implementation is in progress and must complete the
test-first lifecycle, focused/full offline gates, and the same-task read-only Luna Max review before
returning a clean verified branch.

## Located evidence

- `MutationOperation` contains only create/patch/replace and production has no structured delete,
  move or rename tool.
- A deleted sandbox regular file is detected as `deleted` but `eligible=False`, so promotion cannot
  select or apply it.
- Promotion assumes eligible changes are text create/replace operations and cannot carry source/
  destination identity or truthful later-operation partial failure.
- Prepared intent freezes one file evidence item; recovery currently classifies an expected
  absence as missing rather than a completed delete/source move.

## Next action

Inspect the existing mutation, tool, persistence, recovery and sandbox seams; add failing lifecycle
contracts first, then implement only S7P-04. Run the declared focused/full offline gates and close
the same-task reviewer findings. S7P-05 remains unopened.

## Blockers

None.
