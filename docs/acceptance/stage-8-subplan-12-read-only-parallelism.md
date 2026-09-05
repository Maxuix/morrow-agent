# Stage 8 Subplan 12 — Bounded Read-Only Parallelism

Date: 2026-09-05. Base: verified local `main` at `9ada9b0`.
Branch: `feat/stage8-readonly-parallel`. Status: final offline regression in progress.

## Delivered behavior

An explicit Workflow `default_budget.max_concurrency > 1` permits independent, isolated read
nodes to overlap. The default remains 1. The existing Draft editor/CLI can set that field; the
GraphPlanner continues to generate a serial default that users may edit before freezing.

The Scheduler uses a fixed ready frontier in stable Revision order. It does not cross a Writer
or an unfinished dependency to manufacture extra concurrency. All candidate preparation finishes
before admission, and all node admissions reach a barrier before Provider work proceeds.
The Compiler-frozen effective tool requirements, actual static contracts/ToolEffect, workspace
capability and frozen PermissionSnapshot establish the proof. Unknown or opaque contracts,
missing capability evidence and capacity 1 use the existing stable serial path. A known read
contract drift fails the target node, including a changed optional tool hidden by current catalog
filtering. Per-call guards reject changed intents before a handler runs or a model retries.

NodeRun keeps an optional immutable `parallel_read_digest` in its existing JSON record.
No migration, dependency, bundled policy default or public event type is added. The journal
atomically enforces Active slot capacity and prevents unproven nodes/Writers from overlapping.
Permission freezing joins the same Turn/NodeRun admission transaction.

Provider requests retain the existing idempotent AgentRun/attempt-ordinal ledger, which is already
uniquely attributed to a WorkflowRun/NodeRun. Every request, including tool follow-ups and retries,
claims only that request against the optional node and lineage caps. Usage/actuals settle through
the same ledger. No whole-node reservation or second counter exists. Adapters normalize Provider
failures and Retry-After; AgentLoop remains the sole retry owner. The Scheduler adds no retries.

Leaf journals, request observations and candidate Artifacts are durable as they arrive.
**The ordered visibility boundary is Workflow output bindings and NodeRun completion**, not the
chronology of isolated leaf evidence. After all active leaves stop, the Scheduler publishes
available successful outputs in stable node order, then settles failure, cancellation, Replan,
Pause or downstream admission. A committed successful leaf survives sibling failure/cancellation.
Recovery checks the entire active cohort and drains it before admitting queued nodes, including
crashes halfway through the admission barrier. Completed leaf results are never requested again.
Doctor/Backup Workflow integrity checks include the new proof references and slot invariants.

## Deterministic acceptance evidence

`tests/test_stage8_readonly_parallel.py` has 25 cases, using Events/Barriers and scripted Providers.
No test asserts elapsed timing or uses a wall-clock sleep to coordinate concurrency.

| Contract | Evidence |
|---|---|
| Real production Core Host and API events show three simultaneous read nodes | `test_production_core_api_composition_enables_proven_read_parallelism` |
| Simultaneous read admission; isolated sessions and frozen read permissions | `test_frontier_admits_together_and_publishes_in_stable_order` |
| Reverse completion stays invisible to downstream until stable publication | Same test releases reader 2 and reader 1 before reader 0 |
| User cancellation joins every active leaf and preserves a completed sibling | `test_cancel_preserves_completed_leaf_and_joins_every_active_node` |
| Optional Workflow cap rejects excess claims | `test_atomic_request_cap_rejects_overclaim` |
| Real tool follow-ups use per-request caps, without whole-node reservation | `test_parallel_tools_claim_each_followup_request_without_node_reservations` (2 cases) |
| Missing proof/capacity and opaque effects use serial fallback | `test_bounded_capacity_and_serial_fallback` (3 cases), `test_unknown_tool_contract_uses_serial_fallback` |
| Drift fails before requests/handlers; no fallback or model retry | Static, dynamic and catalog-filter drift tests |
| Active capacity and exclusive Writer admission | `test_actual_capacity_bound_and_writer_exclusivity` (2 cases), `test_store_rejects_excess_slots_even_if_scheduler_frontier_is_too_large` |
| Permission evidence is enforced at the transactional gate | `test_frozen_permission_is_required_by_atomic_parallel_admission` |
| Pause drains all active nodes and resumes only queued work | `test_pause_drains_whole_frontier_before_resuming_summary` |
| Driver loss, terminal-commit crash and partial admission preserve completed results | Driver-loss, leaf-terminal crash and partial-admission recovery tests |
| Cancellation at the admission barrier retains user/crash distinction | `test_cancel_at_admission_barrier_preserves_driver_loss_semantics` (2 cases) |
| Provider rate limits have one retry owner and one durable ledger | `test_provider_rate_limit_retries_have_one_leaf_owner_and_one_durable_ledger` |
| Process/cwd/env isolation under concurrent load | `test_process_cwd_environment_isolation_stress`: 16 event-synchronized local child processes, distinct cwd/env, unchanged parent state |

The process stress case validates the existing adapter isolation prerequisite. It does not grant
process tools parallel admission; opaque process effects and Writers remain serialized.

## Validation

Commands use `UV_CACHE_DIR=/tmp/morrow-uv-cache uv run --offline` because the ordinary uv cache is
outside the writable sandbox. The installed dependency set is unchanged.

- Related serial/Pause/continuation matrix plus the first 23 parallel cases: **72 passed**, 51.14s.
- Post-fix parallel + Stage 7 multi-agent pipeline: **55 passed**, 14.55s.
- `uv run ruff format --check .`: 633 files formatted; `uv run ruff check .`: passed.
- `uv run python -m compileall -q src tests`, `uv run morrow --help`, `git diff --check`: passed.
- First full offline run: **1738 passed, 5 failed, 2 skipped, 2 Live deselected**, 484.18s.
  All five failures came from an unnecessary constructor-time transition read in leaf hooks.
  The Scheduler now supplies the frozen proof explicitly, preserving independent hook composition;
  the affected pipeline suite passed after correction.
- Second full offline run: **1747 passed, 2 skipped, 2 Live deselected**, 331.01s.
- Production Core Host/API acceptance additionally found that leaf initialization lacked the
  host workspace/permission profile already used by its ToolExecutor. Copying that immutable
  capability evidence enables production parallel proof. The new API case passed and reconstructs
  a peak of three active nodes from the real durable event stream.
- Final full offline run after production composition correction: pending
  (`/tmp/morrow-subplan12-production-final-offline.log`).

No Live Provider/MCP/network tests or remote Git publication were authorized or performed.
No GUI source changed; this runtime slice uses the existing concurrency input and status/events.

## Integration

Pending successful final offline regression, verified commit, local fast-forward integration,
topic ancestry check and branch retirement. Local `main` already contained 44 unpublished commits
at activation; remote push remains explicitly unauthorized.
