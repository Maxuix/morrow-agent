# Stage 7 Preflight Reliability Repairs — S7P-00 Evaluation Protocol

> Status: completed and integrated locally
> Active subplan: none — Subplan 79 completed and retired
> Integration: local `main` contains the verified implementation through `bbda7de`
> Source authority: the user-requested S7P-00 checklist, current code, and deterministic checks

## 1. Objective

Complete S7P-00 before any Direct Agent reliability repair changes the measured system. Replace the
current ad-hoc CSV/manual-report workflow with a versioned, immutable and machine-checkable run
bundle that can:

1. recreate an equivalent isolated task workspace from one Run Manifest;
2. distinguish verifier outcome, task failure class and tool terminal states;
3. retain raw verifier output, bounded Git evidence, expected/unexpected paths and a structured stop
   reason;
4. mechanically derive aggregate counts without merging failed, denied and blocked outcomes; and
5. enforce two independent repetitions and the frozen Stage 7 entry thresholds.

## 2. Located defect

The existing `evals/code-agent-mini/eval.py` supports only `list`, `show`, `prepare`, `verify` and
`self-check`. Its workspace marker records only dataset version, task ID and gold/baseline state.
`results-template.csv` has no fixed failure enum, tool-state split, stop code, verifier artifact,
diff evidence, environment/configuration snapshot or integrity linkage. The README still permits a
single complete baseline and manual repetition. No committed raw run records exist, so the legacy
report cannot be mechanically regenerated.

This is an evaluation-harness defect. S7P-00 does not require or authorize changes to AgentLoop,
Provider adapters, public events, Operational Store, runtime-policy defaults or production prompts.

## 3. Frozen protocol decisions

- Add a versioned `protocol.toml` as the authority for result classes, tool terminal states,
  repetition count, required evidence and Stage 7 gate thresholds.
- Task result classes are exactly `PASS`, `FAIL_MODEL`, `FAIL_TOOL_CONTRACT`, `FAIL_RUNTIME`,
  `DENIED_POLICY`, `BLOCKED_ENV` and `BUDGET_EXHAUSTED`.
- Tool states are counted independently as succeeded, failed, denied, cancelled and blocked (or the
  documented equivalent). A denial never increments failed.
- A PASS requires verifier success and no unexpected workspace modification. Non-PASS results
  require one fixed class and a bounded evidence-based explanation.
- Every baseline task requires two fresh repetitions. A summary with missing/duplicate repetitions,
  mixed protocol/profile revisions, tampered artifacts or unavailable required evidence is
  incomplete, never passing.
- Freeze the S7P-09 hard thresholds now in the versioned protocol. Changing them later requires a
  new protocol revision; historical manifests retain the original protocol hash.
- A clean evaluator source checkout is required for comparable baselines. Explicit dirty diagnostic
  runs may record a bounded path/hash summary but cannot satisfy the comparison gate.
- Preserve the legacy Stage 7 Direct baseline as historical evidence. Record its known
  `38 failed + 16 denied = 54 non-success` correction in the new S7P-00 acceptance document without
  rewriting that snapshot.

## 4. Run bundle contract

Implement the contract with Python standard library code in the existing evaluation harness:

```text
run bundle/
  run-manifest.json       frozen before Agent execution
  workspace/              newly prepared isolated Git workspace
  runtime-evidence.json   sanitized execution/stop/tool-state facts
  verifier-output.txt     raw external verifier output
  workspace-diff.patch    evaluation-workspace patch evidence
  run-result.json         classification plus hashes of every evidence artifact
```

The manifest captures the evaluator commit and bounded dirty summary, dataset/protocol revisions and
hashes, task/repetition/workspace baseline tree, Agent/entrypoint, Provider/model revision, sampling
parameters, Provider-visible tool snapshot, permission policy, budgets, system-prompt version/hash,
project-instruction sources/hashes and execution/runtime versions. No credential, reasoning, full
tool arguments/results or traceback is accepted.

The CLI must retain the current commands and add narrow lifecycle commands equivalent to:

- start a new run bundle from a strict non-secret profile;
- rebuild and hash-check an equivalent workspace from a manifest;
- finalize a run by invoking the external verifier and recording Git/runtime evidence; and
- summarize finalized run results with completeness and gate diagnostics.

Exact command names may change during implementation if tests reveal a clearer interface, but the
artifact and acceptance contracts above may not be weakened.

## 5. Implementation sequence

1. Add regression tests that expose the missing manifest, classification and aggregation contracts.
2. Freeze protocol v1, strict run-profile shape and per-task expected-change policy without exposing
   Gold implementations to the Agent.
3. Implement manifest creation, content hashes, clean-source comparability and workspace rebuild.
4. Implement verifier/diff/runtime evidence finalization and strict result classification.
5. Implement mechanical aggregation, two-run completeness checks and frozen gate evaluation.
6. Replace the legacy CSV instructions with the run-bundle workflow and publish S7P-00 acceptance
   evidence plus the legacy-statistics erratum.
7. Run focused tests, the dataset self-check, full offline and repository quality gates.

## 6. Validation

```bash
uv run pytest -q tests/test_code_agent_mini_eval.py
.venv/bin/python evals/code-agent-mini/eval.py self-check
uv run pytest -m 'not live'
uv run ruff format --check .
uv run ruff check .
uv run python -m compileall -q src tests evals/code-agent-mini
uv run morrow --help
git diff --check
```

No live Provider, credential, network, Pi or Morrow model run is part of S7P-00.

## 7. Completion and integration

- The focused matrix proves rebuild equivalence, tamper detection, PASS strictness, fixed
  classification, distinct failed/denied/blocked totals, unavailable-not-zero metrics, duplicate
  rejection and two-run completeness.
- S7P-00 acceptance evidence maps every checklist item to a command/test/artifact.
- The implementation receives review from an independent review subagent. All findings are repaired
  and revalidated after the findings are returned.
- Verified changes were fast-forward merged into local `main` through `bbda7de`. The clean topic
  branch and worktree were retired only after ancestry and cleanliness checks. User-owned untracked
  research documents remain untouched.
- No remote push is performed unless separately requested.
