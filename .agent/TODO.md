# TODO

## Current stage

Stage 6 implementation in progress.

## Active subplan

Subplan 63 — Dependency and Contract Spike (completed on `codex/feat/stage6-contract-spike`).

## Tasks

- `[x]` Create `codex/feat/stage6-contract-spike` from the latest verified `main` when implementation
  is authorized to begin.
- `[x]` Reconfirm current runtime, permission, process, migration and backup seams with source refs.
- `[x]` Evaluate the official MCP Python SDK in a temporary environment without changing dependency
  files.
- `[x]` Select JSON Schema dialect/validator behavior and record unsupported-feature handling.
- `[x]` Exercise the narrow local Fake stdio connect/list/call/close prototype.
- `[x]` Measure AgentRun reference, Skill context and MCP catalog/result budgets.
- `[x]` Lock Skill package canonicalization, safe version paths and TOCTOU rules.
- `[x]` Publish the Stage 6 dependency/contract ADR with exact recommendation and alternatives.
- `[x]` Verify `pyproject.toml` and `uv.lock` are unchanged; run the Subplan 63 gates.
- `[x]` Commit, merge and retire Subplan 63 before activating Subplan 64.

## Boundaries

- This subplan changes no production behavior and adds no permanent dependency.
- Do not use real credentials, networked MCP Servers, live Providers or user state.
- A recommended dependency still requires explicit user approval before Subplan 72 changes the lock.
- Preserve AgentLoop/ConversationLog, permission policy defaults, public events and Stage 4/5 behavior.