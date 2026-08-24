# Progress Tracker

## Current status

Stage 5 is complete and accepted. The fact-checked Stage 6 final proposal and complete Subplan
63–75 implementation sequence are written; no Stage 6 implementation has started.

## Last completed work

- Reconciled the prior Stage 6方案 with current local `main` (`479270b`), the Stage 6 roadmap and the
  supplied review-agent assessment.
- Adopted the confirmed corrections for per-run Provider/Model assembly, SkillBinding, local Trust
  authority, safe version paths, package TOCTOU, dedicated Skill script execution, replay/rehydrate,
  dynamic tool schemas/recovery, compound MCP risk, no MCP auto retry, complete MCP results,
  Provider capabilities, migration split, identity conflicts and Backup v2.
- Corrected one additional current-code mismatch: MCP local tool names use provider-compatible
  `mcp__<server>__<tool>` slugs rather than dotted names rejected by current ToolDefinition rules.
- Kept `docs/ARCHITECTURE.md` unchanged because Stage 6 modules do not exist yet; Subplan 75 owns
  fact-only architecture synchronization after implementation.

## Active task

None in progress. Subplan 63 is ready to start.

## Next action

When the user asks to begin implementation, create `codex/feat/stage6-contract-spike` from latest
verified `main`, mark the first TODO `[>]`, and execute Subplan 63. Do not start Subplan 64 early.

## Dependency gate

Subplan 63 may evaluate dependencies in a temporary environment but must leave `pyproject.toml` and
`uv.lock` unchanged. Any exact MCP/JSON Schema dependency addition requires a later explicit user
approval before Subplan 72.

## Blockers

No blocker to Subplans 63–71. MCP implementation Subplans 72–73 are conditional on the recorded
dependency decision and explicit approval if new packages are recommended.

## Preserved history

Detailed Stage 4/5 implementation and acceptance evidence remains in Git history and completed
subplans. It is not duplicated in this active tracker.
