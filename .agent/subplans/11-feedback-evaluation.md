# Subplan 11 — Workflow Feedback and Orchestration Evaluation

> Status: pending activation
> Branch: `feat/stage8-feedback-evaluation`
> Activation base: latest verified `main` with Subplan 9 integrated
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
