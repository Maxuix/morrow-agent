# TODO

## Current stage

Subplan 85 is complete locally on `codex/feat/s7p-06-pi-parity`; root integration is pending.

## Active subplan

Subplan 85 is complete. Subplan 87 is integrated; S7P-06 implementation and closeout are complete.

## Tasks

- `[x]` Replace the obsolete 30 → 60/no-progress S7P-06 draft with a pinned Pi 0.84.2 parity plan.
- `[x]` Freeze the executable Pi parity table, scripted fixtures and Morrow v1 compatibility map.
- `[x]` Implement runtime policy v2 and the uncapped loop.
- `[x]` Implement token-window compaction and overflow recovery.
- `[x]` Implement provider retry and Pi-equivalent tool-output truncation.
- `[x]` Run offline evidence, documentation, review and closeout.

## Boundaries

- Do not infer business correctness or reject a valid model stop based on task/output semantics.
- Do not run live Provider/model/Pi/MCP/network/credential tests.
- Do not add public event types/fields or create another ConversationLog writer.
- Do not persist command text, stdout/stderr, file content, secrets, reasoning or tracebacks.
- Do not run live Provider/model/Pi/MCP/network/credential tests.
- The Stage 7 research checklist was updated at the documented Phase F point; the other two
  user-owned research documents remain unchanged.
