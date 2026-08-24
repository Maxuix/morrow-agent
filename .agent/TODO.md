# TODO

## Current stage

Stage 6 implementation in progress.

## Active subplan

Subplan 68 — Generated Skill Drafts and Usage (completed locally; Subplan 69 is next).

## Tasks (Subplan 68)

- `[x]` Define bounded Draft, validation report and Usage contracts with immutable evidence refs.
- `[x]` Add the v15 Draft/validation/Usage migration and journal mappings without storing sensitive
  payloads.
- `[x]` Implement accepted Candidate→Draft creation, deterministic validation and replay-safe roots.
- `[x]` Implement revision/edit/revalidate/accept/reject transitions and lifecycle publication;
  require a separate Binding update after acceptance.
- `[x]` Record bounded observational SkillUsage and provide insufficient-data comparisons.
- `[x]` Add focused Draft/Usage/migration tests and run the standard quality gates.

## Boundaries

- Do not implement scripts, MCP, learned routing or unrestricted Skill injection.
- Do not use real credentials, networked MCP Servers, live Providers or user state.
- Worktree is on `main` because `.git` refs are read-only; preserve Subplans 65–66 and their review
  repairs while implementing this subplan.
- Preserve AgentLoop/ConversationLog, permission policy defaults, public events and Stage 4/5 behavior.
