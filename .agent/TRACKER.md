# Progress Tracker

## Current status

Stage 7 Subplan 7 (Direct Invoking-Session Adapter) is completed and integrated into local `main`
(implementation `cda0c0c`, closeout `72767b1`). A post-integration review fix now scopes Direct
node evidence, capture aggregation and result snapshots to the admitted NodeRun AgentRun instead of
the shared root Task. Full offline gate: 1510 passed, 2 Live deselected; Ruff format/check,
compileall, `morrow --help` and `git diff --check` clean.

## Active task

None. Subplan 8 is not active.

## Next action

Await explicit continuation before activating Subplan 8 (Management, Templates and Optional
Events) from verified `main`.

## Blockers

No local implementation blocker. Local `main` is ahead of `origin/main` (31 commits); remote
publication remains blocked pending explicit authorization for the configured GitHub remote. No
Live tests were run.
