# TODO

## Current stage

Stage 6 implementation in progress.

## Active subplan

Subplan 74 — Backup v2 and Doctor.

## Tasks (Subplan 74)

- `[>]` Freeze existing bundle v1 behavior and add explicit bundle version decoding.
- `[ ]` Define and create bundle v2 for SQLite, Artifacts, Extension YAML and referenced managed Skills.
- `[ ]` Add canonical path, digest, symlink, drift, schema and cross-store reference validation.
- `[ ]` Implement isolated v2 verification/restore with atomic publication and no partial activation.
- `[ ]` Extend bounded Doctor checks for Stage 6 tables, evidence, bindings and package drift.
- `[ ]` Add compatibility, tamper, restore, Doctor and migration acceptance tests plus standard gates.

## Boundaries

- Do not implement integrated acceptance closeout, learned routing or unrestricted Skill injection.
- Do not use real credentials, networked MCP Servers, live Providers or user state.
- Work is on `codex/feat/stage6-backup-doctor`; preserve the verified Subplan73 commits and do not
  introduce a second backup/doctor authority or rewrite historical evidence.
- Preserve AgentLoop/ConversationLog, permission policy defaults, public events and Stage 4/5 behavior.
