# Stage 7 Preflight Reliability — S7P-08 Single-Agent Function Matrix

> Status: verified; pending local integration
> Active subplan: Subplan 89 — S7P-08 Single-Agent Basic Function Matrix Regression
> Branch: `chore/s7p-08-single-agent-matrix`
> Activation base: verified local `main@d84ac0d`
> Source authority: current user request, current code/tests, S7P-08 checklist, completed S7P-01–07

## 1. Current objective

Prove that the current Direct Agent preserves every basic single-Agent function needed before
Stage 7. Freeze an exact 18-cell test ledger, close missing automated evidence, repair only
deterministically confirmed regressions in already-promised behavior, rerun all published Stage 1
through Stage 6 acceptance surfaces and S7P-00 through S7P-07, and publish current evidence.

Detailed scope, matrix definitions, hold points, test lanes and completion rules are owned by
activated Subplan 89.

## 2. Frozen boundaries

- S7P-08 is an offline product-regression gate, not a new feature package.
- The S7P-09 real-model/Pi A/B, repeated task baseline and threshold decision remain deferred.
- Subplan 87's model-owned final stop is current. Do not restore Runtime completion inference or a
  CompletionChecker; validation remains truthful scoped execution telemetry.
- Do not add Workflow/multi-Agent models, dependencies, runtime-policy default changes or public
  event changes.
- Do not run live Provider/model/MCP/network/credential tests.
- The two current-platform macOS Seatbelt integration selectors are a separate real host gate and
  cannot be waived by an unexplained skip.
- Session-owned `ConversationLog` remains the only chat-history writer; ordinary chat continues
  through `AgentLoop.run_task()` and retained `run_turn()` remains its thin delegate.

## 3. Execution order

1. Freeze the exact coverage ledger and current acceptance-reference mapping.
2. Add tests for uncovered positive, failure, recovery, snapshot and entrypoint seams.
3. Reproduce and narrowly repair any current-contract product regression.
4. Run the focused matrix, Stage 1–6/S7P lanes and real host Seatbelt gate.
5. Run the full offline/static/CLI gates and publish the S7P-08 acceptance record.
6. Review, remediate, commit, integrate and retire the verified topic. Do not start S7P-09.

## 4. Required evidence

- Every matrix cell has at least one executed deterministic offline behavior test.
- High-risk paths include executed failure and recovery evidence.
- Provider, Skill, MCP, Preference, Knowledge, ToolSet, permission and context policy evidence is
  frozen or referenced reproducibly by the admitted AgentRun without unsafe payloads.
- Historical acceptance selectors are mapped to current selectors; stale names are reported, not
  silently counted.
- Full offline, Ruff, compileall, CLI help, eval self-check, diff check and both real Seatbelt host
  selectors pass with exact current results.

## 5. Integration

Use the dedicated branch from `d84ac0d`, keep commits small and verified, then fast-forward into
`main` only after Subplan 89's gate is fully satisfied. Push only with authorized remote access;
otherwise record the upstream blocker. A separate user decision is required to begin S7P-09.
