# Subplan 9 — Stage 7 Acceptance and Closeout

> Status: pending
> Branch: `chore/stage7-acceptance`
> Prerequisite: Subplan 8 completed, verified and integrated
> Revised 2026-08-31 per the conditional-GO plan review: acceptance rolls up the four phase gates
> instead of one final big-bang check, and read-only parallelism is verified as a Stage 8 entry
> item, not a Stage 7 deliverable.

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

1. Freeze an acceptance matrix covering ordinary Direct, the one-node isolated slice, serial
   Explorer-Coder-Reviewer, serial fan-in Parallel Research, Planned Refactor, the opt-in Direct
   invoking-session adapter, cancellation, failure, blocked recovery, revocation, revision drift and
   definition/error isolation. Reuse existing Scripted Providers and Stage 1–6 fixtures.
2. Prove every master-plan invariant, including Session-owned log authority, AgentLoop leaf purity,
   immutable revision/run evidence, Artifact-only handoff, permission non-escalation, aggregate
   budget, Writer serialization within each managed WorkflowRun and completed-node no-rerun. Prove
   the review-driven contracts end to end: `validate` performs zero writes and only explicit
   publication moves a head; required/optional/forbidden tool declarations merge under fixed
   precedence; an admitted WorkflowRun is immune to ordinary head disable while emergency revocation
   closes it through the audited `policy_revoked` mapping; structured results exist only as durable
   validated submissions and a missing required submission closes the node as
   `output_contract_unsatisfied`; a blocking Reviewer verdict yields `completed/needs_revision` with
   the root `READY_FOR_ACCEPTANCE` and is never presented as an execution failure; a disconnected
   component is a compile error while a connected unconsumed node still runs.
   Include source/database backup restore, enable/disable admission, absolute deadline
   freeze, required-output-before-terminal ordering, root Workflow-result snapshot plus accepted-
   Outcome goal/result evidence inheritance bound to the exact latest READY transition, rejection of
   stale Workflow evidence after resume/ordinary Direct or a later Workflow, and durable change
   capture/replay. Prove Workflow root Outcome projections accept benign security-named paths/facts
   while actual credential values are redacted/omitted with fixed `completion_basis` fact
   `workflow_evidence_redacted=true` without blocking terminal closure.
3. Run a minimal paired ordinary-Direct versus Explore-Implement-Verify comparison on representative
   tasks. Record task/verifier outcome, user-visible rework, Reviewer findings, primary
   agent-generation request admissions, compaction count/exclusion, usage/cost availability and
   elapsed facts without inventing missing values. Also record the observed cost of the fixed
   whole-graph failure mapping: when a Workflow fails late, a user rerun re-executes every node from
   attempt 1, and that rerun cost is reported alongside the comparison rather than hidden.
4. Keep Live/real-Provider evaluation separate. Run it only if the user explicitly authorizes the
   exact campaign and compatible credentials exist. No Live authorization is not an offline
   engineering failure.
5. Apply the promotion rule: a template becomes recommended/default for a task class only with
   clear observed benefit. No benefit leaves Direct as default and the template opt-in/preview; it
   does not invalidate a correct static Runtime.
6. Inspect the complete Stage 7 diff for duplicated owner layers (the §5 sole-writer matrix is the
   checklist), speculative validators, legal-path blocking, raw data leaks, state/recovery gaps and
   documentation claiming planned behavior as implemented. Repair confirmed findings only.
7. Roll up the four phase gates (7A contracts, 7B serial execution, 7C Multi-Agent semantics, 7D
   productization) with their evidence, run the final full offline/static/CLI gate, publish exact
   evidence and update architecture/roadmap/Stage 8 entry conditions to implemented truth. The
   Stage 8 entry conditions keep child-run continuation (rerun-from-failure without re-executing
   completed work) as the highest-priority orchestration follow-up, backed by the rerun-cost
   evidence from task 3, with bounded read-only parallelism next under its own recorded entry
   conditions (serial DAG crash-tested, ToolEffect classification stable, provider rate-limit
   ownership settled, per-request budget claim implemented, process/cwd/env isolation stress-tested
   and parallel result visibility barriers verified).
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
- any concurrency proof — read-only parallelism belongs to Stage 8 with its own gate;
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

- All nine subplans and declared offline engineering gates are completed and integrated, with the
  7A/7B/7C/7D phase-gate evidence recorded.
- Direct remains runnable/default unless evidence explicitly promotes a template for a task class,
  and the opt-in Direct adapter proves parity.
- Static serial Workflows complete, cancel, revoke and recover without rerunning completed work or
  hiding side effects.
- Comparative results are recorded truthfully, including unavailable cost/Live evidence.
- Architecture, roadmap, acceptance, usage and `.agent` state describe only implemented behavior,
  and the Stage 8 roadmap owns read-only parallelism with its entry conditions.
- Working tree is clean except for explicitly recorded external publication constraints.
