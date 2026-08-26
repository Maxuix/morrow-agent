# TODO

## Current stage

Stage 7 preflight reliability repairs, S7P-04: basic workspace change lifecycle.

## Active subplan

Subplan 83 — delete, move, rename and sandbox promotion lifecycle.

## Tasks

- `[x]` Read S7P-04 and reproduce missing operations and ineligible sandbox deletion.
- `[x]` Freeze regular-file-only, no-overwrite, approval, evidence and recovery decisions.
- `[>]` Add failing delete/move/rename domain, schema and production inventory tests.
- `[ ]` Implement confined delete and atomic no-replace move/rename publication.
- `[ ]` Add structured tools, previews, approval/cancellation and ChangeSet facts.
- `[ ]` Freeze ordered expected-absence/two-path durable evidence and recovery outcomes.
- `[ ]` Promote sandbox deletes and unambiguous moves with deterministic partial-failure truth.
- `[ ]` Prove dirty-change, conflict, symlink, directory, race and crash boundaries.
- `[ ]` Prove scripted Direct delete/rename final-tree acceptance.
- `[ ]` Update architecture, publish acceptance evidence and run focused/full offline gates.
- `[ ]` Complete same-task Luna Max subagent review and repair every confirmed finding.
- `[ ]` Commit the final verified implementation; leave merge/retirement to the root task.

## Boundaries

- Do not implement copy, directories/recursive mutation, overwrite/force, chmod/link, cross-device
  fallback, run-level undo, Git writes or S7P-05 completion verification.
- Do not change public events, runtime-policy defaults, permission/sandbox authority or dependencies.
- Do not run live Provider/model/Pi/MCP/network/credential tests.
- Preserve ConversationLog ownership and all secret/reasoning/tool-payload boundaries.
- Preserve the three user-owned untracked research documents.
