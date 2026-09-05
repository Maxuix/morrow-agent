# Subplan 11 — Workflow Feedback and Orchestration Evaluation

> Status: active (2026-09-05 user activation)
> Branch: `feat/stage8-feedback-evaluation`
> Activation base: verified local `main` at `acf3923` (Subplans 1–10 integrated)
> Prerequisite: Subplan 9 verified
> Roadmap authority: stage-8 §5.3, §14, §8G

## Objective

Close the feedback loop: user edits and post-run judgments become WorkflowFeedback, the system
proposes OrchestrationPolicy candidates, and a Direct/Multi-Agent comparison dashboard shows where
multi-agent actually pays off — without one-off edits permanently changing routing.

## Deliverables

- `WorkflowFeedback` capture: user edits during Draft/run (deleted Planner, swapped model, added
  Reviewer), plus post-run structured feedback per §14.3 (too complex, missing exploration,
  Reviewer value, cost, template preference).
- LearningReview integration per §5.3: the system only proposes candidates ("skip Planner by
  default for small implementation tasks in this workspace?"); acceptance writes
  OrchestrationPolicy through the normal path.
- Evaluation dashboard per §8G: TaskOutcome, verification results, Reviewer findings, whether the
  user modified the Workflow, Direct baseline estimates or paired evaluation results; metrics for
  dead nodes, edit frequency and Reviewer value.
- Promotion bookkeeping: which task classes have paired Direct/Multi benefit, driving the §4.6
  promotion gates for auto-run and task-class-level Replan automation.

## Key semantics

- A single user edit never permanently changes routing; candidates accumulate with evidence.
- No benefit evidence keeps automation suggestion-only without blocking engineering acceptance.

## Validation

- Feedback-to-candidate flow tests (repeat edits propose; single edit does not apply), dashboard
  metric correctness over scripted runs, promotion gate state transitions.
- Standard offline/static gates.

## Out of scope

Automatic policy mutation, cross-workspace aggregation, background evaluation workers (Stage 9).


## Implementation decisions (2026-09-05)

- v29 adds bounded feedback, deterministic Workflow policy review results, and explicit user evaluations.
  Draft/user Patch edits commit evidence in the same transaction; repeated operations and lineage children
  never inflate independent samples. Learning uses its existing management Inbox with a separate typed
  orchestration review projection; no additional Provider/worker or leaf Task learning is introduced.
- Two distinct Draft/root-task signals propose a candidate. Acceptance records an intent, checks both
  global/workspace policy revisions, and uses the existing YAML OCC owner; interruption reuses the exact
  command and policy, while later edits conflict. Rejected candidates remain historical.
- Paired evaluation requires independent initial completed single-/multi-node runs of the same immutable
  TaskContract. Actual request counts are read from the ledger; 0–4 quality grades are user judgments.
  Each run can enter only one pair. Estimates remain explicit and never promote.
- Initial promotion rule: at least two independent pairs, all beneficial (higher quality, or equal quality
  with fewer requests). Any no-benefit record closes eligibility. Explicit policy remains necessary and
  Replan still rechecks risk. This subplan records auto-run eligibility; it adds no task-launch worker.
- Metric definitions: output-ancestor-excluded completed read nodes; edited roots / all roots; latest
  per-root Reviewer useful / rated roots. Missing values remain unavailable rather than zero.
- No dependencies, bundled policy defaults, public event lifecycle, ordinary chat path, or parallelism changed.
