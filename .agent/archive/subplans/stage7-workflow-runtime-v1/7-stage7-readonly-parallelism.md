# Subplan 7 — Stage 7 Bounded Read-only Parallelism

> Status: pending
> Branch: `feat/stage7-readonly-parallelism`
> Prerequisite: Subplan 6 serial multi-Agent pipeline completed and integrated

## Objective

Add bounded concurrency only for fixed ready nodes whose compiled effective capability is read-only,
then join their typed outputs through a Synthesizer Agent. Preserve one serialized Writer and the
same durable NodeRun/Artifact model.

## Ownership

- a narrow bounded-concurrency extension to the Stage 7 Scheduler;
- aggregate admission/reservation/settlement for concurrent read-only NodeRuns;
- cancellation/failure/recovery aggregation for one fixed fan-out frontier;
- built-in Synthesizer definition, SynthesisReport contract and Parallel Research template;
- focused deterministic parallelism tests and execution state.

## Tasks

1. Compute a fixed ready frontier from the frozen DAG. At runtime, prove each `access_mode=read`
   candidate from its frozen effective ToolSet, ToolEffect and PermissionSnapshot; role name alone
   is never proof, while `access_mode` remains an enforceable ceiling.
2. A read node cannot retain Host `bash`, write/edit/config/promotion capability, unknown/opaque
   effect or an MCP/Skill tool without authoritative read-only effect. It may retain existing
   native-sandbox bash only when promotion is absent, external effects are denied by existing policy
   and snapshot mutations are discarded. Remove optional wider capabilities; if native isolation is
   unavailable and bash is required, or runtime facts drift from compile evidence, fail preparation
   for that target node. Do not conceal a read-contract violation with serial fallback. `access_mode=write` nodes
   remain legal through stable serial execution; fallback is only for unavailable concurrency
   proof/capacity that does not widen the frozen contract.
3. Keep Writer admission serialized within this WorkflowRun/frontier. Stage 7 does not claim a
   workspace-global lease and does not create worktrees, path predictions or distributed locks.
   MCP/integration friction stays actionable: when a tool is removed from a read contract (or fails
   node preparation) for lacking an authoritative read-only effect declaration, the diagnostic names
   the missing declaration and how the tool/definition can provide it, so a legitimate read-only MCP
   tool is a metadata fix away rather than an opaque rejection.
4. Reserve concurrency slots and each concurrently admitted leaf's full finite agent-generation-
   request maximum, persisting that value as its NodeRun
   `effective_node_generation_request_cap`.
   For recovery, derive each running leaf's outstanding reservation as its frozen cap minus its
   durable admitted `purpose=agent` request rows; do not add an in-memory-only ledger or a second
   request counter. After settlement release only the unused reservation; never decrement, rewrite or
   "release" an admitted durable request row. A leaf whose full
   cap does not fit stays queued; if no parallel reservation fits but remaining capacity is positive,
   use stable serial admission with
   `min(declared_node_max_agent_generation_requests, workflow_remaining_agent_generation_requests)`.
   Provider token/cost and tool-call/
   round counts remain observations; unavailable usage is never replaced by a guessed charge.
5. Give every leaf its own fresh empty Session plus matching internal `workflow_node` TaskRun,
   prepared runtime, cancellation token, NodeRun attempt and Artifact outputs. No mutable
   Session/AgentLoop state is shared.
6. Gather terminal results in stable node order independent of completion order. Required fan-in
   waits for all declared inputs; failure follows the existing fixed Workflow failure semantics.
7. Add a read-only Synthesizer Agent and SynthesisReport used by the Parallel Research template.
   Compile the real fixed fan-out/fan-in definition now that these refs/contracts exist, then run it;
   do not invent a generic deterministic Merge node/runtime or rely on a Subplan 3 placeholder.
   Keep the template within the existing TaskOutcome `artifact_refs` capacity by construction:
   fan-out leaves publish their facts as `required=false` observation outputs (or as inputs consumed
   by the Synthesizer), and only the single aggregate SynthesisReport is a Workflow required output —
   a large fan-out must not approach the 64-ref compile bound.
8. Cancel the frontier deterministically, settle every admitted NodeRun once and recover from
   partial completion without rerunning completed nodes.
9. Prove concurrency with injected barriers/events and Scripted Providers rather than wall-clock
   timing. Test budget reservation, stable gather, failure, cancellation, restart and Writer
   exclusion.

## Proportionality decisions

Parallelism is permitted only after the serial pipeline is accepted and only for a real read-only
research use case. The minimum mechanism is an in-process bounded frontier, not a general executor
platform.

Explicitly deferred:

- dynamic fan-out/`Send`, concurrency-group DSL, priority scheduling, background/remote workers,
  distributed lease, load balancing and autoscaling;
- parallel Writers, Git worktrees, conflict merging and compensation;
- early-completion/`any` joins and arbitrary Merge functions.

If the frozen read contract remains fully satisfied but concurrency slots or harmless parallelism
proof/capacity is unavailable, deterministic serial fallback is valid and must not make the
Workflow unusable. Capability/effect/isolation drift is a target-node failure, never a fallback.

## Validation

```bash
uv run pytest -q tests/test_stage7_readonly_parallelism.py
uv run pytest -q tests/test_stage7_serial_scheduler.py tests/test_stage7_multi_agent_pipeline.py
uv run pytest -m 'not live'
uv run ruff format --check .
uv run ruff check .
uv run python -m compileall -q src tests
git diff --check
```

## Exit criteria

- Fixed read-only siblings demonstrably overlap under deterministic synchronization and gather in
  stable order.
- Concurrent leaves share no ConversationLog/runtime state and cannot exceed authoritative
  agent-generation-request, request/admission-deadline or concurrency limits.
- Cancellation/recovery settles each attempt once and never reruns completed siblings.
- A Writer node cannot enter a concurrent frontier and remains runnable serially; an opaque/unknown
  effect cannot survive a read contract. Runtime drift fails only target preparation and is never
  silently widened.
- No multi-Writer, distributed/background or dynamic-fan-out machinery exists.
