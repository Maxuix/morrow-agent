# Subplan 2 — FutureGraphPatch and Continuation Child Runs

> Status: pending activation
> Branch: `feat/stage8-patch-continuation`
> Activation base: latest verified `main` with Subplan 1 integrated
> Prerequisite: Subplan 1 (Pause/Drain runtime) verified
> Roadmap authority: stage-8 §6.2–6.5, §8C bullets 4–10, §13 (retry/rerun), §16.2

## Objective

Deliver the runtime's running-edit capability: explicit FutureGraphPatch, the sole deterministic
`PatchApplicationService`, terminal supersession of the old run and atomic child-run handoff, so a
late failure or user edit reuses completed nodes' immutable Artifacts instead of re-running the
whole graph (the Stage 7 six-primary-admissions cost).

## Deliverables

- `FutureGraphPatch` domain type referencing an exact base Revision with an OCC/CAS check; stale
  patches conflict and are never silently replayed.
- `PatchApplicationService` as the single deterministic apply entry for both user exact edits and
  (later) suggested patches, reusing the Stage 7 pure Compiler and the existing Revision
  publication service. No second Revision writer.
- Past/Future enforcement: Past means admitted/started or holding AgentRun/effect/evidence;
  never-admitted queued or cancelled-before-admission rows do not make a semantic node Past. Only
  Future nodes and Future-targeted bindings/edges are editable; Past outputs may be referenced as
  exact immutable `node_id.slot` sources; Future→Past and Past-provenance changes are rejected.
- New terminal `WorkflowRun.status=superseded(reason=continued_by_patch)`, including a migration
  v27 table rebuild of `workflow_runs` adding `superseded` to the status CHECK (terminal, so the
  `workflow_active_root` index and `active_for_root` query are unchanged). Handoff is one
  transaction: CAS on the exact paused parent row version, no Active nodes, no unknown outcomes,
  root still nonterminal → close old run and its unstarted NodeRuns, create the child referencing
  the new Revision with `parent_run_id`, `run_relation=continuation`, inherited Artifact bindings
  and the full Compiler-closed `execution_node_ids` (attempt-1 rows pre-created), transfer the
  root's single nonterminal ownership. A non-empty child starts `running,pause_requested=false`.
- Dedicated journal transaction `create_continuation_run` (and its `rerun` sibling) instead of
  reusing `create_run`: the existing `create_run` rigidly requires QUEUED status, row_version 1,
  `budget_snapshot == revision.budget`, `deadline == started_at + timeout` and a full one-NodeRun-
  per-revision-node pre-creation — all four are violated by a continuation child by design. The new
  method keeps the real invariants (exact immutable Revision, root ownership, pre-created
  execution set, recorded inherited budget/deadline facts) and drops the initial-run-only
  assertions. `create_run` itself is unchanged for initial runs.
- Inherited Past projection: Past nodes are never re-materialized as NodeRuns in the child
  (roadmap §6.3). Instead the handoff transaction records inherited output bindings in the
  child's `workflow_artifact_bindings` (direction=output rows referencing the parent NodeRun and
  the original immutable Artifact IDs — references, never re-published bytes), and the run carries
  an explicit inherited-Past node map. Scheduler readiness (`_require_ready`,
  `_bind_node_inputs`) treats an edge/binding whose producer is inherited Past as satisfied by the
  recorded inherited binding, and the finalizer's `compute_workflow_result` resolves
  required_outputs refs through the same projection — no KeyError/StopIteration on missing child
  NodeRuns, no lineage-wide SQL scans.
- Scheduler drive loop keyed by the child's `execution_node_ids`, not by the full Revision
  topological order: `stable_execution_order` is filtered to the execution set before iteration,
  so `_node_for` is only ever called for nodes with pre-created child rows.
- Empty execution set: accepted only when exact inherited Artifacts satisfy every required contract
  of the new Revision; child is created and terminalized in the same transaction through the
  existing fixed result owner (`succeeded` writes the READY transition + marked snapshot;
  `needs_revision` writes FAILED transition + terminal TaskOutcome referencing required blocking
  reports). No empty `running` child, no separate finalize command.
- Lineage budget: `run_relation: initial | continuation | rerun` and
  `lineage_budget_root_run_id`; continuations inherit the budget root, remaining request cap and
  absolute `admission_deadline_at`; cap/deadline increases must be user-exact edits or
  user-approved proposals applied under OCC with parent facts; non-positive remaining budget or an
  expired deadline still allows saving the patch but does not start a non-empty child. Explicit
  post-terminal `rerun`/new Runs become new budget roots and the CLI says so.
- Result-driving report re-targeting: a patch may point required outputs at a Future replacement
  Reviewer; old blocking reports remain inherited evidence but no longer drive the child; multiple
  result-driving reports keep the Stage 7 any-blocking rule.
- Blocked/outcome-unknown parents: patch may be saved and compiled but handoff/child start is
  refused until Recovery resolves and the root is still nonterminal; resolve-failed/cancelled goes
  through the terminal-parent child/new-Run path; abandon closes the lineage on that root.
- Unified failed-node retry (explicit root resume, then `run_relation=rerun` child carrying the
  full execution set) versus full rerun (new budget root, no inherited node outputs);
  completed-node partial rerun is rejected with an actionable full-rerun message.
- CLI surface for patch save/validate/apply with parent/child lineage and inherited-Artifact
  visibility.

## Key semantics

- Old Runs/Revisions/NodeRuns are never mutated in place; inherited Artifacts are referenced, never
  forged as new producers.
- A drain-time Active failure/cancel keeps the Stage 7 terminal parent; continuing requires the
  explicit legal root transition plus a child/new Run.
- A result-driving blocking ReviewReport with queued declared nodes completes the Reviewer and
  drains to paused rather than terminalizing early; non-result-driving blocks are evidence only.

## Validation

- Deterministic tests for the roadmap §16.2 matrix: handoff transaction fault injection (no
  ownership gap), full execution-set pre-creation, empty-set closure (both succeeded and
  needs_revision, including budget-zero/expired-deadline adjacents), blocked-parent gating and
  resolve paths, lineage-budget accounting across rerun→continuation boundaries, stale-patch OCC
  conflict, Past-forgery rejection, reviewer re-targeting, drain-time failure keeping terminal
  parents, migration from Subplan 1 rows. Plus the review-driven regressions: a continuation
  child whose graph depends on inherited Past nodes runs to completion without
  StopIteration/KeyError; required_outputs satisfied by inherited Artifacts produce a truthful
  `succeeded`/`needs_revision` result instead of a spurious `output_contract_unsatisfied`;
  `create_run` still rejects non-QUEUED initial runs while `create_continuation_run` accepts a
  running child with inherited budget facts.
- Stage 7 + new Stage 8 runtime matrices, full offline gate, Ruff format/check, compileall,
  `git diff --check`.

## Out of scope

Any GUI; ReplanSignal/ReplanCoordinator (Subplan 8); GraphPlanner (Subplan 7); parallelism
(Subplan 11).
