# Stage 7 Evaluation Blocker Remediation — 2026-09-02

## Scope

This repair addresses defects found by a real-user Stage 7 simulation without changing the
Workflow state machine, public event lifecycle, bundled runtime policy, dependency set, or
ConversationLog ownership.

## Repaired blockers

- The installed-ripgrep path now binds the regex argument correctly as
  `--regexp <pattern> -- <path>`. The previous argv made ripgrep treat `--` as the pattern and the
  requested pattern as a path, so default non-literal grep returned `search_failed`.
- `submit_node_result` now exposes a node-specific Provider schema. Exact output slot names,
  required slots, `additionalProperties=false`, and the complete EvidenceBundle, PlanArtifact,
  ReviewReport, or SynthesisReport payload schema are visible before inference. Local Leaf
  validation remains authoritative and now returns bounded value-free field paths on semantic
  validation errors.
- Workflow CLI JSON conversion explicitly supports Enums, dataclasses, Pydantic models, mappings,
  sequences, dates and paths. It no longer recursively inspects arbitrary `__dict__` objects.
- Foreground Workflow Start is durably separated from Scheduler driving. The CLI emits
  `workflow_run_id` before the first model request, exposes `workflow runs` for crash discovery,
  and returns 0 only for completed runs; failed, cancelled and blocked results return 1. Command
  and boundary failures continue to return 2. `completed/needs_revision` remains a successful 0.

## Regression evidence

```text
uv run pytest -q tests/test_stage7_*.py
232 passed

uv run pytest -m 'not live'
1536 passed, 2 skipped, 2 deselected

uv run ruff format --check .
558 files already formatted

uv run ruff check .
All checks passed

uv run python -m compileall -q src tests
passed

git diff --check
passed
```

A local installed-ripgrep smoke searched a temporary workspace through `LocalSearchAdapter` and
returned `engine=rg`, one match, and the expected relative path. The CLI regression matrix also
proves that non-empty compile diagnostics serialize, a warning-bearing publication reports success
after its committed Head update, the durable Run ID precedes Scheduler output, and terminal status
maps to the documented exit code.

## Live Provider status

After explicit authorization, an isolated typed EvidenceBundle smoke ran against the configured
`opencode-go/deepseek-v4-flash` Provider and the third-party `opencode.ai` endpoint:

```text
workflow_run_id: wrun_HEDHY72pOWc6jHxy
node_run_id: nrun_GYFM7pQhoEu5kf85
agent_run_id: arun_uo_kQdMF5ay_NeJX
artifact_id: art_d26bdaf9ea0918426834ad95431195a3
workflow status: completed
workflow result: succeeded
CLI exit code: 0
agent generation requests: 5 / 8
```

The Explorer used only the frozen `ls` and `read` capabilities, identified Morrow and
`src/morrow/`, and produced the required `EvidenceBundle` v1 in the exact `evidence` output slot.
The terminal snapshot referenced the resulting Artifact and reported no changed paths. The Run was
also discoverable through `workflow runs`, proving the repaired durable Run-ID and recovery-query
path against a real Provider.

One first `submit_node_result` attempt returned recoverable `invalid_arguments` after the reads; the
same Agent corrected the submission and the next attempt succeeded. This did not block or misreport
the Run, and it demonstrates that the Provider can consume the node-specific schema and reach the
typed committer. The empty diagnostic detail on that first attempt remains an observability signal
for future refinement, not a Stage 7 evaluation blocker.
