# Subplan 9 — Stage 7 Acceptance and Closeout

> Status: pending
> Branch: `chore/stage7-acceptance`
> Prerequisite: Subplan 8 completed, verified and integrated

## Objective

Prove the static Workflow Runtime as an engineering system, compare it truthfully with Direct using
the smallest useful evidence set, freeze current built-in versions and reconcile all documentation.
Do not create another broad reliability campaign or turn model-quality variance into a safety gate.

## Ownership

- isolated deterministic Stage 7 acceptance fixtures and representative workspaces;
- `docs/acceptance/stage-7-workflow-runtime.md`;
- Stage 7 README/human examples and final built-in definition/version evidence;
- truthful `docs/ARCHITECTURE.md`, `docs/ROADMAP.md`, Stage 7/8 entry-state reconciliation;
- final `.agent/` completion state and validation evidence.

## Tasks

1. Freeze an acceptance matrix covering Direct, serial Explorer-Coder-Reviewer, fixed read-only
   parallel research, cancellation, failure, blocked recovery, revision drift and definition/error
   isolation. Reuse existing Scripted Providers and Stage 1–6 fixtures.
2. Prove every master-plan invariant, including Session-owned log authority, AgentLoop leaf purity,
   immutable revision/run evidence, Artifact-only handoff, permission non-escalation, aggregate
   budget, Writer serialization within each managed WorkflowRun/frontier and completed-node
   no-rerun. Include source/database backup restore, enable/disable admission, absolute deadline
   freeze, required-output-before-terminal ordering, root Workflow-result snapshot plus accepted-
   Outcome goal/result evidence inheritance bound to the exact latest READY transition, rejection of
   stale Workflow evidence after resume/ordinary Direct or a later Workflow, and durable change
   capture/replay. Prove Workflow root Outcome projections accept benign security-named paths/facts
   while actual credential values are redacted/omitted with fixed `completion_basis` fact
   `workflow_evidence_redacted=true` without blocking terminal closure.
3. Run a minimal paired Direct versus Explore-Implement-Verify comparison on representative tasks.
   Record task/verifier outcome, user-visible rework, Reviewer findings, primary agent-generation
   request admissions, compaction count/exclusion, usage/cost availability and elapsed facts without
   inventing missing values.
4. Keep Live/real-Provider evaluation separate. Run it only if the user explicitly authorizes the
   exact campaign and compatible credentials exist. No Live authorization is not an offline
   engineering failure.
5. Apply the promotion rule: a template becomes recommended/default for a task class only with
   clear observed benefit. No benefit leaves Direct as default and the template opt-in/preview; it
   does not invalidate a correct static Runtime.
6. Inspect the complete Stage 7 diff for duplicated owner layers, speculative validators, legal-path
   blocking, raw data leaks, state/recovery gaps and documentation claiming planned behavior as
   implemented. Repair confirmed findings only.
7. Run the final full offline/static/CLI gate, publish exact evidence and update architecture/
   roadmap/Stage 8 entry conditions to implemented truth.
8. Commit and fast-forward integrate verified closeout; verify every Stage 7 topic commit is in
   `main`, retire clean branches/worktrees and record any remote-publication blocker.

## Proportionality decisions

Acceptance proves current supported behavior; it does not demand statistical significance,
coverage percentage, fuzz/load infrastructure or a repeated external campaign. A safety finding
requires a concrete violated invariant and the smallest repair. A model-quality miss changes
promotion/evaluation status, not runtime truth.

Explicitly deferred:

- automatic workflow selection, GraphPlanner, GUI, GraphPatch/Replanner, runtime editing and
  background automation;
- new dashboards, telemetry backend, evaluator framework or provider-specific tuning undertaken
  without evidence from the bounded comparison.

## Validation

```bash
uv run pytest -q tests/test_stage7_acceptance.py
uv run pytest -m 'not live'
uv run ruff format --check .
uv run ruff check .
uv run python -m compileall -q src tests
uv run morrow --help
uv run morrow agent --help
uv run morrow workflow --help
git diff --check
```

Any authorized Live command/result is recorded separately and is not folded into the offline pass
count.

## Exit criteria

- All nine subplans and declared offline engineering gates are completed and integrated.
- Direct remains runnable/default unless evidence explicitly promotes a template for a task class.
- Static serial and bounded read-only-parallel Workflows complete, cancel and recover without
  rerunning completed work or hiding side effects.
- Comparative results are recorded truthfully, including unavailable cost/Live evidence.
- Architecture, roadmap, acceptance, usage and `.agent` state describe only implemented behavior.
- Working tree is clean except for explicitly recorded external publication constraints.
