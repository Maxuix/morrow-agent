# Stage 7 Pre-Baseline: Direct Agent Evaluation and Blocking Tool Repairs

> Status: completed locally; pending fast-forward integration
> Active subplan: 78 — Direct Agent baseline and blocking tool gaps (closeout)
> Baseline revision: `05e3603090dce7955d89f72f08f7df2ed2d7b120`
> Branch: `codex/feat/stage7-direct-baseline`
> Evaluation authority: `evals/code-agent-mini/README.md`

## 1. Objective

Establish the Direct Agent success, cost and rework baseline required before Stage 7. Run the fixed
10-task Code Agent Mini Eval through Morrow's public terminal interface, diagnose failures from
sanitized evidence, and repair only tool behavior that demonstrably blocks Stage 7 complex code
tasks.

This slice does not implement AgentDefinition, Workflow, orchestration, compaction, steering, plan
mode, model routing or generalized product improvements.

## 2. Test contract

- Agent: current production Direct Agent.
- Provider/model: the configured `opencode-go/mimo-v2.5`; no silent model fallback.
- Permission mode: `auto-sandboxed` so ordinary workspace-local code operations can run without
  manual approvals while preserving the native sandbox boundary.
- Workspace: one newly prepared, isolated Git workspace per task.
- User surface: launch with `morrow --dir WORKSPACE`, submit one ordinary request to read `TASK.md`
  and complete the task, then exit through the documented REPL command.
- Oracle: the evaluation harness's workspace-external verifier plus unexpected-diff inspection.
- Repetition: one complete 10-task baseline. Repeat only a failed task when needed to distinguish
  nondeterminism from a reproducible tool defect or to verify a repair.
- Network/credentials: Provider-backed execution is explicitly in scope for this user-requested
  Direct Agent baseline; never print credential values or raw Provider payloads.

## 3. Failure classification and repair gate

Classify every non-pass as one of:

1. `tool_blocker`: an available or clearly required code-agent tool cannot perform a necessary,
   policy-allowed operation, reports unusable diagnostics, or exposes a broken workflow contract;
2. `agent_reasoning`: tools were sufficient but the model chose or implemented the wrong approach;
3. `budget_or_context`: the run stopped at a frozen runtime limit without evidence of a tool defect;
4. `provider_or_environment`: credential, network, Provider, sandbox, dependency or harness failure;
5. `inconclusive`: evidence is insufficient or the outcome is nondeterministic.

Production changes require a reproducible `tool_blocker` on at least one complex task and a clear
Stage 7 impact. Fix the narrowest shared tool boundary; preserve capability policy, approvals,
sandboxing, workspace containment, ToolExecutor/recovery ownership, event lifecycle and secret
redaction. Do not tune against Gold patches or copy reference solutions into Agent context.

## 4. Execution sequence

1. Record revision, dirty-state caveat, public launch path, non-secret model/policy facts and
   Provider readiness.
2. Run the dataset self-check, then one simple public-interface smoke task.
3. Run all 10 tasks once, capture sanitized terminal evidence and verifier results, and inspect
   workspace diffs.
4. Reconcile the 10 tasks into the runtime-generated user-scenario matrix and diagnose failures.
5. If and only if the repair gate is met, add a focused regression, implement the minimal tool fix,
   rerun the affected task(s), focused tests and required quality/offline gates.
6. Persist a self-contained Stage 7 Direct baseline report and reconcile roadmap/execution state.

## 5. Validation

Always run the focused tests for any changed tool boundary, then:

```bash
uv run pytest -m 'not live'
uv run ruff format --check .
uv run ruff check .
uv run python -m compileall -q src tests
uv run morrow --help
git diff --check
```

The live evaluation result and the offline quality gate are separate evidence. A Provider-backed
task pass does not replace deterministic regression coverage; an offline unit pass does not count
as a Direct Agent task pass.

## 6. Completion conditions

- all 10 baseline tasks have `PASS`, `FAIL`, `BLOCKED`, `NOT RUN` or `INCONCLUSIVE` status with an
  external verifier result or precise blocking reason;
- the report records task success, runtime/tool-call/token facts when publicly observable, user
  intervention, failure class and unexpected file changes;
- every implemented production change is traced to a reproduced Stage 7-blocking tool defect and
  has focused regression coverage;
- affected task reruns and all required offline/quality gates pass, or remaining blockers are
  recorded without broadening scope;
- verified changes and execution state are committed without absorbing the user's pre-existing
  uncommitted roadmap/evaluation files; no remote push is performed unless separately requested.

## 7. Preserved boundaries

- `ConversationLog` remains the only chat-history writer.
- Ordinary chat remains on `AgentLoop.run_task()` and the existing ToolExecutor path.
- Credentials, reasoning, full tool arguments/results, SDK objects and tracebacks stay out of
  evidence, logs, terminal output and YAML.
- No destructive Git operation, host-wide mutation, live MCP, Workflow implementation, new
  dependency or permission-default change is in scope.

## 8. Outcome

- Complete Direct baseline: `2 PASS / 8 FAIL`; 312 tool calls; approximately 32 minutes 52 seconds.
- Confirmed and repaired two shared blockers: durable policy-denial recovery diagnostics and exact
  read-only project/runtime toolchain exposure inside the native sandbox.
- Repair verification: `MORROW-001` and `MORROW-003` reached viable project command execution; both
  still failed for Agent/budget behavior, so no Workflow, compaction or budget change was added.
- Deterministic gates: focused `37 passed`; host-level macOS sandbox `2 passed`; full offline
  `1081 passed, 2 deselected`; Ruff, compileall, CLI help and diff check passed.
- Acceptance record: `docs/acceptance/stage7-direct-agent-baseline.md`.
