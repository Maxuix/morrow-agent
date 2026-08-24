# TODO

## Current stage

Stage 6 implementation in progress.

## Active subplan

No subplan is active. Subplan 69 — Skill Script Execution is complete locally; Subplan 70 is
pending activation.

## Tasks (Subplan 69)

- `[x]` Define bounded script requests, execution results and output Artifact contracts.
- `[x]` Implement frozen package verification, argv/env/root validation and isolated execution.
- `[x]` Route capability/approval/cancellation/audit/recovery through existing ToolExecutor seams.
- `[x]` Import bounded declared outputs as Artifacts and redact/truncate process summaries.
- `[x]` Add focused script/permission/recovery tests and run the standard quality gates.

## Boundaries

- Do not implement scripts, MCP, learned routing or unrestricted Skill injection.
- Do not use real credentials, networked MCP Servers, live Providers or user state.
- Worktree is on `main` because `.git` refs are read-only; preserve Subplans 65–66 and their review
  repairs while implementing this subplan.
- Preserve AgentLoop/ConversationLog, permission policy defaults, public events and Stage 4/5 behavior.
