# Stage 7 Preflight Reliability — S7P-09 Repeated Direct/Pi Baseline

> Status: active; model and budget approved, live campaign held pending Pi readiness and clean pins
> Active subplan: Subplan 90 — S7P-09 Repeated Direct Evaluation and Same-Condition Pi Baseline
> Branch: `feat/s7p-09-direct-pi-baseline`
> Activation base: verified local `main@1fd7e229bef276d1a0361e775ce800ade4b318fc`
> Source authority: current user request, S7P-00 protocol v1, completed S7P-08, current code/tools

## 1. Current objective

Build and run the auditable S7P-09 campaign: 10 Morrow tasks × 2 fresh repetitions plus the four
protocol-pinned Pi tasks × 2 fresh repetitions under one exact Provider/model and a frozen
same-condition contract. Publish success, cost, latency, rework, intervention, tool, context and
failure evidence as an immutable Direct baseline for Stage 7.

Detailed data contracts, fairness rules, campaign schedule, thresholds, failure handling and
retention policy are owned by activated Subplan 90.

## 2. Frozen decisions

- S7P-00 protocol v1 thresholds and task/verifier data are immutable in this subplan.
- The primary campaign has exactly 28 admitted runs: Morrow 20 and Pi 8. Formal results are never
  discarded or replaced after observation.
- Morrow and Pi use the same canonical Provider/model/revision, task bytes, sampling contract,
  baseline tree, verifier and 1,800-second external deadline.
- Product-native prompts and tool schemas may differ, but capability/permission boundaries must be
  equivalent, content-hashed and proved offline before live execution.
- Morrow runs use ordinary bootstrap, TaskRun, AgentRun, AgentLoop, ToolExecutor, permission and
  ConversationLog boundaries. Evaluation approval can confirm an already-confined policy decision
  but cannot override denial or grant broader authority.
- Raw events, reasoning, full tool payloads, credentials and tracebacks stay outside Git. Only
  bounded normalized evidence, hashes, summaries and the baseline record are committed.
- S7P-09 measures the frozen Agent. It does not tune thresholds/tasks after results, restore the
  removed CompletionChecker or begin Workflow/S7P-10 work.

## 3. Live hold point

The user approved `opencode-go/mimo-v2.5` for both Agents, a hard campaign ceiling of 5,000,000
tokens, and no currency ceiling. Pi 0.84.2's installed catalog contains the exact model at the same
service endpoint, with a 1,000,000-token context window and 128,000-token maximum output. Before a
formal paid run, the harness must still freeze and verify:

1. the exact served model revision and equal sampling contract;
2. credential readiness for both Agents without exposing or copying credentials;
3. the non-secret comparison plan and equivalent permission mapping;
4. the clean runner/source/evidence pins and bounded no-tool readiness probes.

Credential checks must report only readiness and must never request or print credential values.
Offline harness implementation and testing may proceed before this decision.

## 4. Execution order

1. Implement and offline-test strict comparison planning, Morrow/Pi runners, safe trace
   normalization, permission equivalence and mechanical comparison.
2. Commit the harness and create a clean evaluation worktree pinned to that commit.
3. Resolve the common model, readiness and spend hold point; freeze the 28-entry counterbalanced
   schedule and evidence root.
4. Run and immediately finalize each campaign entry without state/workspace reuse.
5. Validate Morrow's two protocol gates and the four-task stable Pi quality deficit.
6. Publish the immutable baseline/evidence index and exact S7P-09 PASS/FAIL/BLOCKED report.
7. Run final offline/static gates, integrate verified work and stop before S7P-10.

## 5. Completion

Completion requires all 28 primary bundles, complete metrics, exact failure attribution, zero
unaccounted tool calls, the frozen Morrow thresholds, Pi quality deficit ≤ 1, safe evidence
publication and all offline quality gates. Anything less is reported as S7P-09 FAIL or BLOCKED,
never as a conditional PASS.
