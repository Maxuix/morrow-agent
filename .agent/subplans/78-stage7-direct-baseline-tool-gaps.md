# Subplan 78 — Stage 7 Direct Baseline and Blocking Tool Gaps

> Status: completed locally; integration pending

## Goal

Run the fixed 10-task Code Agent Mini Eval against the current production Direct Agent, establish
the Stage 7 comparison baseline, and repair only reproducible tool defects that prevent complex
tasks from completing under the existing policy and sandbox boundaries.

## Ownership

- evaluation orchestration and sanitized evidence under `evals/code-agent-mini/` or
  `docs/acceptance/`;
- only the concrete production tool modules and focused tests implicated by reproduced failures;
- `.agent/PLAN.md`, `.agent/TODO.md`, `.agent/TRACKER.md`, `.agent/LOG.md` and this subplan.

No AgentDefinition, Workflow, routing, compaction, steering, plan-mode, dependency or permission
default work belongs to this subplan.

## Tasks

1. Establish revision/config/public-surface inventory and safe isolated execution.
2. Self-check the dataset and smoke one simple task through the public REPL.
3. Execute all 10 tasks once with the fixed model and permission mode.
4. Verify each workspace externally and inspect unexpected changes.
5. Classify failures and reproduce suspected tool blockers without consulting Gold solutions.
6. Implement focused regressions and minimal confirmed fixes only.
7. Rerun affected tasks and the required deterministic gates.
8. Publish the Direct baseline report and retire the subplan cleanly.

## Exit evidence

- one status and verifier record for every task;
- Provider/model, call/latency/cost facts where safely observable;
- public-interface reproduction for every confirmed tool blocker;
- focused regression plus affected eval rerun for every production fix;
- full non-live, Ruff, compileall, CLI help and diff-check gates.

## Outcome

- Baseline: `2 PASS / 8 FAIL`, 312 tool calls, approximately 32 minutes 52 seconds.
- Fixed durable policy-denial diagnostics and exact read-only sandbox toolchain exposure without
  widening network, dependency-install, Git-write, HOME or workspace-write capability.
- Representative reruns reached viable project test execution; remaining failures were classified
  as Agent/round-budget behavior and deliberately left for Stage 7 comparison.
- Validation: focused `37 passed`; host-level macOS sandbox `2 passed`; full offline
  `1081 passed, 2 deselected`; Ruff, compileall, CLI help and diff check passed.
- Report: `docs/acceptance/stage7-direct-agent-baseline.md`.
