# Subplan 7 — Stage 7 Direct Invoking-Session Adapter

> Status: completed and integrated
> Branch: `feat/stage7-direct-adapter`
> Prerequisite: Subplan 6 completed, verified and integrated; the serial Scheduler path is stable
> Revised 2026-08-31 per the conditional-GO plan review: the Direct shape is an adapter on the
> proven Scheduler, not a separate runner, and it arrives only after serial stability is proven.

## Objective

Add the opt-in one-node `invoking_session` conversation scope so a single-node Workflow can run in
the invoking root Session with ordinary Direct chat parity. This is a Session-binding strategy on
the existing WorkflowScheduler/WorkflowTransitionService/NodeResultCommitter/finalizer — not a
second runner, state machine or terminal path.

## Ownership

- the `invoking_session` conversation-scope value and its compiler rule;
- the Direct Turn admission binding inside existing TurnLifecycle;
- the scope-specific root-terminal delegation (TurnLifecycle owns the root transition; the Workflow
  finalizer closes the Run);
- the Direct built-in WorkflowDefinition fixture used by Subplan 8's template;
- parity, cancellation and recovery evidence against ordinary Direct;
- focused adapter tests and execution state.

## Tasks

1. Add the `invoking_session` scope value to the Subplan 2 domain and the Subplan 3 compiler rule:
   legal only when the entire graph has exactly one node and no edges; reject every multi-node graph
   containing it, because its first STOP would transition the root before downstream nodes can run.
   All other graphs remain `isolated`. No result-driving ReviewReport restriction exists:
   `needs_revision` closes the root as `READY_FOR_ACCEPTANCE` regardless of scope, so prove an
   invoking-session graph may list a result-driving ReviewReport slot beside the TextResult
   positive and the multi-node rejection.
2. Extend StartWorkflowCommand with the Direct-only distinct client-message ID. For an
   invoking-session graph the leaf Session/TaskRun refs equal the invoking root pair, and the bound
   TaskContract text passes exactly once to existing TurnLifecycle under that client-message ID;
   Workflow command replay and Turn replay use different receipts and are tested independently. Turn
   admission rechecks the exact WorkflowRun/root ID, expected root version and client-message
   association in its own transaction; it never falls back to whatever Task became current after
   Start. The root active-Workflow guard from Subplan 2 keeps ordinary Turn admission and this exact
   bound Turn mutually exclusive.
3. Compose the same AgentLoop leaf without the ordinary steering/follow-up queue so one NodeRun
   remains one AgentRun/Turn; cancel remains available and ordinary Direct steering is unchanged.
   Leaf composition supplies the Revision node's frozen `resolved_model_ref`, never the then-current
   active model.
4. Keep root-terminal ownership with the existing TurnLifecycle for this scope: once the Direct Turn
   is admitted, TurnLifecycle commits the output binding/root transition while the WorkflowRun stays
   nonterminal, so the active-Workflow guard continues to own the root. For STOP/success (including
   a result-driving blocking ReviewReport, which only changes Workflow `result_status` and snapshot
   facts) the idempotent Workflow finalizer then writes the marked result snapshot and closes the
   WorkflowRun in one transaction. ERROR/CANCEL/committer failure closes Node/Workflow from the
   durable Turn/Agent terminal and preserves the existing terminal TaskOutcome without inventing a
   result snapshot whose exported outputs do not exist. Recovery may finish the finalizer from the
   durable root/Agent terminal without rerunning the node. Every TaskOutcome produced by this exact
   Workflow-bound Direct lifecycle (STOP, ERROR, CANCEL or committer failure) uses the internal
   Workflow text-safety profile; ordinary Direct remains legacy-strict.
5. The NodeResultCommitter composes at the same TurnLifecycle terminal path as in the isolated
   slice; TextResult slots wrap the committed final Assistant message and structured slots require
   the same durable `submit_node_result` submission facts from Subplan 6. No Direct-specific output
   path is created.
6. Prove parity: for identical inputs and evidence, the adapter produces the same AgentLoop,
   ToolExecutor, permission and completion behavior as ordinary Direct, with no additional model
   request. One deliberate, documented exception stands: a task whose text legitimately contains
   secret-shaped material is rejected at Start because the TaskContract Artifact is durable; the
   actionable error directs the user to ordinary Direct. If any other legal ordinary-Direct task
   cannot complete through the adapter, the adapter is repaired — never defended as safer.
7. Add Scripted Provider tests for the client-message binding, Turn admission recheck (including
   root replacement between Start and admission), command replay versus Turn replay, parity against
   ordinary Direct, cancellation, crash/recovery at the committer/finalizer boundaries,
   pre-Turn preparation failure leaving the root OPEN via the Workflow lifecycle transaction,
   disabled/revoked rejection at Start, admitted-Run immunity to ordinary disable, result-driving
   ReviewReport on the invoking-session shape producing `completed/needs_revision` with the root
   READY, accepted-Outcome marker inheritance with an existing Turn goal preserved, and proof that
   the adapter Turn neither consumes nor creates an ordinary steering/follow-up turn.

## Proportionality decisions

Required now is only the scope-specific Session binding and its parity evidence. This subplan must
not add:

- a separate runner, scheduler, transition service, budget pipeline or finalizer;
- multi-node invoking-session semantics, steering inside a Workflow node or a compatibility facade;
- any change to ordinary Direct defaults; the adapter stays opt-in;
- new output contracts, events or CLI surface (Subplan 8 wires the template and commands).

## Validation

```bash
uv run pytest -q tests/test_stage7_direct_adapter.py
uv run pytest -q tests/test_stage7_isolated_workflow_slice.py tests/test_stage7_serial_scheduler.py
uv run pytest -q tests/test_agent_run_preparation.py tests/test_stage4_recovery_crash.py
uv run pytest -m 'not live'
uv run ruff format --check .
uv run ruff check .
uv run python -m compileall -q src tests
uv run morrow --help
git diff --check
```

## Exit criteria

- The invoking-session shape runs through the same Scheduler/committer/finalizer as isolated
  graphs; the only scope-specific code is Session binding, Turn admission and root-transition
  delegation.
- Multi-node graphs containing `invoking_session` are compile errors; the one-node shape proves
  ordinary-Direct parity with no additional model request and identical completion truth.
- TurnLifecycle never double-writes a root transition with the Scheduler, and the Workflow finalizer
  closes the Run exactly once.
- A result-driving ReviewReport on the invoking-session shape yields `completed/needs_revision`
  with the root `READY_FOR_ACCEPTANCE` — no FAILED reinterpretation anywhere.
- Ordinary Direct remains the default path and its regressions pass.
