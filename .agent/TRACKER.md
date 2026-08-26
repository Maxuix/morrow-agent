# Progress Tracker

## Current status

S7P-00 is active on `codex/feat/s7p-00-eval-protocol` from verified
`main@f69a0ce9ec7efc6a7eea07f7c5a1bb36945e9dfa`. The full user-provided reliability checklist has
been read. Current repository evidence confirms that the evaluation harness has task preparation
and verification but no Run Manifest, frozen taxonomy, raw run record, integrity-linked diff/stop
evidence or mechanical aggregation path.

The current worktree also contains three user-owned untracked research documents, including the
reliability checklist. They are not part of the topic branch and must remain untouched.

## Active task

The executable Subplan 79 is written and awaiting implementation in the user-requested separate
Codex session using `gpt-5.6-luna` with `max` reasoning.

## Located evidence

- `eval.py --help` exposes only `list/show/prepare/verify/self-check`.
- A freshly prepared workspace marker contains only dataset/version/task/gold fields.
- `results-template.csv` lacks failure class, tool terminal-state split, stop reason, verifier/diff
  evidence and all environment/configuration snapshots.
- The README still defines one full baseline plus optional reruns, conflicting with S7P-00's
  minimum two independent repetitions.
- No committed raw result JSON/JSONL exists from which the legacy acceptance totals can be rebuilt.

## Next action

Commit this plan checkpoint, return the primary worktree to `main`, create a separate project
worktree session on the topic branch with Luna Max, and execute Subplan 79. After its implementation
commit, review the full branch diff before requesting any review repairs.

## Blockers

None.
