# Progress Tracker

## Current status

Subplan 90 was explicitly resumed from the consolidated local `main`. The harness self-check,
focused evaluator suite (`56 passed`), permission matrix and historical capacity audit passed. A
policy-ordering regression was found: explicit network/Git-write/privilege-escalation risk flags
were checked after the direct-command fast path and therefore returned `allow`.

## Active task

The explicit risk-denial ordering repair is implemented and its focused regression suite passed.
Run the complete offline/static/CLI gate, record the evidence, and commit before refreezing any
S7P-09 comparison plan.

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

Run the full offline/static/CLI/diff gate. If it passes, commit this repair and create a fresh
source/profile/evidence refreeze; do not reuse prior campaign admissions.

## Blockers

- The approved 50,000,000-token ceiling is currently insufficient for the conservative fresh
  campaign reservation after retained formal usage and incomplete requests. Preflight/refreeze can
  continue offline, but formal admission must stop unless capacity passes or the user approves a
  larger ceiling.
