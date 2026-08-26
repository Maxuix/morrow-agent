# TODO

## Current stage

Stage 7 preflight reliability repairs, S7P-00: freeze evaluation protocol and failure taxonomy.

## Active subplan

Subplan 79 — S7P-00 reproducible evaluation protocol.

## Tasks

- `[x]` Read the full reliability checklist and locate S7P-00 against current code and evidence.
- `[x]` Freeze scope, protocol decisions, implementation boundaries and deterministic validation.
- `[ ]` Add failing tests for run manifests, rebuild, evidence finalization and aggregation.
- `[ ]` Implement the versioned protocol, strict run profile and task change contracts.
- `[ ]` Implement run start/rebuild/finalize/summarize without changing production runtime code.
- `[ ]` Update evaluation documentation and publish S7P-00 acceptance evidence.
- `[ ]` Run focused, self-check, full offline and quality gates.
- `[ ]` Complete independent code review, repair all findings and rerun affected gates.
- `[ ]` Commit verified implementation, fast-forward merge to `main`, and retire Subplan 79.

## Boundaries

- Do not modify AgentLoop, Provider adapters, public events, Operational Store, prompts or runtime
  policy in S7P-00.
- Do not run live Provider/model/Pi/network/credential tests.
- Do not expose Gold solutions to an evaluated Agent.
- Do not persist credentials, reasoning, full tool arguments/results or tracebacks.
- Do not rewrite the legacy Direct baseline; publish an explicit erratum in the new acceptance
  record.
- Preserve the three user-owned untracked research documents.
