# Progress Tracker

## Current status

The user-requested workspace consolidation is complete on local `main` at integration commit
`a7e22e5`. Focused integration tests passed `140`, the final full offline gate passed `1333 passed,
2 deselected`, and Ruff format/check, compileall, CLI help and `git diff --check` passed. The complete
`docs/notes/` draft is preserved in the named stash `checkpoint: preserve complete docs notes before
branch consolidation`.

## Active task

No active implementation task. Await user direction; do not resume the S7P-09 live campaign.

## Implemented boundary

- Accepted outcomes create Learning Reviews regardless of recorded tool failures or unresolved
  items; the background Reviewer decides whether there is anything to save.
- Production Learning Review composition reuses the main Agent model, maximum run time and context
  budget. Separate Learning timeout/lease settings were removed.
- Preference Review jobs JSON serializes datetime fields through the public CLI.
- Managed Skill projection at `3670860` passed a real explicit Skill run; its test Binding was
  removed afterward.

- Core `bash` accepts shell commands without semantic Git/network/destructive classification.
- Registered workspace writes, including delete/move/rename and sandbox promotion, do not wait for
  heuristic approval.
- File, search, Git diff/status and sandbox snapshots do not hide workspace content based on names
  such as `.env`, `secret`, `credentials` or PEM-like fixture text.
- Command output redacts exact active credential values, not generic token-shaped source strings.
- Project instructions load one root file by precedence; malformed/large/unreadable context warns
  and skips, and nested paths do not affect discovery or recovery.
- Workspace escape, external symlinks, read-only sessions, revision conflicts, atomic publication,
  timeouts/cancellation/output limits, Full Access grant+approval and Skill/MCP policy remain.

## Next action

Await user direction. Any future S7P-09 work must refreeze its source/profile/evidence contract.

## Blockers

- None for the requested consolidation.
- S7P-09 admissions remain paused and require a new explicit user direction after this product
  behavior change invalidated the former permission-equivalence pin.
