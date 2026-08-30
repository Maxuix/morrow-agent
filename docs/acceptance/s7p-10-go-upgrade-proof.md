# S7P-10 Targeted GO Upgrade Proof

> Date: 2026-08-30
> Gate result: **GO UPGRADE CONDITION MET**
> Scenario result: **FAIL** (`FAIL_MODEL` mechanically)
> Task: EXTERNAL-003, difficult, one authorized post-repair Morrow run

## Verdict

The post-repair proof satisfies the narrow S7P-10 GO upgrade condition. The AgentRun completed
normally, every model request and tool call reached a durable terminal state, no basic-tool or
runtime blocker occurred, and the external verifier failure is attributable to model-produced task
quality rather than Morrow runtime failure.

The task itself did not pass: the frozen verifier reported four failures. This report therefore
records `PASS 0`, `FAIL 1`, `BLOCKED 0`, `NOT RUN 0`, `INCONCLUSIVE 0`. GO is an admission decision
for Stage 7 production work, not a claim that EXTERNAL-003 passed or that the accepted 14-run
baseline met its original quality/statistical thresholds.

## Test basis

| Fact | Value |
|---|---|
| Source revision | `6a088ce9e2a1abf6b3ad815400d3a3bda6f14d82` |
| Runtime behavior revision | `519d4d7df4869cadc609b67fc3e6580c3acbaf2d` |
| Source state | clean at Live-run admission |
| Platform / mode | macOS, ordinary `eval.py run-morrow`, Auto Safe permission profile |
| State isolation | fresh mode-0700 temporary root; fresh workspace and Morrow state |
| Provider / model | `opencode-go/deepseek-v4-flash`, OpenAI-compatible Adapter |
| Provider authorization | explicitly authorized and executed once |
| External bound | 1,800 seconds; not reached |
| Task bytes | `sha256:e177ae7e06e1448e79d755230bdd3c034683ab4864c25453c28dcc6b0112bf2b` |
| Verifier bytes | `sha256:12c627c341426999b018931188f30d393fd06dc3c1e3cdf6b4f3a348d7bb3e71` |
| Workspace baseline | `013287723d9de50ddee690efc34979c02598c7d8` |

No credential value, raw model stream, reasoning, complete tool argument/result or sensitive
traceback was read, printed or committed. The prior 14-run evidence was not modified or repeated.

## User surface inventory

This rerun intentionally covered only the S7P-10 upgrade surface authorized by the user.

| Surface / capability | Implementation evidence | Reachable states or modes | Scenario IDs | Coverage status |
|---|---|---|---|---|
| Fresh evaluator workspace | `eval.py prepare`, frozen EXTERNAL-003 task | baseline failure, changed workspace | S7P10-R1 | covered |
| Ordinary Morrow task execution | `eval.py run-morrow` → bootstrap, AgentLoop, ToolExecutor | completed, model/tool failures, bounded timeout | S7P10-R1 | covered |
| Durable request/tool accounting | AgentRun observation and tool journal | completed/failed terminal facts | S7P10-R1 | covered |
| External task oracle | frozen `eval.py verify EXTERNAL-003` | pass/fail | S7P10-R1 | covered |

No documentation/code conflict was found for this scoped surface. Other Morrow capabilities were
not rediscovered or rerun because the user explicitly requested one EXTERNAL-003 sample.

## Scenario result

| ID | Persona and real task | Preconditions | User actions | Expected | Actual and evidence | Status |
|---|---|---|---|---|---|---|
| S7P10-R1 | Developer asks Morrow to implement stable Reactive Cells propagation | clean current source; fresh baseline/state; configured Provider | prepare EXTERNAL-003; run one ordinary Morrow turn; invoke frozen verifier | pass verifier, or produce an attributable non-runtime terminal with complete accounting and no leaf blocker | AgentRun completed; `react.py` changed; verifier `4 failed`; complete request/tool accounting; no runtime/basic-tool blocker | **FAIL** |

Mechanical evaluator class: `FAIL_MODEL`. The bounded safe evidence hashes are:

- runtime evidence: `sha256:cdf049dd4558efa8cceb400620364372ab2eb0190ebb5ddbea8308475748f4c9`;
- durable safe projection: `sha256:ca1fb7a5f30da6d960c5abd6e356be7144eb8b6c776025bdba0b72cb8f7acf72`;
- verifier output: `sha256:3944070b0ecdf554b509c002df1c74e8b11d06e54c46dc89fec31f4b6be974f7`;
- workspace binary diff: `sha256:37ae808f64671188d49791ea386d7e642137b47d9f9450ad97f3788ad24c5157`.

## Complex journey

S7P10-R1 is one difficult end-to-end journey: a real Provider interpreted the task, inspected the
fresh repository, attempted a project command, recovered from one model-issued invalid command,
wrote and revised the implementation, ran project checks, read results, and returned a normal final
answer. State crossed nine model requests and nine tool executions before the independent verifier
judged the final user outcome.

Only one complex journey was executed. The product supports more, but three journeys would have
violated the user's one-run scope and repeated evidence already accepted from S7P-09.

## Findings

### P2 — Reactive Cells implementation does not satisfy the frozen oracle

- Affected outcome: EXTERNAL-003 functional correctness.
- Reproduction: prepare the frozen task, execute the one recorded Morrow run, then run
  `eval.py verify EXTERNAL-003`.
- Expected: verifier exit 0. Actual: exit 1, four failed tests.
- Reproducibility: one authorized nondeterministic Provider sample; no repeat was allowed.
- Attribution: `FAIL_MODEL`. AgentRun completed normally and no runtime/basic-tool blocker was
  recorded. No workaround was tested.

One `bash` call closed as `failed/invalid_command`; the Agent recovered and later completed four
successful command calls. Evaluator diagnostics report zero invalid-argument calls and zero
basic-tool blockers, so this is not classified as a product defect or gate blocker.

## Coverage and gaps

- Scoped public capabilities exercised: 4/4.
- Planned scenarios executed: 1/1.
- Meaningful transitions covered: fresh baseline → model/tool execution → recovered tool failure →
  workspace mutation → normal AgentRun terminal → failed external oracle.
- Distinct complex journeys: 1; two additional journeys intentionally not run due explicit scope.
- Provider-backed scenarios: 1 executed, 0 blocked, 0 omitted within scope.
- Not covered: another model sample, other tasks, platforms, permission profiles, Provider families,
  cost availability, statistical reliability and Workflow behavior.

## Provider evidence

The single scenario used `opencode-go/deepseek-v4-flash`. It made nine completed model requests with
no retries: 72,079 input tokens, 6,868 output tokens and 78,947 total tokens. Provider cost remained
explicitly unavailable. Safe runtime duration was 65.385 seconds; bounded process wall time was
66.283 seconds. All nine tool calls settled: eight succeeded and one failed/recovered.

## Gate decision and next actions

The previous attempt stopped on an unattributed model-call `internal` failure before producing a
change. Subplan 99 replaced that split path with one attributed failure chain. This rerun did not
exercise an internal failure because all requests completed, but it supplies the other allowed
proof branch: a normal complex AgentRun terminal with complete tool accounting and an independently
attributable `FAIL_MODEL` result.

Therefore S7P-10 upgrades from **CONDITIONAL GO** to **GO**. Stage 7 production Workflow work may
begin under the existing architecture, permission, recovery, budget and validation boundaries.
Recommended follow-up is to start a separately planned Stage 7 production subplan; do not rerun
EXTERNAL-003 or reinterpret this failed task as a quality pass.

## Validation

| Gate | Result |
|---|---|
| Frozen EXTERNAL-003 baseline | verifier exit 1 before Agent execution |
| Authorized Live scenario | one run; normal AgentRun terminal; verifier exit 1 (`4 failed`) |
| Evaluator and AgentRun observability focus | 101 passed in 15.96 s |
| Complete offline suite | 1,285 passed, 2 Live tests deselected in 102.39 s |
| Ruff format/check | 487 files formatted; all checks passed |
| `compileall`, CLI help and `git diff --check` | passed |

## Prior attempt retained

The earlier current-code attempt remains valid historical evidence: 3 model requests, 4/4
successful read-only tools, no workspace change, verifier exit 1 and an unattributed `internal`
terminal. It did not satisfy the upgrade condition and motivated Subplan 99. The new sample adds to
that history without changing or relabeling it.
