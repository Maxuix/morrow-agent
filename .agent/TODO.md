# TODO

## Current stage

Subplan 88 is active on `feat/s7p-07-runtime-control`.

## Active subplan

Subplan 88 — S7P-07 runtime control steering and follow-up.

## Tasks

- `[>]` Activate Subplan 88 and publish the Phase A acceptance evidence.
- `[ ]` Add the durable bounded runtime-control queue and schema migration.
- `[ ]` Add `FinishReason.STEERED` and AgentLoop safe-point delivery.
- `[ ]` Add orchestrator/headless steering and follow-up delivery with crash-safe idempotency.
- `[ ]` Add terminal in-run input using the pinned Pi mapping.
- `[ ]` Run focused and offline gates, formal read-only review, remediation and closeout.

## Boundaries

- Do not modify S7P-06 retry, compaction, truncation, loop-default or policy-version behavior.
- Do not add public event types/fields or create another ConversationLog writer.
- Do not interrupt admitted tool batches or in-flight model streams for steering.
- Do not persist tool arguments/results, command output, secrets, reasoning or tracebacks.
- Do not run live Provider/model/Pi/MCP/network/credential tests or add dependencies.
