# TODO

## Current stage

Stage 7 preflight reliability repairs, S7P-01: safe AgentRun observability and headless execution.

## Active subplan

Subplan 80 — S7P-01 AgentRun observability and headless run.

## Tasks

- `[x]` Read the full reliability checklist and locate S7P-01 against current code and tests.
- `[x]` Freeze data, lifecycle, safety, compatibility and validation decisions in Subplan 80.
- `[x]` Add failing usage, context, persistence, diagnostic and headless-entry tests.
- `[x]` Normalize Provider usage and retain usage-only stream chunks without extra text.
- `[x]` Add schema-v17 AgentRun request/terminal observations and safe query paths.
- `[x]` Persist bounded invalid-argument field/type diagnostics without argument values.
- `[x]` Add the one-shot JSONL entrypoint through the existing Session application/orchestrator.
- `[x]` Publish S7P-01 acceptance evidence and run focused/full offline quality gates.
- `[x]` Complete Luna Max subagent code review, repair all confirmed findings and rerun gates.
- `[x]` Commit the final verified implementation; leave merge and Subplan 80 retirement to the root task.

## Boundaries

- Do not change public event types, payload contracts or lifecycle ordering.
- Do not change runtime-policy defaults, tool schemas, prompts, completion semantics or retry policy.
- Do not add dependencies or run live Provider/model/Pi/MCP/network/credential tests.
- ConversationLog remains the only chat-history writer; headless mode reuses AgentLoop and the
  existing Session application/orchestrator.
- Persist no credentials, reasoning, prompts/messages, full tool arguments/results, SDK objects or
  tracebacks. Missing usage/cost is unavailable, never zero.
- Preserve the three user-owned untracked research documents.
