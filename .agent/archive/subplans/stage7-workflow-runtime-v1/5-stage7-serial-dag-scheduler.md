# Subplan 5 — Stage 7 Serial DAG Scheduler

> Status: pending
> Branch: `feat/stage7-serial-scheduler`
> Prerequisite: Subplan 4 Direct Workflow parity completed and integrated

## Objective

Extend the proven invoking-session Direct slice into a deterministic foreground serial DAG Scheduler
for every all-isolated graph, including one isolated node, with dependency advancement, aggregate
budget, cancellation and crash recovery. Keep failure semantics
fixed and avoid automatic retry or a general worker/lease framework.

## Ownership

- serial scheduling/application lifecycle services under `src/morrow/application/workflows/`;
- Workflow/Node journal transitions needed by scheduling and recovery;
- Task/Agent recovery composition through existing ports;
- bounded root TaskOutcome evidence projection from exact isolated-leaf links;
- aggregate Workflow budget accounting using existing per-AgentRun observations;
- focused scheduler/recovery tests and execution state.

## Tasks

1. Extend Subplan 4's final Start transaction from its invoking-session Direct Node to any frozen
   all-isolated graph, including one node and multi-node graphs:
   after the same full admission recheck, atomically create the receipt, WorkflowRun/input binding and
   exactly one attempt-1 queued NodeRun for every Revision node in stable order. Command replay reuses
   that complete set; transaction/fault failure creates neither a partial Run nor partial Node set
   (an already published unbound TaskContract may remain). Then derive ready nodes from the frozen
   Revision, terminal NodeRuns and required Artifact bindings. Do not persist a duplicate `ready`
   state. Admit one node at a time in stable revision order.
2. The extended Start transaction has pre-created one attempt-1 queued NodeRun per frozen node.
   Admission transitions that exact row and creates only its leaf AgentRun: `invoking_session` reuses the root pair;
   `isolated` creates one fresh standalone Session plus matching internal `workflow_node` TaskRun and
   binds the references. Duplicate wake/resume cannot create a second NodeRun/attempt.
3. Derive readiness only after every incoming-edge predecessor is completed and every declared input
   Artifact is bound/valid. A control-only edge therefore orders nodes without inventing an Artifact;
   a data binding always has the compiler-proven matching edge.
   Schedule every declared Revision node, including disconnected/unconsumed warning components; all
   are execution-required, so any node failure closes the whole Workflow under the same fixed rule.
   `output.required` controls successful materialization/binding only and never creates optional-node
   continue/skip behavior. Apply the currently reachable rows of the master fixed transition table for success,
   ordinary/preparation/Provider/output failure,
   budget/deadline exhaustion, unknown-side-effect block, cancel and abandon. Ordinary temporary
   unavailability fails the node; only recovery-classified unknown side effects use `blocked`.
   Stage 7 v1 has no configurable failure-policy matrix. The `needs_revision` row is intentionally
   dormant here because ReviewReport does not exist yet; Subplan 6 activates its exact
   required-output interpreter with the real payload/producer and no Scheduler role-name branch.
4. Extend Subplan 4's single-node request-cap/deadline seam into a minimal aggregate ledger for
   primary agent-generation requests (existing durable request rows with `purpose=agent`) shared by
   all nodes and the frozen request/admission deadline; serial concurrency is inherent. Derive Node
   admissions from unique NodeRun rows rather
   than duplicating a counter. At admission freeze and persist on NodeRun
   `effective_node_generation_request_cap = min(declared_node_max_agent_generation_requests,
   workflow_remaining_agent_generation_requests)` and run
   whenever it is positive; enforce that cap at the existing durable request-admission boundary.
   Only zero remaining requests yields `budget_exhausted`. Provider token/cost and tool-call/round
   counts remain truthful observations; unavailable usage is never zero or a guessed charge. A
   passed deadline stops new Node/agent-generation requests, but an active Tool is not forcibly interrupted.
   After it settles, the existing durable request-admission seam rejects the next request and maps
   the active node to `failed(reason=budget_exhausted|deadline_exceeded)`, queued nodes to cancelled,
   and Workflow/root to failed. An outcome-unknown Tool remains blocked instead.
   Automatic compaction summaries/retries are not admitted through the current request seam, so they
   remain under existing AgentRun context/retry bounds and are explicitly excluded/unavailable in
   Workflow aggregate reporting; do not instrument AgentLoop or claim a total Provider-request cap
   in this subplan.
5. Extend Subplan 4's intent-aware foreground cancellation to multiple nodes: stop future admission
   and delegate the active leaf cancellation to existing controls. The owning `workflow run`
   process/caller keeps the live handle; Ctrl-C or a same-process
   application call drives this path. Stage 7 has no cross-process cancel command or durable cancel
   watcher; those arrive with background execution in Stage 9.
   Only a safely settled cancellation marks active/queued nodes, WorkflowRun and root TaskRun
   cancelled. If an in-flight Tool remains outcome-unknown, apply the same blocked mapping as crash:
   persist WorkflowRun `pending_terminal_intent=user_cancel`, keep queued nodes queued and root
   TaskRun open until recovery resolution or abandon. Completed
   Artifacts/side effects remain visible. For all-isolated Scheduler-owned runs, map `completed/succeeded` to root
   `READY_FOR_ACCEPTANCE`, ordinary failure/safe cancel/abandon to the corresponding existing
   TaskService command, and leave a blocked root TaskRun open. All-isolated terminal Node/Workflow,
   root Task transition and the applicable outcome commit in one Operational Store application
   transaction. On success this is a versioned root `TaskOutcome(trigger=SNAPSHOT)` with TaskContract
   goal, required result Artifact refs, the Subplan 4 typed Workflow marker and a
   `TASK_TRANSITION/workflow_ready_transition` ref to the exact root transition committed by this
   transaction; the Subplan 4 acceptance seam then inherits that TaskContract goal for an all-isolated root
   with no Turn only when this ref matches the root's latest transition into READY. On
   failure/cancel/abandon it
   is the existing terminal Outcome with partial evidence. Build those root outcome facts through a
   bounded deterministic Workflow evidence projection in stable Node order from exact NodeRun ->
   leaf TaskRun/AgentRun/ToolExecution links, bound Artifacts and recovery facts, then pass it to the
   existing TaskOutcome owner. This must preserve partial change/test/side-effect/unresolved evidence
   that root-only Task assembly cannot see. Apply the existing Stage 7 value-sensitive Workflow text
   mode to this projection so benign security-named paths/facts pass and actual credential values are
   redacted/omitted with fixed `completion_basis` fact `workflow_evidence_redacted=true` without
   blocking terminal closure. Do not make TaskService scan
   Workflow tables, add an LLM, a second scanner, or duplicate TaskOutcome.
6. On restart, derive the next action from durable state: completed nodes stay completed, queued
   nodes may run, and a previously running AgentRun uses current recovery classification. Unknown
   side effects block only this Workflow. `resume` can continue only after the existing Recovery
   service has reconciled/resolved the Tool outcome; it cannot clear blocked evidence. That resume
   path is legal only when no pending terminal intent exists. If
   `pending_terminal_intent=user_cancel`, resolution never admits queued work: close the active Node
   from its durable terminal fact (otherwise cancelled), cancel queued Nodes and atomically finish
   Workflow/root as user-cancelled. Still-unknown remains blocked. Persist `blocked` only after the
   Morrow-owned handler has returned/released its live handle, or Recovery after restart observes the
   old process handle is gone; a nonterminal durable Tool row remains unknown evidence, not proof of
   a live controllable handler. Recovery-only `abandon` accepts an OCC-current blocked Run without
   requiring that unknown outcome to be reconciled, preserves its NodeRun/Artifacts/evidence and side
   effects, cancels queued nodes, closes the WorkflowRun as `cancelled(reason=abandoned)` and calls the
   existing root TaskRun abandon command. Reject running/queued Runs and any exact live handle still
   owned by this process; do not infer liveness from missing terminal facts or PID absence.
7. Do not add scheduler automatic or node-level retry. Existing model-attempt retry remains in
   AgentLoop. A Stage 7 user rerun starts a new WorkflowRun against an explicitly selected immutable
   Revision; if it reuses a failed root TaskRun, the user first performs the existing explicit
   `FAILED -> OPEN` root Task resume. Workflow `resume` is recovery of the same nonterminal run, not
   rerun. Attempt >1 remains deferred; Stage 8 rerun-from-node uses a child/new WorkflowRun rather
   than appending an attempt to this terminal parent.
8. Add deterministic three-node Scripted Provider tests for order, dependency binding, duplicate
   wake, atomic all-node Start precreation/replay and fault rollback, positive-remainder shrunken
   budget, zero/deadline terminal mapping before admission and
   expiry between a settled Tool and the next agent-generation request, a leaf that performs
   automatic compaction without misreporting it as counted, Provider/preparation/output failure
   propagation, safe same-process cancel versus cancel-unknown mapping, cancellation at each
   boundary, atomic root/Workflow terminal visibility, success-snapshot/accepted-Outcome evidence
   inheritance for one-node and multi-node all-isolated roots with no root Turn, a generic existing write leaf/ToolExecution followed by
   downstream failure whose root projection preserves durable facts,
   safe-cancel after a completed node, cancel-unknown then resolved (no queued-node admission),
   ordinary crash-unknown then resolved/resumed, running/live-handle abandon rejection versus
   blocked/no-local-handle abandon that preserves an unreconciled durable Tool fact, one
   disconnected warning node that is nevertheless executed and can fail the Workflow, generic proof
   that Workflow success finalization waits for every declared node rather than only required-output
   producers, control-only edge ordering, and crash at node/Agent/tool boundaries. ReviewReport-
   specific result semantics wait for their real payload/producer in Subplan 6.

## Proportionality decisions

Serial execution guarantees Writer order only inside this Scheduler-managed WorkflowRun; it does
not claim a workspace-global lease over another process, ordinary Direct Session or separate run.
Existing file revision/conflict checks continue to handle external changes, so no distributed lock
or path-overlap predictor is needed. The only recovery hard block is an unresolved side effect.
Provider unavailability, cost absence and model-quality failure remain scoped runtime outcomes.

Explicitly deferred:

- background worker/daemon, distributed lease, scheduler retry/backoff, condition routes, skip/
  continue policy DSL, pause/drain/Replan and parallel execution;
- transaction compensation, workspace rollback and transparent replay of write nodes.

## Validation

```bash
uv run pytest -q tests/test_stage7_serial_scheduler.py
uv run pytest -q tests/test_stage4_recovery_crash.py tests/test_runtime_control.py
uv run pytest -m 'not live'
uv run ruff format --check .
uv run ruff check .
uv run python -m compileall -q src tests
git diff --check
```

## Exit criteria

- A static multi-node DAG runs in deterministic serial order from frozen revision data only.
- Dependencies and Artifact bindings advance exactly once and terminal states are idempotent.
- Authoritative agent-generation-request/deadline limits cannot be over-admitted; a positive remainder can run
  a node under a recorded shrunken cap, while unavailable Provider token/cost cannot falsely exhaust
  the run.
- Cancel/recovery never reruns completed nodes or hides completed side effects.
- Isolated leaves use matching internal Session/TaskRun pairs that remain outside user acceptance
  and learning; root TaskRun outcome remains authoritative.
- No automatic retry, parallelism, background worker or dynamic graph behavior is present.
