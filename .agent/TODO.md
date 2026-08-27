# TODO

## Current stage

The Pi-parity rewrite of candidate Subplan 85 is prepared. No implementation is active.

## Active subplan

No subplan is active. Subplan 87 is integrated; revised S7P-06 remains pending activation.

## Tasks

- `[x]` Replace the obsolete 30 → 60/no-progress S7P-06 draft with a pinned Pi 0.84.2 parity plan.
- `[ ]` Activate Subplan 85 only after explicit user direction; then create its topic branch and
  execute Phase A before production changes.

## Boundaries

- Do not infer business correctness or reject a valid model stop based on task/output semantics.
- Do not start S7P-06 implementation, change runtime defaults or create a topic branch from this
  planning-only request.
- Do not add public event types/fields or create another ConversationLog writer.
- Do not persist command text, stdout/stderr, file content, secrets, reasoning or tracebacks.
- Do not run live Provider/model/Pi/MCP/network/credential tests.
- Leave the three research documents unchanged until Subplan 85 activation reaches its documented
  checklist-update phase.
