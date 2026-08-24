# TODO

## Current stage

Stage 6 implementation complete locally; post-closeout review pending.

## Active subplan

No active implementation subplan. Subplan 75 is complete; post-closeout review is pending.

## Tasks (Subplan 75)

- `[x]` Create isolated Stage 6 acceptance fixtures and integrated scenario coverage.
- `[x]` Exercise handwritten Skill, Draft/Usage, Script, Provider/Model and Fake MCP seams offline.
- `[x]` Exercise conflict, spoof/drift, Doctor, Backup v1 and Backup v2 restore evidence.
- `[x]` Fix only confirmed narrow integration defects.
- `[x]` Run full offline/quality/CLI gates and capture exact outcomes.
- `[x]` Reconcile README, architecture, roadmap and final proposal deviations.

## Boundaries

- Do not implement integrated acceptance closeout, learned routing or unrestricted Skill injection.
- Do not use real credentials, networked MCP Servers, live Providers or user state.
- Work is on `codex/feat/stage6-closeout`; preserve the verified Subplan74 merge and do not
  introduce a second backup/doctor authority or rewrite historical evidence.
- Preserve AgentLoop/ConversationLog, permission policy defaults, public events and Stage 4/5 behavior.
