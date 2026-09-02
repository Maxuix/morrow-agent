# Subplan 1 — Pause/Drain Runtime

> Status: pending activation
> Branch: `feat/stage8-pause-drain`
> Activation base: latest verified `main` at activation
> Prerequisite: Stage 8 master plan approved
> Roadmap authority: stage-8 §三 (entry conditions), §6.3 (Pause semantics), §8C bullets 1–3,
> §16.2 (pause/blocked test cases)

## Objective

Add durable Pause/Drain/Resume to the Workflow runtime so that running-edit (Subplan 2) has a safe
control point: users can stop new node admission without losing in-flight work, and pause intent
survives crashes, blocked states and recovery.

## Deliverables

- Core model updates in `core/workflows/runs.py`: add `DRAINING`, `PAUSED` to `WorkflowStatus`
  (both nonterminal), extend `validate_run_transition` with the pause/drain/resume transitions,
  and update the run validators for the new states.
- Schema migration v26 (table rebuild, since SQLite cannot drop CHECK constraints): rebuild
  `workflow_runs` with `status CHECK` extended by `draining`/`paused`, and rebuild the
  `workflow_active_root` partial unique index to cover
  `('queued','running','blocked','draining','paused')` — without this, a draining/paused run
  escapes root-task exclusivity and a second WorkflowRun could attach to the same root. The
  application-level `active_for_root` query in `workflow_journal.py` must be extended with the
  same status set; index and query stay in lockstep. (`workflow_node_runs` keeps its existing
  CHECK — Stage 8 adds no new node statuses; supersession closes future NodeRuns as `cancelled`
  with an explicit reason.)
- The single orthogonal persisted boolean `pause_requested` (no generic command queue), carried
  on `WorkflowRun` with OCC row-version semantics.
- Pause command: OCC transition `running,pause_requested=false -> draining,true`; also legal on a
  `pending_terminal_intent=null` blocked run (sets only `pause_requested`, leaving status and
  unknown evidence untouched); rejected on a blocked run carrying
  `pending_terminal_intent=user_cancel`.
- Admission guard: every Node admission rechecks parent `status=running` and
  `pause_requested=false` in the same authoritative store transaction, so Pause and
  queued→running have exactly one winner.
- Drain completion: `draining` admits no new nodes; when Active nodes settle safely and no terminal
  mapping applies, the run becomes `paused`. A node waiting on an unconsumed Approval stays
  `running` with an approval-pending projection while the Workflow stays `draining` — it is not
  `blocked`; if that pending Approval is later denied or its tool fails/cancels, the existing
  fixed failure/cancel terminal mapping applies to the node and the Workflow (denial during drain
  resolves through the same mapping, never a deadlock); only an unknown Tool outcome uses
  recovery-only `blocked`, preserving `pause_requested=true`.
- Recovery interaction: resolve-success with the root still nonterminal returns to `draining` or
  `paused` by remaining Active nodes and never admits queued nodes; resolve-failed/cancelled follows
  the existing terminal mapping. Resume atomically clears `pause_requested` and returns a paused
  run with no child/patch handoff to `running`. Core restart preserves the pause fact, and root
  ownership is not released while paused/draining/blocked-with-pause.
- Migration backfill in the same v26 step: existing runs get `pause_requested=false`,
  `run_relation=initial`, `lineage_budget_root_run_id=self`; upgraded running/blocked runs continue
  on the original recovery path without NULL-induced admission stalls or budget resets.
- CLI surface: `workflow pause|resume` commands with truthful output, alongside existing cancel.

## Key semantics

- Pause is a fact, not a process-local flag; a pause request racing admission has one winner.
- Drain never converts an Active node into editable Pending by cancelling it; completed/failed/
  cancelled-after-admission nodes are immutable history.
- The migration is additive and nullable-free at read time; no existing Stage 7 transition changes
  meaning.

## Validation

- Deterministic tests: pause during queued admission (barrier-controlled race), pause with
  in-flight Tool (unknown outcome keeps `pause_requested`, resolve-success returns to
  draining/paused without admitting queued), approval-pending drain projection plus denial during
  drain following the fixed terminal mapping, user-cancel-intent blocked run rejects Pause and
  still terminalizes, restart preserves pause, migration v26 backfill of scripted legacy rows and
  root-exclusivity proof that a draining/paused run still blocks a second WorkflowRun on the same
  root (at both the index and `active_for_root` layers).
- Stage 7 workflow matrix, full offline gate, Ruff format/check, compileall, `git diff --check`.

## Out of scope

FutureGraphPatch, supersession handoff, child runs (Subplan 2); any GUI; durable remote
cancellation (Stage 9).
