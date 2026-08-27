# TODO

## Current stage

Removal of the runtime OutcomeContract/output-fact gate is complete and integrated locally.

## Active subplan

No subplan is active. Subplan 87 is integrated; the untracked S7P-06 draft remains inactive.

## Tasks

- `[x]` Remove intent resolution, workspace baseline preparation and pre-answer completion checks from
  AgentLoop and production composition.
- `[x]` Remove active application/session APIs and output telemetry that expose runtime completion
  verdicts, while preserving stored-snapshot/schema read compatibility.
- `[x]` Delete obsolete semantic resolver/checker implementation and replace its tests with direct
  model-stop acceptance and recovery regressions.
- `[x]` Update architecture and execution documents.
- `[x]` Run final static/CLI gates, commit and fast-forward integrate.

## Boundaries

- Do not infer business correctness or reject a valid model stop based on task/output semantics.
- Do not implement S7P-06+, add dependencies, change permission/tool effects or runtime defaults.
- Do not add public event types/fields or create another ConversationLog writer.
- Do not persist command text, stdout/stderr, file content, secrets, reasoning or tracebacks.
- Do not run live Provider/model/Pi/MCP/network/credential tests.
- Preserve the untracked S7P-06 draft and the three user-owned research documents.
