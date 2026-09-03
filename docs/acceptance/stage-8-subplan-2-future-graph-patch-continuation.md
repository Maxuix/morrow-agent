# Stage 8 Subplan 2 — FutureGraphPatch and Continuation Child Runs

Date: 2026-09-03. Result: implementation commit `fb399df` and review-remediation commit `b8cdb2f`
are fast-forward integrated into local `main`. Remote publication remains separately authorized.

## Delivered

- Exact `FutureGraphPatch` values bind a complete desired source to the parent Run row version and
  immutable base Revision. The pure Compiler remains the graph normalizer; admitted Past nodes,
  Past-targeted edges and provenance cannot change, while Future nodes and required-output targets
  remain editable.
- `PatchApplicationService` is the only patch validation/save/apply owner. Save creates an
  idempotent detached Revision whose identity derives from the patch ID; it never advances the
  WorkflowDefinition head. Detached revisions use a negative per-definition namespace, so any
  number of saved patches cannot consume the next positive published revision. Stale parent facts
  produce an OCC conflict.
- `create_continuation_run` atomically cancels unstarted parent NodeRuns, records
  `superseded(reason=continued_by_patch)`, creates the child, transfers the active root, pre-creates
  the Compiler-closed execution set and records exact inherited Artifact imports. Fault injection
  before commit proves the parent remains the sole active owner and no child survives rollback.
- Initial Runs now persist the complete execution set. Continuation children materialize only
  Future NodeRuns. `EffectiveOutputResolver` supplies inherited output lookup to readiness,
  admission-time input binding, prompt rendering, result calculation and query projection.
- Empty execution sets close in the handoff transaction through the existing finalizer and inherited
  required outputs, including blocking inherited ReviewReports that produce `needs_revision`, a
  FAILED root transition and terminal outcome without indexing a nonexistent child NodeRun.
  Non-empty children inherit the parent budget root and absolute deadline;
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
- Chained continuations derive Past from admitted rows, execution-set complements and imports, so
  inherited ancestors remain immutable and their original Artifact provenance is re-imported rather
  than re-executed. Idle RUNNING pause requests complete directly to PAUSED in one transaction.
- The model-request adapter preserves authoritative lineage budget/deadline reasons. Query projections
  expose every effective output through `EffectiveOutputResolver` with an explicit inherited marker.

## Deterministic evidence

`tests/test_stage8_patch_continuation.py` contains 11 tests covering the original matrix plus review
regressions for:

- continuation over an inherited predecessor through completion, with no missing-NodeRun lookup;
- detached Revision/head separation, exact imports, lineage query counts and shared integrity;
- stale-patch OCC and Past-forgery rejection;
- before-commit handoff rollback with uninterrupted root ownership;
- budget-zero and expired-deadline save-without-start behavior;
- empty execution-set atomic success from inherited required outputs;
- initial full execution-set persistence;
- the initial/continuation/failed-retry/full-rerun inheritance and budget-root matrix.
- multiple detached saves followed by ordinary positive-head publication;
- chained-continuation Past immutability and exact multi-hop Artifact import;
- inherited blocking ReviewReport empty-set closure.

## Validation

| Gate | Result |
|---|---|
| Review-focused matrix | 56 passed |
| Full offline gate: `uv run pytest -m 'not live' -q` | 1566 passed, 2 deselected |
| `uv run ruff format --check .` | 576 files already formatted |
| `uv run ruff check .` | passed |
| `uv run python -m compileall -q src tests` | passed |
| `uv run morrow --help` | passed |
| `git diff --check` | passed |

## Review finding disposition

- Bugs 1–4 were confirmed and repaired with deterministic regressions.
- Suggestions 5–6 were accepted because they close authority/projection drift at low risk.
- Suggestion 7 is comment-style cleanup only; no functional defect was found, so unrelated comment
  churn was deliberately omitted.
- Nit 8 matches the existing state machine. The product choice is now explicit in C2: PAUSED may
  resume or be superseded; cancellation requires an explicit resume followed by the existing owner
  cancellation path. No public lifecycle expansion was made.

No Live Provider/MCP/network test, third-party dependency, public `ApplicationEvent` extension,
GUI work, runtime-policy default change, GraphPlanner or ReplanSignal implementation is included.
