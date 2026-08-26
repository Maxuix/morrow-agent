# Subplan 79 — S7P-00 Reproducible Evaluation Protocol

> Status: active
> Branch: `codex/feat/s7p-00-eval-protocol`
> Dependency: none

## Goal

Freeze the measurement contract before Direct Agent reliability repairs begin. Turn the existing
Code Agent Mini Eval into a reproducible, immutable, machine-checkable evaluation lane without
changing production Agent behavior.

## Ownership

- `evals/code-agent-mini/eval.py` and new versioned protocol/profile artifacts in that directory;
- task-level expected-change metadata in `evals/code-agent-mini/manifest.toml`;
- focused regression coverage in `tests/test_code_agent_mini_eval.py`;
- `evals/code-agent-mini/README.md` and a new S7P-00 acceptance record;
- `.agent/` execution state for Subplan 79.

The subplan does not own `src/morrow/`, runtime-policy defaults, public events, the Operational Store,
production prompts, Provider behavior or the user-owned untracked research documents.

## Defect reproduction

1. `eval.py --help` has no run-manifest, record or aggregate lifecycle.
2. `.morrow-eval.json` cannot identify the evaluator commit, dataset hash, model/config/tool/budget
   snapshot, repetition or workspace baseline tree.
3. `results-template.csv` cannot represent the fixed S7P-00 taxonomy or distinguish failed, denied
   and blocked tool outcomes.
4. Verifier output, stop reason and workspace diff are not durable artifacts; summary tables are
   manual prose.
5. The documented one-run baseline policy cannot establish repeatability.

## Frozen data contracts

### Protocol

Create a versioned `protocol.toml` containing:

- protocol ID/version and minimum repetitions (`2`);
- the seven exact task result classes;
- distinct terminal tool states;
- required manifest/runtime/verifier/diff/result evidence;
- the Stage 7 thresholds stated in S7P-09 of the approved checklist;
- the four fixed Pi comparison task IDs;
- explicit rules that unavailable usage is not zero and incomplete evidence cannot pass.

Tests assert the frozen values. Future threshold edits require a new protocol version, not an
in-place relaxation.

### Strict run profile

Provide a JSON template and strict validation for non-secret operator inputs:

- Agent ID/version/entrypoint;
- Provider ID/revision and model ID/revision;
- explicit sampling values or explicit unavailable markers;
- Provider-visible tool names plus stable schema hashes/content;
- permission/sandbox policy;
- independent token/context/round/deadline/tool-call/model-attempt budgets;
- system-prompt version/hash and project-instruction source/hash list;
- Python/Morrow/agent execution versions.

Reject unknown fields and secret/reasoning/raw-payload/traceback material. Do not infer absent values
as zero or default.

### Run Manifest

Before the Agent runs, create a new run directory and isolated workspace. Write a canonical JSON
manifest with the strict profile plus:

- run ID, task ID and repetition;
- evaluator source commit and bounded dirty-state summary;
- dataset, task, protocol and relevant lock/config hashes;
- workspace baseline commit/tree hash;
- expected-change policy hidden from the Agent workspace;
- manifest content hash/integrity envelope.

Comparable baseline creation requires a clean source checkout. Output paths are create-only.

### Finalized result

Finalization runs the external verifier and writes raw output, bounded Git status/name-status/patch
evidence, expected/unexpected path lists, sanitized runtime evidence, primary task class, bounded
classification reason and hashes for all artifacts. PASS is accepted only when the verifier exits
successfully and unexpected changes are empty. Every tool call must be accounted for by a distinct
terminal-state counter.

### Aggregate

Aggregation revalidates all hashes and mechanically derives result-class and tool-state totals. It
rejects duplicate task/repetition keys, mixed protocol/profile snapshots and tampered/missing
artifacts. It reports `INCOMPLETE` until every task has two independent repetitions and all required
metrics exist; only then may it evaluate the frozen gate.

## Implementation steps

1. Add focused failing tests and minimal import support for the script module.
2. Add protocol/profile schemas using only Python 3.12 standard library facilities.
3. Extend task metadata with evaluation-only expected-change policies based on task text and current
   verifier scope, without placing Gold code or paths in the Agent workspace.
4. Add create-only start and manifest-backed rebuild commands; preserve existing CLI commands.
5. Add finalization with verifier/Git/runtime evidence capture and integrity linkage.
6. Add aggregation and gate completeness diagnostics.
7. Remove or explicitly retire the underspecified CSV template; document the exact operator flow.
8. Publish `docs/acceptance/s7p-00-evaluation-protocol.md`, including the legacy non-success erratum
   and a checklist-to-evidence table.
9. Run all declared validation, update execution state and commit coherent verified changes.

## Focused acceptance matrix

- Starting twice with the same task/profile but different repetitions produces fresh workspaces with
  the same baseline tree and configuration hashes.
- Rebuilding from a manifest in a new temp directory produces the same baseline tree and rejects a
  dataset/protocol mismatch.
- Existing output, dirty comparable source, missing profile field and forbidden sensitive field are
  rejected deterministically.
- Verifier success plus an unexpected file cannot be recorded as PASS.
- Raw verifier output, diff/status evidence, stop code, expected/unexpected paths and artifact hashes
  are present and tamper-detected.
- A run with failed=3 and denied=2 aggregates exactly those counts, never failed=5.
- Missing usage remains `unavailable`, never numeric zero.
- Duplicate repetitions, one-run tasks and mixed protocol/profile results remain incomplete.
- Two complete synthetic repetitions for all ten task IDs produce a mechanically derived summary
  and deterministic gate result.
- Existing list/show/prepare/verify/self-check behavior remains compatible.

## Validation

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

## Review and integration gate

Implementation is not complete at the first green commit. The primary session reviews the full
`main...topic` diff for correctness, reproducibility, secret boundaries, false PASS paths,
compatibility and test gaps. The Luna Max implementation session repairs every confirmed finding,
reruns affected and full gates, and makes a final verified commit. Only then may the branch be
fast-forward merged to local `main` and retired.
