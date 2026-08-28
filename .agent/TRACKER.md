# Progress Tracker

## Current status

The user requested a Hermes-like best-effort learning path without front-end task-resolution
analysis. Production changes and regressions are implemented. Final validation passed:
`1344 passed, 2 deselected`; Ruff format/check, compileall, CLI help and `git diff --check` also
passed. A real public Skill install/enable/select/run succeeded, and Preference jobs JSON returned
the existing queue successfully.

## Active task

Commit the verified learning-loop repair. Do not merge or resume S7P-09 evaluation without explicit
authorization; unrelated untracked `docs/notes/` remains untouched.

## Implemented boundary

- Accepted outcomes create Learning Reviews regardless of recorded tool failures or unresolved
  items; the background Reviewer decides whether there is anything to save.
- Production Learning Review composition reuses the main Agent model, maximum run time and context
  budget. Separate Learning timeout/lease settings were removed.
- Preference Review jobs JSON serializes datetime fields through the public CLI.
- Managed Skill projection at `3670860` passed a real explicit Skill run; its test Binding was
  removed afterward.

- Core `run_command` accepts argv/shell without semantic Git/network/destructive classification.
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

Commit the verified repair, then await user direction. A future evaluation must refreeze its
permission/evidence contract from the new core behavior rather than reuse an older S7P-09 pin.

## Blockers

- None for the requested implementation.
- S7P-09 admissions remain paused and require a new explicit user direction after this product
  behavior change invalidated the former permission-equivalence pin.
