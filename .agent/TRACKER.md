# Progress Tracker

## Current status

S7P-01 through S7P-03 are verified, fast-forward integrated into local `main`, and retired. S7P-04
Subplan 83 is active for regular-file delete/move/rename, sandbox promotion and truthful recovery.
The main worktree contains only three user-owned untracked research documents; they remain outside
the plan and untouched.

## Active task

The root task has reproduced the S7P-04 gaps and frozen the executable design. The next action is to
commit this activation baseline, create `codex/feat/s7p-04-workspace-change-lifecycle`, and open one
dedicated `gpt-5.6-luna` / `max` implementation task. That task must implement and run its own
read-only Luna Max subagent review before returning a clean verified branch.

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

Activate the topic task from this committed main baseline. Implement only S7P-04, run the declared
focused/full offline gates and close the same-task reviewer findings. S7P-05 remains unopened.

## Blockers

None.
