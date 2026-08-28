# Progress Tracker

## Current status

Subplan 90 was explicitly resumed from the consolidated local `main`. The harness self-check,
permission matrix, historical capacity audit, reduced-plan preflight and capacity check passed.
The explicit risk-denial repair, cumulative prior-campaign capacity guard, full reservation check
and explicit 14-run reduced single-repetition variant are implemented; the focused evaluator suite
passed `61`, and the complete offline gate passed `1342 passed, 2 deselected`.

## Active task

The admission boundary repair is implemented and verified. Morrow entries generate a minimal
isolated config from the frozen Provider/service/model selection, preserve only the matching
configured Keychain reference, and call existing `build_active()` before creating the admission.
No readiness command, no-tool probe or model request was added.

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

Preserve the paused 2/14 evidence root as diagnostic evidence. Any later formal execution requires
a fresh current-source plan and explicit authorization; do not reuse its admissions or run keys.

## Blockers

- The full 28-run primary remains over the approved 50,000,000-token ceiling after retained usage.
  The user-selected reduced 14-run pilot fits, but its first two formal Morrow admissions were
  blocked by provider/runner runtime conditions. The remaining 12 entries were intentionally not
  admitted; the pilot is not complete and cannot claim a comparison result.
