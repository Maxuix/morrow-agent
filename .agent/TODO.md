# TODO

## Current stage

Stage 7 pre-baseline, item 2: test the current Direct Agent and repair only blocking tool gaps.

## Active subplan

Subplan 78 — Direct Agent baseline and blocking tool gaps.

## Tasks

- `[x]` Establish the public-surface test basis, isolated execution setup and Provider readiness.
- `[x]` Self-check the 10-task dataset and pass one public-interface smoke task.
- `[x]` Execute the complete 10-task Direct Agent baseline once and capture sanitized evidence.
- `[x]` Classify every failure and reproduce only suspected Stage 7-blocking tool defects.
- `[x]` Add focused regression tests and minimally repair each confirmed blocking tool defect.
- `[x]` Rerun affected evaluations and required focused/full offline and quality gates.
- `[x]` Persist the evidence-backed baseline report and reconcile execution state.
- `[x]` Commit verified work, integrate it into local `main`, and retire the topic branch.

## Boundaries

- Do not use Gold patches or external-task reference solutions to guide the Agent.
- Do not repair model reasoning, add Workflow/AgentDefinition behavior, or broaden into compaction,
  steering, plan mode, routing, package management or Git writes without direct blocking evidence.
- Preserve policy defaults, approval/sandbox/workspace boundaries, recovery ownership, event shape,
  secret redaction and all unrelated user changes.
- Provider-backed Direct Agent calls are in scope; live MCP and unrelated network actions are not.
