# Stage 8 Subplan 2 — FutureGraphPatch and Continuation Child Runs

Date: 2026-09-03. Result: local implementation accepted on
`feat/stage8-patch-continuation`; integration/publication is recorded at closeout.

## Delivered

- Exact `FutureGraphPatch` values bind a complete desired source to the parent Run row version and
  immutable base Revision. The pure Compiler remains the graph normalizer; admitted Past nodes,
  Past-targeted edges and provenance cannot change, while Future nodes and required-output targets
  remain editable.
- `PatchApplicationService` is the only patch validation/save/apply owner. Save creates an
  idempotent detached Revision whose identity derives from the patch ID; it never advances the
  WorkflowDefinition head. Stale parent facts produce an OCC conflict.
- `create_continuation_run` atomically cancels unstarted parent NodeRuns, records
  `superseded(reason=continued_by_patch)`, creates the child, transfers the active root, pre-creates
  the Compiler-closed execution set and records exact inherited Artifact imports. Fault injection
  before commit proves the parent remains the sole active owner and no child survives rollback.
- Initial Runs now persist the complete execution set. Continuation children materialize only
  Future NodeRuns. `EffectiveOutputResolver` supplies inherited output lookup to readiness,
  admission-time input binding, prompt rendering, result calculation and query projection.
- Empty execution sets close in the handoff transaction through the existing finalizer and inherited
  required outputs. Non-empty children inherit the parent budget root and absolute deadline;
  exhausted/expired patches may be saved but cannot start.
- Workflow lineage budget is authoritatively enforced inside durable `purpose=agent` request
  admission. Scheduler preflight remains a non-authoritative early refusal that avoids preparing an
  unusable Provider. No second ledger exists.
- Failed-node retry and full rerun use the dedicated `create_rerun` transaction. Both establish a
  new budget root; failed retry inherits completed outputs and executes failed/retained nodes, while
  full rerun inherits nothing and executes the entire graph. Completed-node partial retry is
  rejected with a full-rerun instruction.
- CLI exposes `workflow patch validate|save|apply`, `workflow rerun --full`, lineage/import fields in
  status queries, and an explicit `new_budget_root` marker. Doctor/Backup shared integrity checks and
  Artifact reference enumeration understand execution sets, imports and inherited deadlines.

## Deterministic evidence

`tests/test_stage8_patch_continuation.py` contains 8 tests covering:

- continuation over an inherited predecessor through completion, with no missing-NodeRun lookup;
- detached Revision/head separation, exact imports, lineage query counts and shared integrity;
- stale-patch OCC and Past-forgery rejection;
- before-commit handoff rollback with uninterrupted root ownership;
- budget-zero and expired-deadline save-without-start behavior;
- empty execution-set atomic success from inherited required outputs;
- initial full execution-set persistence;
- the initial/continuation/failed-retry/full-rerun inheritance and budget-root matrix.

## Validation

| Gate | Result |
|---|---|
| Subplan 2 deterministic matrix | 8 passed |
| Workflow store + lineage integrity focused matrix | 19 passed |
| Full offline gate: `uv run pytest -m 'not live' -q` | 1560 passed, 2 deselected |
| `uv run ruff format --check .` | 576 files already formatted |
| `uv run ruff check .` | passed |
| `uv run python -m compileall -q src tests` | passed |
| `uv run morrow --help` and patch/rerun help smoke | passed |
| `git diff --check` | passed |

No Live Provider/MCP/network test, third-party dependency, public `ApplicationEvent` extension,
GUI work, runtime-policy default change, GraphPlanner or ReplanSignal implementation is included.
