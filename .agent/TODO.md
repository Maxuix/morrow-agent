# TODO

## Current stage

Stage 6 planned; implementation not started.

## Active subplan

Subplan 63 — Dependency and Contract Spike (`ready`, not yet in progress).

## Tasks

- `[ ]` Create `codex/feat/stage6-contract-spike` from the latest verified `main` when implementation
  is authorized to begin.
- `[ ]` Reconfirm current runtime, permission, process, migration and backup seams with source refs.
- `[ ]` Evaluate the official MCP Python SDK in a temporary environment without changing dependency
  files.
- `[ ]` Select JSON Schema dialect/validator behavior and record unsupported-feature handling.
- `[ ]` Exercise the narrow local Fake stdio connect/list/call/close prototype.
- `[ ]` Measure AgentRun reference, Skill context and MCP catalog/result budgets.
- `[ ]` Lock Skill package canonicalization, safe version paths and TOCTOU rules.
- `[ ]` Publish the Stage 6 dependency/contract ADR with exact recommendation and alternatives.
- `[ ]` Verify `pyproject.toml` and `uv.lock` are unchanged; run the Subplan 63 gates.
- `[ ]` Commit, merge and retire Subplan 63 before activating Subplan 64.

## Boundaries

- This subplan changes no production behavior and adds no permanent dependency.
- Do not use real credentials, networked MCP Servers, live Providers or user state.
- A recommended dependency still requires explicit user approval before Subplan 72 changes the lock.
- Preserve AgentLoop/ConversationLog, permission policy defaults, public events and Stage 4/5 behavior.
