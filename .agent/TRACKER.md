# Progress Tracker

## Current status

The user paused the S7P-09 evaluation direction and requested a Pi-like usable core loop before
additional protection layers. Production changes and regressions are implemented. Final validation
passed: `1339 passed, 2 deselected`; Ruff format/check, compileall, CLI help and `git diff --check`
also passed.

## Active task

Commit the verified repair without adding the unrelated untracked `docs/notes/`, then record the
checkpoint and stop. Do not merge or resume evaluation without explicit user authorization.

## Implemented boundary

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

Create the verified commit and record its hash in execution state.

## Blockers

- None for the requested implementation.
- S7P-09 admissions remain paused and require a new explicit user direction after this product
  behavior change invalidated the former permission-equivalence pin.
