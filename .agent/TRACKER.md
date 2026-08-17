# Progress Tracker

## Current status

Subplan 25 is active after explicit user authorization. The Natural-Language Configuration
Tooling plan remains limited to the generic approval foundation and the later configuration
slice; Stage 3 local file/search/edit/Shell work is still out of scope.

## Last completed task

Verified the external configuration-tooling plan review against current code and revised the
master plan plus Subplans 25–28 to lock composition, approval/cancellation, reset/tombstone,
no-op/revision, compatibility, partial-write, history, test-inventory, and Stage 3 contracts.

## Next action

Implement the Core approval contract and Runtime execution metadata, then add generic
ToolExecutor enforcement and focused boundary tests.

## Active task

CT.25.1 — Lock generic contracts and add red boundary tests.

## Blockers

None.

## Active boundary

- One AgentLoop for every non-Slash input.
- One standard `update_configuration` tool for natural-language Preferences/Profile changes.
- No keyword authority or secondary structured configuration route.
- No configuration-specific AgentLoop, ToolExecutor, Orchestrator, event, or approval branch.
- Generic local Tool Policy/ApprovalPort; thin handler over ConfigPatchService.
- Per-call approval and atomic writes with deliberate cross-call partial persistence; no
  rollback transaction.
- Existing reset tombstones, session-null revision, fixed no-op matrix, and dirty/history
  semantics are locked before implementation.
- No Handoff, Provider/credential/security tool, file/Shell/Git/network capability, or
  persistent Session work.
