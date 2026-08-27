# TODO

## Current stage

Subplan 85 is active on `codex/feat/s7p-06-pi-parity`; Phase A is in progress.

## Active subplan

Subplan 85 is active. Subplan 87 is integrated; S7P-06 implementation is underway.

## Tasks

- `[x]` Replace the obsolete 30 → 60/no-progress S7P-06 draft with a pinned Pi 0.84.2 parity plan.
- `[>]` Freeze the executable Pi parity table, scripted fixtures and Morrow v1 compatibility map.
- `[ ]` Implement runtime policy v2 and the uncapped loop.
- `[ ]` Implement token-window compaction and overflow recovery.
- `[ ]` Implement provider retry and Pi-equivalent tool-output truncation.
- `[ ]` Run offline evidence, documentation, review and closeout.

## Boundaries

- Do not infer business correctness or reject a valid model stop based on task/output semantics.
- Do not run live Provider/model/Pi/MCP/network/credential tests.
- Do not add public event types/fields or create another ConversationLog writer.
- Do not persist command text, stdout/stderr, file content, secrets, reasoning or tracebacks.
- Do not run live Provider/model/Pi/MCP/network/credential tests.
- Leave the three research documents unchanged until Subplan 85 activation reaches its documented
  checklist-update phase.
