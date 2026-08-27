# Subplan 90 — S7P-09 Repeated Direct Evaluation and Same-Condition Pi Baseline

> Status: active; Phase A harness work may proceed
> Branch: `feat/s7p-09-direct-pi-baseline`
> Activation base: verified local `main@1fd7e229bef276d1a0361e775ce800ade4b318fc`
> Dependency: Subplan 89 / S7P-08 passed and is integrated locally
> Live hold point: no formal campaign may start until §5 is satisfied

## 1. Outcome

Create the immutable Direct single-Agent baseline required by Stage 7:

- run all 10 Code Agent Mini Eval tasks twice with Morrow from fresh workspaces;
- run the four protocol-pinned comparison tasks twice with Pi Agent 0.84.2;
- use the same Provider, exact model, task bytes, sampling contract, workspace baseline, verifier,
  external deadline and equivalent capability policy for each paired Morrow/Pi run;
- mechanically classify every run, compare repeated quality, and publish complete token plus
  available cost, latency, tool, rework, intervention and context evidence;
- freeze the resulting Direct baseline as a content-hashed version for Stage 7 comparisons.

The primary campaign contains exactly 28 formal runs: 20 Morrow runs and 8 Pi runs. A run is never
silently replaced after seeing its result.

## 2. Activation facts

1. S7P-08 passed all 18 capability cells, Stage 1–6/S7P lanes, both real macOS Seatbelt tests and
   the complete offline gate. S7P-09's dependency is satisfied at `main@1fd7e22`.
2. S7P-00 protocol v1 already freezes the seven result classes, five tool terminal states, two
   repetitions, four Pi task IDs and all Stage 7 thresholds. Threshold edits require a new protocol
   version and are outside this subplan.
3. The installed Pi executable reports `0.84.2`. Phase A must additionally freeze its resolved
   package identity, executable target and content hash; a version string alone is insufficient.
4. The user approved `opencode-go/mimo-v2.5` for both Agents. Pi 0.84.2's installed catalog contains
   that exact provider/model and service endpoint with a 1,000,000-token context window and
   128,000-token maximum output. Served revision, sampling and credential readiness still require
   no-secret probe evidence before a formal same-model A/B.
5. The evaluator can start/finalize bundles, execute either Agent through its confined runner,
   normalize both traces, enforce the paired schedule and mechanically compare completed campaign
   evidence. Formal execution remains gated by readiness and clean immutable pins.
6. Morrow headless mode fails closed on approval. A fair automated campaign therefore needs a
   bounded evaluation ApprovalPort that uses the ordinary CapabilityPolicy path; it may not bypass
   preflight, permission snapshots, ToolExecutor, AgentLoop, TaskRun or ConversationLog ownership.

## 3. Ownership

Primary ownership:

- `evals/code-agent-mini/eval.py` and bounded companion fixtures/scripts in that directory;
- the strict comparison-plan/profile templates and safe result schemas;
- `tests/test_code_agent_mini_eval.py` and focused offline runner/comparison tests;
- `evals/code-agent-mini/README.md`;
- `docs/acceptance/s7p-09-repeated-direct-pi-baseline.md`;
- a bounded, sanitized baseline summary/index suitable for Git;
- `.agent/` execution state for Subplan 90.

Narrow production changes are permitted only if a deterministic test proves that existing S7P-01
through S7P-08 observation or headless composition cannot truthfully supply a required safe fact.
No production behavior is changed merely to improve a score.

## 4. Non-goals and prohibited shortcuts

- Do not change `protocol.toml` v1 thresholds, task set, Gold state, verifier or expected paths.
- Do not start Workflow, AgentDefinition, Scheduler, multi-Agent or S7P-10 work.
- Do not choose a different model for one Agent, mix Provider endpoints or compare different task
  revisions, sampling values, project instructions or verifier code.
- Do not count the current `opencode-go/mimo-v2.5` configuration unless the exact same served model
  and revision become verifiably available to Pi.
- Do not use a live pytest test as the campaign runner. The formal campaign uses create-only run
  bundles; pytest remains deterministic and offline.
- Do not commit credentials, reasoning, assistant transcripts, complete tool arguments/results,
  raw Provider payloads, raw Pi JSONL, command output or tracebacks.
- Do not infer missing usage/cost as zero, infer a Provider/model revision from a display name, or
  treat an incomplete summary as a failure-class result.
- Do not discard an admitted run, edit its bundle, rerun the same `(agent, task, repetition)` key,
  cherry-pick favorable samples or relabel a failure in prose.
- Do not relax permissions, enable task network access or give either Agent access to Gold/expected
  path policy. Provider transport is not a tool capability.
- Do not restore the removed Runtime CompletionChecker. The external verifier and evaluator own
  assessment; the product runtime still accepts a valid model `stop`.

## 5. Live campaign hold point

Before the first formal campaign run, freeze one strict `comparison-plan.json`. The user approved
the exact Provider/model, a 5,000,000-token hard ceiling and no currency ceiling. The plan must
prove:

1. **Common model.** Both Morrow and Pi resolve the same Provider family, endpoint/service,
   canonical model ID and model revision. Aliases are accepted only when a no-secret readiness
   response proves the same canonical response model.
2. **Credential readiness.** Morrow's Provider test and `pi auth check --json --no-refresh` succeed
   without `--credentials`. Only presence/status is recorded; no token is read, printed or copied.
3. **Sampling equality.** Temperature, top-p, seed and max output are numerically equal when both
   sides expose them. An unsupported value is recorded as `provider_default_verified` in the
   comparison plan and must be unsupported/equivalent on both sides; `unavailable` is not silently
   treated as equality.
4. **Exact model capability.** Both sides agree on context window and maximum output. Morrow must
   admit RunPolicy v2 for that exact model; Pi must resolve the same capability from its frozen
   catalog/runtime evidence.
5. **Budget approval.** The user approved an explicit 5,000,000-token campaign maximum and no
   currency maximum. The harness stops scheduling before the token ceiling. Cost is recorded when
   supplied and otherwise remains explicitly unavailable; it is not a campaign completeness gate.
   Already admitted runs finalize truthfully.
6. **External watchdog.** Both agents use the same 1,800-second per-run external deadline. It is an
   evaluator `budget_exhausted` result, not a Morrow/Pi internal stop code. Both use a 120-second
   per-tool execution bound where the product supports a tool timeout.
7. **Source pins.** The Morrow Agent/evaluator commit is a clean immutable commit, Pi is exactly
   0.84.2 with matching package/executable hashes, and dataset/protocol/config hashes match the
   comparison plan.
8. **Permissions and tools.** The equivalence proof in §7 passes offline. Each side has workspace
   read/write, search, editing and bounded project-command capability; task network, external
   filesystem, credential access, Git writes and privilege escalation are denied.
9. **Evidence root.** The raw evidence directory is explicit, outside the evaluator checkout,
   create-only, mode-restricted and has enough space. A content-hashed safe index is destined for
   Git; sensitive raw streams remain outside Git.
10. **Self-check.** All 10 baselines fail and all 10 Gold states pass immediately before campaign
    freeze.

If any item is absent, Phase A/B may continue offline but the live campaign remains on hold. This
is not a subjective waiver or a reason to substitute a different model.

## 6. Frozen primary campaign

### 6.1 Morrow lane

- Tasks: all `MORROW-001`…`MORROW-006` and `EXTERNAL-001`…`EXTERNAL-004`.
- Repetitions: exactly `1` and `2`, each with a fresh bundle, workspace, state root, Workspace ID,
  Session, TaskRun and AgentRun.
- Entrypoint: ordinary production bootstrap/AgentLoop through an evaluation runner that captures
  the same safe observations as `morrow run`.
- No memory, Preference, Skill usage, chat, state root or working tree is reused between runs.

### 6.2 Pi lane

- Tasks: exact protocol list `MORROW-003`, `MORROW-005`, `EXTERNAL-003`, `EXTERNAL-004`.
- Repetitions: exactly `1` and `2`, each from the same evaluator baseline tree as its Morrow pair.
- Runtime: Pi Agent `0.84.2`, non-interactive JSON mode, fresh/disabled session persistence,
  project context enabled when the same files are visible to Morrow, user packages/skills/prompt
  templates disabled, and only the explicit evaluation policy extension enabled.
- The policy extension may enforce the frozen workspace/network/process limits and emit safe facts;
  it may not add planning, editing, validation or task-solving guidance.

### 6.3 Counterbalanced schedule

Create and hash the complete 28-entry schedule before any run. Runs are sequential, not concurrent.
For paired tasks:

- repetition 1 executes Morrow then Pi;
- repetition 2 executes Pi then Morrow.

The remaining six Morrow-only tasks are distributed deterministically between paired blocks. This
reduces time/order bias without choosing order after observing outcomes. Schedule mutation creates
a new campaign ID and invalidates prior comparability.

### 6.4 Immutable run rule

Readiness failures before the first model request do not consume a formal run key. Once a model
request is admitted, the run is formal and must be finalized—even after auth expiry, Provider
outage, cancellation, watchdog expiry or harness interruption. A process crash is recovered into
bounded evidence when possible; it is never replaced by a favorable rerun.

## 7. Same-condition contract

| Surface | Frozen comparison rule |
|---|---|
| Task | Same canonical UTF-8 task bytes and task hash; expected paths/Gold remain evaluator-only |
| Baseline | Same task baseline tree hash; separate fresh workspaces with no carried state |
| Provider/model | Same service family, canonical response model and revision; aliases alone do not prove equality |
| Sampling | Same supported numeric values or a documented equal provider-default contract |
| Context | Same context-window/max-output capability; Morrow policy v2 required |
| Deadline | Same external 1,800 s watchdog; no hidden product watchdog added |
| Filesystem | Workspace read/write only; external paths and credentials denied |
| Commands | Same project toolchain/PATH/env allowlist; 120 s tool bound; network and Git mutation denied |
| Human input | No steering, follow-up or manual correction; any intervention is counted and makes the run comparison-ineligible |
| Project rules | Same visible AGENTS/CLAUDE/project-instruction bytes; product system prompts remain native and are hashed separately |
| Tools | Native product tools may differ, but read/search/edit/create/command capability categories and denial boundary must be equivalent and snapshotted |
| Verifier | Exact same external verifier code, arguments, timeout and expected/unexpected path policy |

### Morrow approval mapping

Use workspace-scoped `AUTO_SAFE`. A dedicated EvaluationApprovalPort may approve only an ordinary
CapabilityPolicy `REQUIRE_APPROVAL` decision whose frozen intent is already confined to the task
workspace and free of denied risk flags. It cannot override `DENY`, grant full access, approve
network/Git mutation, bypass ToolExecutor or mutate the permission snapshot. Each approval is
counted as harness policy automation, not a user intervention.

### Pi permission mapping

Use the native tool set needed for the equivalent capability categories plus one explicit,
content-hashed policy extension. The extension blocks out-of-workspace paths and prohibited command
classes before effect, applies the same tool timeout, and reports `denied`/`blocked` safely. It must
not rewrite prompts, arguments, results or successful edits. An offline conformance matrix feeds
the same allowed/denied intents to Morrow's policy and the Pi adapter; any mismatch blocks live A/B.

## 8. Harness data contracts

### 8.1 Comparison plan

Add a strict, canonical, create-only comparison plan containing:

- campaign ID/schema and protocol/dataset/config hashes;
- Morrow commit/wheel or source hash and Pi package/executable hashes;
- both full non-secret profiles and their hashes;
- common Provider/model/sampling/capability contract;
- permission-equivalence map and policy-extension hash;
- external/tool deadlines, total token ceiling and explicit nullable currency ceiling;
- exact 28-entry schedule;
- raw evidence root identity and start-not-before timestamp.

Reject unknown fields, mutable aliases, missing revisions, sensitive keys/text, mixed profiles,
duplicate keys and any current source dirt. Plan files are immutable after the first run admission.

### 8.2 Safe normalized trace facts

Both runners normalize private event streams in memory into the same bounded schema:

- model round/attempt count and terminal stop mapping;
- tool calls by ordinal, round, capability family and terminal state;
- normalized workspace-relative path set and validator kind/status only;
- first relevant read/search round;
- first effective write round;
- first validation round and latest validation outcome;
- compaction/overflow recovery/retry counts;
- input/output/total tokens, available provider/runtime cost and external duration;
- denied/blocked/invalid/unaccounted counts;
- mechanically defined rework and intervention counts.

No raw argument, result, stdout/stderr, prompt, response, reasoning or traceback enters this schema.
The runner may transiently inspect raw data to derive a fact, then must discard it or retain it only
in the protected non-Git evidence root with an integrity hash.

### 8.3 Metric definitions

- `rounds`: completed assistant/model turns, counted from final authoritative events.
- `rework_count`: an effective write to a path already changed earlier in the same run after a
  failed validation or an intervening effective write. Formatting a new path once is not rework.
- `user_interventions`: steering, follow-up, manual prompt or manual approval after formal start;
  the evaluation ApprovalPort is policy automation and is counted separately.
- `first_relevant_read`: first successful read/search touching the task's evaluator-known allowed
  scope; the scope is never shown to the Agent.
- `first_effective_write`: first completed tool after which the workspace tree differs from the
  frozen baseline on an allowed path.
- `first_validation`: first recognized project validator execution, not arbitrary exit-zero shell.
- `cost`: sum of Provider/runtime-reported per-response cost when complete; otherwise explicitly
  unavailable, never zero. Do not reconstruct cost from a mutable public price table.

### 8.4 Pi adapter

Parse Pi's authoritative `message_end`, `turn_end`, `tool_execution_start/end`, compaction and retry
events. Sum final assistant usage exactly once, require each started tool to reach one terminal end,
map Pi stop reasons to the frozen evaluator stop codes and fail closed on unknown event shapes.
Offline fixtures cover success, tool failure, invalid arguments, aborted/error/length stops,
watchdog termination, missing terminal events, retry, compaction and secret-bearing raw data.

### 8.5 Morrow adapter

Use the production composition root and safe AgentRun/ToolRunContext observations. Build each
`runtime-evidence.json` mechanically from terminal metrics and bounded process-local facts. The
external harness supplies duration and intervention/rework facts; it may not alter the terminal
finish reason or turn history.

### 8.6 Mechanical comparison

Add a command that validates the Morrow 20-run summary plus exactly eight Pi bundles and the frozen
comparison plan. It must reject missing/extra/duplicate runs, profile drift, schedule drift, bundle
tampering, unavailable required metrics, mixed model/task/verifier hashes or comparison-ineligible
runs.

The command emits a sanitized `comparison-summary.json` and `baseline.json`; it does not print raw
events or verifier output.

## 9. Frozen gate calculation

### 9.1 Morrow gate

For **each** repetition independently:

- simple/medium tasks: `5/5 PASS`;
- difficult tasks: at least `2/5 PASS`;
- total: at least `7/10 PASS`;
- `FAIL_RUNTIME = 0`;
- basic-tool blockers `= 0`;
- successful change tasks without required relevant diff `= 0`;
- unexpected paths `= 0`.

Across all 20 runs:

- `invalid_arguments / total_tool_calls <= 0.01` and no task failure is caused by invalid arguments;
- every tool call reaches exactly one `succeeded`, `failed`, `denied`, `cancelled` or `blocked`
  terminal state;
- tokens, cost, duration, rounds, rework and interventions are all available.

### 9.2 Pi comparison gate

For each of the four paired tasks, define a stable task pass as both repetitions being `PASS`.
Compute:

```text
quality_deficit = Pi stable task passes - Morrow stable task passes
```

The comparison passes when:

- all four task pairs and both repetitions are complete and comparable;
- `quality_deficit <= 1`;
- Morrow has no basic tool/runtime blocker on a paired task where Pi has none;
- every failure has one mechanical S7P-00 result class and bounded reason code;
- all paired token/cost/time/rework metrics are complete.

A negative deficit is reported as data, not clamped into a superiority claim. Two repetitions do
not support a public statistical ranking.

### 9.3 Variance handling

The primary 28 runs remain authoritative. If a failed deficit could reflect sampling variance,
additional repetitions may be proposed only as a separate, predeclared diagnostic campaign with a
new plan hash and new user-approved Token ceiling. They cannot overwrite primary keys or convert a
failed v1 gate into PASS without a separately approved protocol/campaign decision.

## 10. Failure and remediation protocol

1. Pre-admission auth/config/source readiness failure pauses scheduling and does not consume a run
   key.
2. Post-admission Provider outage/auth expiry finalizes as `BLOCKED_ENV`; external watchdog as
   `BUDGET_EXHAUSTED`; agent/runtime protocol failure as `FAIL_RUNTIME`; evaluator corruption makes
   the bundle invalid and the campaign incomplete.
3. A verifier failure with legal tools and complete evidence is `FAIL_MODEL` unless a precise
   advertised-tool contract failure mechanically caused the stop.
4. Denied policy attempts remain `denied` facts. `DENIED_POLICY` is the primary result only when the
   frozen policy prevents the required task effect.
5. Human interpretation may explain facts but cannot change the machine class.
6. If a harness defect is found after a formal run, preserve the campaign as invalid diagnostic
   evidence, repair offline and start a new full campaign ID.
7. If a Morrow product defect is confirmed, preserve the failed baseline, add a minimal regression,
   repair it on a new commit, rerun S7P-08, and request approval for a new full 28-run campaign.
   Selective reruns cannot be mixed with the old commit.
8. Model-quality failure without a runtime/tool defect produces an honest S7P-09 FAIL/NO-GO input;
   do not tune the prompt, thresholds or task after seeing results.

## 11. Evidence retention and publication

The protected raw root retains create-only manifests, safe runtime evidence, verifier output,
workspace status/diffs, result bundles and optionally raw agent streams. Raw streams are mode
restricted, hash-indexed and never committed because they can contain reasoning or full payloads.

Git receives only:

- the strict comparison plan with secrets and absolute private paths removed;
- `comparison-summary.json` with per-run class and bounded aggregate metrics;
- `baseline.json` containing the immutable Direct baseline ID and all source/profile/result hashes;
- `evidence-index.json` containing content hashes and byte counts for raw bundles;
- the human acceptance report.

The acceptance report must state the exact local retention/backup status. If raw evidence has no
authorized durable remote, record that as a publication blocker; do not claim globally durable
auditability.

## 12. Execution sequence

### Phase A — Offline harness and contract

1. Add failing tests for comparison-plan validation, exact 28-key schedule, profile equivalence,
   immutable pins and sensitive-field rejection.
2. Add Morrow/Pi normalized trace fixtures and prove tool pairing, usage deduplication, stop mapping,
   rework/intervention/context facts and raw-data exclusion.
3. Add the permission-equivalence conformance matrix and bounded EvaluationApprovalPort.
4. Add create-only single-run execution, preflight and mechanical compare commands.
5. Test interruption/restart, partial output, duplicate admission, budget ceiling and evidence-root
   confinement without network or real credentials.
6. Run focused and complete offline gates; audit the harness for false PASS paths and secret leaks.
7. Commit the verified harness. This clean commit becomes the candidate evaluator/Agent pin.

### Phase B — Configuration freeze and hold-point approval

8. Create a clean dedicated evaluation worktree at the harness commit. All official `start`, run,
   finalize and compare commands execute there; `.agent/` updates occur elsewhere.
9. Select a mutually supported exact Provider/model. Run no-secret readiness checks and one bounded
   no-tool probe on each Agent only after the user approves the probe and campaign Token ceiling.
10. Freeze profiles, common capability/sampling contract, Pi package hashes, permission adapter,
    raw evidence root and counterbalanced schedule.
11. Run the eval self-check and comparison preflight. Present the exact 28-run plan, expected upper
    cost/token bounds and any unsupported sampling facts for final confirmation.

### Phase C — Formal primary campaign

12. Execute schedule entries sequentially. Before each admission, revalidate remaining campaign
    budget, source/profile hashes, credential readiness and target workspace freshness.
13. After each formal run, immediately normalize, finalize, hash and validate its bundle. Do not
    inspect Gold or tune later prompts based on the result.
14. On safe pre-admission environmental failure, pause. On post-admission failure, finalize the run
    and continue only if the global campaign budget and evidence integrity permit.

### Phase D — Aggregate and review

15. Generate the Morrow summary, Pi subset summary, paired comparison and immutable baseline.
16. Independently recompute all hashes, run counts, thresholds, quality deficit and tool accounting
    from finalized bundles. Review every non-PASS classification against the frozen mechanical
    rules without changing it.
17. If the gate fails, decide only among confirmed harness defect, confirmed product defect,
    model-quality result or environment blocker using §10. Do not start a replacement campaign
    without its required approval.

### Phase E — Publish and close

18. Publish the S7P-09 acceptance report with per-run and cross-repetition tables, paired A/B,
    failure attribution, cost/time/rework distributions, evidence hashes and residual risks.
19. Run the final offline/static/CLI gates from the source branch; confirm no secret/raw stream was
    staged and the baseline files validate from scratch.
20. Commit coherent verified evidence, fast-forward into `main`, verify containment and retire the
    clean topic. Do not start S7P-10 automatically.

## 13. Offline validation

```bash
uv sync
uv run pytest -q tests/test_code_agent_mini_eval.py
uv run python evals/code-agent-mini/eval.py self-check
uv run pytest -q tests/acceptance/test_s7p08_single_agent_matrix.py
uv run pytest -m 'not live'
uv run ruff format --check .
uv run ruff check .
uv run python -m compileall -q src tests evals/code-agent-mini
uv run morrow --help
uv run morrow run --help
uv run python evals/code-agent-mini/eval.py --help
git diff --check
```

The real campaign commands are not pytest tests and are recorded separately with exact plan/bundle
hashes. If `uv` cache access fails, use the synchronized `.venv/bin/` equivalents and record the
fallback. No command is reported as passed unless it actually completed.

## 14. Completion gate

Subplan 90 is complete only when:

- the strict harness and permission-equivalence tests pass offline;
- the exact common Provider/model and 5,000,000-token ceiling with no currency ceiling were
  explicitly approved;
- all 28 primary run bundles are complete, valid, immutable and comparison-eligible;
- Morrow's two repetition gates pass;
- the four-task stable Pi quality deficit is at most one;
- no Morrow runtime/basic-tool blocker, unaccounted tool call, unexpected path or false-diff success
  remains;
- all required usage/cost/time/rework/intervention metrics are present;
- every failure has a mechanical class and exact evidence;
- the Direct baseline ID and source/profile/evidence hashes are published safely;
- full offline/static gates pass and the branch is clean and verified.

If these conditions do not hold, close the evidence as S7P-09 FAIL or BLOCKED with the precise
reason. Do not issue Stage 7 readiness; S7P-10 remains a separate explicit user decision.
