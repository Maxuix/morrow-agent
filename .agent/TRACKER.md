# Progress Tracker

## Current status

Subplan 90 was explicitly resumed from the consolidated local `main`. The harness self-check,
permission matrix, historical capacity audit, reduced-plan preflight and capacity check passed.
The explicit risk-denial repair, cumulative prior-campaign capacity guard, full reservation check
and explicit 14-run reduced single-repetition variant are implemented. Pi-aligned transient Provider
classification and partial-usage evaluation are now also implemented; focused affected tests passed
`194`, and the complete offline gate passed `1363 passed, 2 deselected`.

## Active task

The ordinary bundled retry default is aligned with Pi at three and committed as `c0b14b9`. Fresh
reduced plan r14 passes plan-check, source/evidence preflight and permission equivalence, but its
full remaining-schedule capacity check blocks before admission.

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

Preserve r14 as a no-admission blocked plan. Continuing requires an explicit choice to raise the
50,000,000-token ceiling, lower the 1,500,000-token per-admission conservative reservation, or add
a smaller frozen diagnostic schedule. Do not create an admission before that choice is recorded.

## Blockers

- The full 28-run primary remains over the approved 50,000,000-token ceiling after retained usage.
  The fresh r14 reduced plan accounts for `31,877,509` historical tokens and has `18,122,491`
  remaining. Its 14 × 1,500,000 = `21,000,000` planned reservation exceeds the ceiling by
  `2,877,509`. No r14 admission or Provider request was created; the pilot is not complete or a
  comparison result.
