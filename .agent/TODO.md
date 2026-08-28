# TODO

## Current stage

Subplan 92 is active on `refactor/remove-legacy-tool-adapters`, intentionally stacked from the
verified S7P-09 execution branch after Subplan 91.

## Active subplan

Remove Legacy Model-Facing Tool Adapters.

## Tasks

- `[x]` Remove legacy Provider schemas, Pydantic argument models and RegisteredTool factories from
  production local-tool code.
- `[x]` Separate legacy durable-recovery name declarations from the current production inventory;
  remove obsolete runtime contract entries.
- `[x]` Migrate focused tests to `read/ls/find/grep/edit/write/bash` or underlying service tests and
  delete assertions whose sole subject was the retired Provider contract.
- `[x]` Update architecture, acceptance evidence and current inventory documentation.
- `[x]` Run focused tests, full offline tests, Ruff, compileall, CLI help and diff checks.
- `[>]` Commit verified cleanup and fast-forward it into the parent S7P-09 execution branch.

## Boundaries

- Do not change runtime-policy defaults, public events, protocol v1 thresholds, tasks or verifiers.
- Keep filesystem/search/mutation/process services and execution-side safety semantics.
- Preserve recovery of historical old-name records through a narrowly labelled compatibility map.
- Do not run live Provider requests or access credentials.
