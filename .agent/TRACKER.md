# Progress Tracker

## Current status

Subplan 90 was explicitly resumed from the consolidated local `main`. The harness self-check,
permission matrix, historical capacity audit, reduced-plan preflight and capacity check passed.
The explicit risk-denial repair, cumulative prior-campaign capacity guard, full reservation check
and explicit 14-run reduced single-repetition variant are implemented; the focused evaluator suite
passed `59`, and the complete offline gate passed `1340 passed, 2 deselected`.

## Active task

The explicit risk-denial ordering repair, cumulative prior-campaign capacity guard, full
remaining-schedule reservation check and reduced-variant support are implemented. The fresh reduced
plan passed preflight and has capacity for 14 conservative admissions under the approved ceiling.

## Implemented boundary

- Accepted outcomes create Learning Reviews regardless of recorded tool failures or unresolved
  items; the background Reviewer decides whether there is anything to save.
- Production Learning Review composition reuses the main Agent model, maximum run time and context
  budget. Separate Learning timeout/lease settings were removed.
- Preference Review jobs JSON serializes datetime fields through the public CLI.
- Managed Skill projection at `3670860` passed a real explicit Skill run; its test Binding was
  removed afterward.

- Core `bash` accepts shell commands without semantic Git/network/destructive classification.
- Explicit `network`, `git_write` and `privilege_escalation` risk flags are denied before the
  direct registered-command fast path; ordinary command content still is not parsed.
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

The exact reduced 14-run admission boundary is ready for confirmation. After confirmation, admit and
execute entries sequentially; do not reuse prior campaign admissions.

## Blockers

- The full 28-run primary remains over the approved 50,000,000-token ceiling after retained usage;
  the user-selected reduced 14-run pilot fits. No formal admission or model request has been made;
  the reduced pilot still requires explicit start confirmation.
