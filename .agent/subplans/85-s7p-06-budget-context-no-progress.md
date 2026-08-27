# Subplan 85 — S7P-06 Frozen Double Tool-Round Budget, Context Compression and No-Progress Repair

> Status: planned, pending activation
> Priority: P0; depends on S7P-01, S7P-02, S7P-05 (all completed and integrated into local `main`
> through `d570c5c`)
> Source authority: `docs/research/Stage 7 前 Direct Agent 可靠性修复清单.md` section S7P-06,
> including its frozen budget decision; current code on local `main`; S7P-01 observability and
> S7P-05 completion-truth implementations.

## 1. Objective

Prove, under a fixed 60-tool-round candidate budget, whether the historical 30-round ceiling was a
real blocker or only correlated cost; make context compression semantically survivable through a
deterministic, rebuildable work summary; and replace blind full-budget consumption with
progress-signal-based early stopping. All three changes land behind measured evidence, not tuning
by feel.

S7P-06 does not change any other budget dimension, does not introduce LLM-generated summaries, does
not add a second ConversationLog writer, does not change public event types/fields, does not add
dependencies, and does not run live Provider/Pi/MCP/network/credential tests.

## 2. Pre-activation gate (root task, before branch creation)

1. **Resolve the dirty `main` working tree.** At planning time, `main` (`d570c5c`) carries
   uncommitted modifications in `src/morrow/application/context.py`, `application/prompt.py`,
   `core/completion.py`, `core/domain.py`, `runtime/agent.py`, `services/completion.py`,
   `services/files.py`, `tests/test_s7p05_completion_truth.py`, `tests/test_stage4_execution.py`,
   plus untracked `tests/test_s7p05_audit_remediation.py`. These are post-integration S7P-05
   remediation changes. The S7P-06 topic branch must start from a **verified clean** `main`; the
   root task must first confirm with the user whether this remediation set is to be committed as
   S7P-05 follow-up or checkpointed separately. The S7P-06 task must never absorb, revert or
   silently rebase over this state.
2. Preserve the three user-owned untracked research documents under `docs/research/`.
3. Base the topic branch on the latest verified local `main` after gate 1 resolves; record the base
   commit in `.agent/PLAN.md` at activation.

## 3. Located current facts (verified against code)

| Fact | Location | Consequence for S7P-06 |
|---|---|---|
| Bundled default `max_tool_rounds = 30` | `src/morrow/resources/runtime-policy.toml:4` | The single value this subplan changes to 60 |
| Safety ceiling `AGENT_MAX_TOOL_ROUNDS = 100` | `src/morrow/core/runtime_policy.py:13` | 60 fits; no ceiling change needed |
| Round limit enforced with `AgentStopCode.TOOL_CALL_LIMIT` | `src/morrow/runtime/agent.py:935` | Existing stop semantics; reused, not replaced |
| Combination validator `loop_repeat_limit * loop_max_pattern_cycles <= max_tool_rounds` | `src/morrow/runtime/policy.py:103` | 3×4=12 ≤ 60 stays valid; boundary tests must pin this |
| Loop detection = repeated identical cycle signatures only | `src/morrow/runtime/agent.py:222-230, 1394-1402`; `AgentStopCode.LOOP_DETECTED` | No progress-based judgment exists; repeated-but-varying failures (same error code, different args) are invisible to it |
| Context compression clears old tool results, drops old turns/cycles; no semantic summary | `src/morrow/application/context.py:296-380` (`ContextBuilder._chat`) | Cleared/dropped content loses all task semantics; resume/eval evidence exists only as counts |
| Deterministic checkpoint machinery already exists | `src/morrow/core/context.py` (`ContextCheckpoint`, sections, omissions), `application/checkpoints.py`, `application/context.py:435-466` | The work summary must extend this deterministic pattern, not invent a parallel mechanism |
| Per-request observability records estimated chars, budget, cleared/dropped counts, tool rounds/calls | `observation_runtime.admit_model_request` at `src/morrow/runtime/agent.py:987-999`; `core/observability.py` | S7P-01 already provides the trajectory raw material; Phase A consumes it, no new metric plumbing needed first |
| Terminal aggregates carry stop code, validation outcome, completion outcome/basis/reason | `src/morrow/core/observability.py:151-232` | Comparison report reads these; new no-progress stop code needs an additive migration like v17/v18 |
| Completion gate, one fact-only correction, stop-code mapping | `src/morrow/core/completion.py:278-356`, `services/completion.py:628`, `runtime/agent.py:245-256` | No-progress diagnosis reuses the bounded system-feedback projection pattern; it is not a user message and not a history writer |
| Eval protocol, Run Manifest, 2-repetition rule, frozen taxonomy | `evals/code-agent-mini/protocol.toml`, `eval.py`; `docs/acceptance/s7p-00-evaluation-protocol.md` | The 30v60 comparison runs through this harness unchanged; protocol/profile/evaluator are not modified by this subplan |
| Headless one-shot entrypoint | S7P-01 deliverable (`tests/test_headless_run.py`) | Scripted comparison runs use it; interactive CLI is not part of the measurement path |
| Historical 30-round results | `docs/acceptance/stage7-direct-agent-baseline.md` (immutable snapshot) | Treated as the "before" baseline; never rewritten |

## 4. Frozen design decisions

### Budget freeze

- Change the bundled `runtime-policy.toml` default `max_tool_rounds` from 30 to **60**, once.
  Authority: the user-approved checklist section "S7P-06 已冻结的预算决策", which satisfies the
  AGENTS.md ask-first rule for bundled runtime-policy defaults; record this explicit decision in
  `.agent/LOG.md` and the acceptance document.
- **No other budget moves.** `max_run_seconds` (1800), `max_model_attempts` (40),
  `max_tool_calls` (128), `max_tool_calls_per_cycle` (32), `tool_timeout_seconds` (120),
  `model_retry_limit` (1) and all context/result char limits stay unchanged unless Phase A
  observability evidence shows a specific one also blocking; any such later change is a separate
  recorded decision, never a silent bundle.
- 60 stays fixed through this subplan's repair and the final repeated evaluation. No 45-round
  intermediate step, no per-task upward adjustment. If 60 still fails a task, attribute by
  trajectory first.
- Hard deadline, total call ceiling, cancellation and loop detection remain in force; progress-based
  early stop is added so obvious dead loops need not burn all 60 rounds.
- Loop-detection parameters (`loop_repeat_limit=3`, `loop_max_pattern_cycles=4`) remain valid under
  60 and are not retuned in this subplan.

### Deterministic work summary for compressed content

- When `ContextBuilder._chat` clears tool results or drops turns/cycles, produce a bounded,
  deterministic **WorkSummary** derived only from durable facts already owned by the Session/run
  (durable tool envelopes' safe fields, `ChangeToolFact`/`ValidationFact` values, frozen Outcome
  Contract, known failure codes, validation results). No LLM call, no free-text generation.
- The summary preserves at minimum: user goal and constraints (from the frozen contract/task
  projection, not re-parsed prose); viewed files and key findings (bounded path + fact digest
  references); applied changes with diff-stat digests; validations run and their latest
  `(validator, scope)` outcomes; unresolved errors with safe error codes; declared next step.
- Every section carries its source sequence range and a content hash, so the summary is rebuildable
  from the durable ConversationLog and verifiable against it. The summary never overwrites or
  mutates durable facts; it is a projection.
- The summary contains no reasoning, secrets, raw command text, stdout/stderr, file contents or
  full tool arguments — the same safety envelope as existing durable records and checkpoint
  projections.
- Freeze summary evidence into the run snapshot path so a resumed run reuses the original
  projection instead of re-deriving from an emptied resume input (same contract as S7P-05
  contract/baseline freeze).

### Progress signals and no-progress stop

- Define per-tool-cycle progress signals from existing runtime facts: new relevant diff
  (ChangeToolFact/promotion), new evidence (first read of a previously untouched relevant path),
  error-type change (error code set differs from the previous failing cycle), validation improvement
  (a `(validator, scope)` key moves failed → passed, or a required validation first appears).
- A cycle with none of these signals increments a bounded no-progress counter; any signal resets it.
  Repeated identical cycles are still caught earlier by existing loop detection; no-progress
  detection covers the "varying but sterile" case loop detection cannot see.
- At the configured threshold (default candidate: 3 consecutive no-progress cycles, pinned by tests
  and recorded in policy only if evidence supports the value), inject exactly one bounded fact-only
  diagnosis through the existing completion-feedback projection pattern (reason codes + next-action
  codes + changed-path facts; no prose generation). If the following cycles still show no progress,
  terminate with a new deterministic stop code `NO_PROGRESS`.
- `NO_PROGRESS` is added to `AgentStopCode` as an additive enum member with terminal-aggregate and
  migration support (mirroring the v18 additive migration); it must never be emitted for a run that
  produced new diffs or validation improvement within the window.
- The diagnosis projection is not a user message, not a ConversationLog writer, and must survive
  the same cancellation/error/history-pairing legality tests as the S7P-05 correction feedback.

## 5. Ordered execution

### Phase A — Measurement first (no production behavior change)

1. Build a trajectory extraction over the S7P-01 observability store: per model request, plot
   estimated request chars vs budget, cleared/dropped counts, tool rounds/calls, stop code, and
   join with terminal aggregates (validation outcome, completion outcome/reason). Output is a
   machine-readable report artifact, not a new runtime feature.
2. Re-run the historically failed 30-round tasks (MORROW-002 … MORROW-005 as applicable) through
   the frozen eval harness on the current integrated code, capturing per-round trajectories.
   Classify each failure against the S7P-00 taxonomy; identify whether termination was
   budget-shaped, loop-shaped, contract-shaped or completion-shaped. This step writes the "before"
   half of the comparison report.
3. If Phase A shows a non-tool-round budget also terminating tasks, record it as a separate
   finding; do not change that budget in this subplan.

### Phase B — Budget change to 60 (test-first)

4. Lock failing policy tests: bundled default parses to 60; combination validator holds
   (`3*4 <= 60`); user overlay boundaries unchanged; ceiling still 100.
5. Change `runtime-policy.toml` `max_tool_rounds` to 60. Add/extend boundary tests: a scripted run
   reaching exactly round 60 terminates with `TOOL_CALL_LIMIT` and legal history; rounds 30–60
   behave identically to 1–30; loop detection still fires before the new ceiling on identical
   cycles; `max_model_attempts=40` interplay is pinned (attempt limit still precedes round limit
   where the existing test matrix requires it).
6. Record the explicit bundled-default decision in `.agent/LOG.md` and the acceptance doc.

### Phase C — Deterministic work summary (test-first)

7. Lock failing tests: after forced compression (small char budget + scripted multi-cycle run), the
   model-visible context still answers: task goal, which files were changed, which validations ran
   and their outcomes, what error is unresolved, what the declared next step is. Baseline today:
   these answers are lost when cycles are cleared/dropped.
8. Implement the WorkSummary projection at clear/drop time inside the existing context pipeline;
   carry provenance ranges + hashes; verify rebuildability from the durable log in tests.
9. Freeze/rehydrate summary evidence across fresh and resumed durable runs; prove a resumed run
   does not repeat an operation whose earlier failure is recorded only in compressed-away cycles.
10. Safety tests: summary sections contain no reasoning/secret/raw-payload markers; oversized or
    unverifiable summary input fails closed to the existing clear/drop behavior plus a bounded
    diagnostic, never to fabricated content.

### Phase D — Progress signals and no-progress stop (test-first)

11. Lock failing matrices:
    - recognized: same `invalid_arguments` repeated with varying args; `not_found` on successive
      guessed paths; repeated cycles with no new diff on a change task;
    - not killed: new diff each window; validation improving; error codes changing;
      explanation/unspecified tasks making normal progress;
    - one diagnosis then stop: after diagnosis, continued sterility terminates `NO_PROGRESS`;
      after diagnosis, resumed progress resets and the run may complete normally.
12. Implement signal extraction from existing facts, the bounded counter, the single diagnosis
    projection, and the `NO_PROGRESS` stop code with additive migration and terminal aggregates.
13. Prove no false kills on the S7P-05 acceptance scenarios and the scripted Direct-agent suite:
    every previously passing scripted run still completes with its original terminal state.

### Phase E — Fixed 30v60 comparison report

14. Under identical Run Manifests (same evaluator revision, dataset, profile shape, scripted/
    recorded conditions, 2 repetitions per task per arm), compare the frozen 30-round historical
    arm against the fixed 60-round candidate on: pass/fail taxonomy, token usage and cost (with
    explicit `unavailable` where absent), wall time, invalid-argument rate, first relevant read /
    first effective write / first validation round, rework count, unexpected files, compression
    events and recovery outcomes.
15. Publish the comparison as `docs/acceptance/s7p-06-budget-30v60-comparison.md`, presenting
    success rate and cost together; no conclusion may hide a cost regression behind a pass-rate
    gain or vice versa. The report states the frozen decision: 60 remains for the final evaluation
    regardless of per-task temptation.

### Phase F — Closeout

16. Publish `docs/acceptance/s7p-06-budget-context-no-progress.md` mapping every S7P-06 checklist
    acceptance box to its test/report evidence.
17. Update `docs/ARCHITECTURE.md` only where actual structure changed (work summary projection,
    progress-signal stop); update `.agent` execution state.
18. Same-task `gpt-5.6-luna` / `max` read-only review of the complete base…HEAD diff; reproduce and
    fix every confirmed finding; rerun affected and full offline gates; clean coherent commits.
19. The implementation task does not merge, push, delete its branch/worktree, touch the three
    user-owned research documents, or start S7P-07. Root task verifies ancestry/cleanliness,
    fast-forward merges into local `main`, retires clean resources.

## 6. Required test matrices

- **Policy**: default 60 parses; combination validator at and around boundaries; overlay cannot
  exceed ceiling; exact round-60 termination with legal ToolCycle/history; attempt-vs-round
  precedence unchanged.
- **Summary**: goal/changes/validations/errors/next-step answerable after compression; provenance
  range and hash verify against durable log; rebuild determinism; resumed-run no-repeat; safety
  redaction; fail-closed on unverifiable input.
- **No-progress**: every recognition and false-kill matrix row in Phase D; diagnosis projection is
  bounded, non-user, non-history-writer; cancellation/error mid-diagnosis keeps call pairing legal;
  `NO_PROGRESS` never co-occurs with new-diff or validation-improvement evidence.
- **Durability**: budget exhaustion, `NO_PROGRESS`, `LOOP_DETECTED` and normal completion each
  leave legal ToolCycles, exact stop codes and continuable TaskRun state; old snapshot/row
  compatibility through the additive migration.
- **Regression**: full S7P-01…S7P-05 acceptance suites unchanged; no public event type/field added;
  YAML/events/DB/terminal show no credentials, reasoning, full params/results or tracebacks.

## 7. Validation

```bash
uv run pytest -q tests/test_policy.py tests/test_agent_limits.py
uv run pytest -q tests/test_agent_tool_loop.py tests/test_context_runtime.py
uv run pytest -q tests/test_agent_run_observability.py tests/test_operational_store.py
uv run pytest -q tests/test_s7p05_completion_truth.py tests/test_s7p05_audit_remediation.py
uv run pytest -q tests/test_stage4_recovery.py tests/test_stage4_journal.py
uv run pytest -q tests/test_code_agent_mini_eval.py tests/test_headless_run.py
uv run pytest -m 'not live'
uv run ruff format --check .
uv run ruff check .
uv run python -m compileall -q src tests
uv run morrow --help
uv run morrow run --help
git diff --check
```

If sandboxed `uv` cannot reach its cache, use the worktree's synchronized `.venv/bin/python -m
pytest`, `.venv/bin/ruff` and `.venv/bin/morrow` equivalents and record the restriction plus exact
fallback. No live Provider/model/Pi/MCP/network/credential test is allowed.

## 8. Boundaries and exclusions

- Do not change `max_run_seconds`, model attempts, total tool calls, tool timeout or any char
  budget; do not tune loop-detection parameters; do not raise the 100-round safety ceiling.
- Do not introduce LLM-generated summaries, a second history writer, new public event types/fields,
  dependencies, or permission/tool-effect changes.
- Do not modify the eval protocol, taxonomy, thresholds or historical baseline documents; the
  30-round baseline is immutable evidence.
- Do not implement S7P-07 (retry/backoff/steering) or any Workflow concept.
- Do not run live/network tests; scripted Providers and recorded fixtures only.
- Do not start from or sweep up the current dirty `main` tree (section 2 gate).

## 9. Acceptance (maps to the S7P-06 checklist)

- [ ] `max_tool_rounds=60` policy, boundary-combination, budget-exhaustion and loop-detection tests
      pass (Phase B matrix).
- [ ] 30-round vs fixed-60-round comparison report committed, presenting success rate and cost
      together (Phase E artifact).
- [ ] The final evaluation keeps 60 rounds throughout; no per-task ceiling raise (decision record).
- [ ] After compression, user goal, relevant changes, validation failures and next step remain
      answerable (Phase C matrix).
- [ ] After restart/recovery, the Agent does not repeat an operation whose failure exists only in
      compressed-away cycles (Phase C durability tests).
- [ ] Consecutive repeated `invalid_arguments` / `not_found` / no-diff patterns are recognized
      (Phase D matrix).
- [ ] No-progress stop never kills runs producing new diffs or validation improvement (false-kill
      matrix).
- [ ] Every budget exhaustion leaves legal ToolCycles, an exact stop code and a continuable
      TaskRun state (durability matrix).

Each completion claim additionally attaches: problem reproduction, code/contract change, automated
verification, scripted-task verification, safety/persistence check, metric impact, and unresolved
items with their destination — per the checklist's implementation discipline.
