# Stage 8 Subplan 11 — Workflow Feedback and Orchestration Evaluation

Date: 2026-09-05. Activation base: verified local `main` at `acf3923`.
Implementation branch: `feat/stage8-feedback-evaluation`; existing checkout, no extra worktree.

## Delivered behavior

- Draft edits and user-requested run patches record bounded WorkflowFeedback in the graph mutation's
  transaction. Semantic evidence includes removing Planner, adding Reviewer and changing an exact
  role/model selection; ordinary graph edits also feed the edit-frequency metric. No-op edits and
  command retries do not add evidence. Continuation runs retain the same root-task sample.
- Settled runs accept structured feedback for complexity, missing exploration, Reviewer usefulness,
  model expense and preferred/avoided templates. One signal never changes routing. Two independent
  Draft/root-task samples can propose a workspace/task-class OrchestrationPolicy candidate.
- Learning exposes candidates, evidence, the complete proposed policy, conflicts and decision history.
  Acceptance uses the existing YAML owner with workspace/global revision checks and a durable decision
  intent. Retrying the original command recovers an interrupted YAML publication without overwriting
  subsequent policy edits. Rejecting a candidate retains its history.
- The evaluation dashboard displays run-anchored TaskOutcome and verification facts, Reviewer findings,
  actual request counts, edit status and unused completed read nodes. Missing usage remains unavailable.
  Direct estimates are explicitly separate from actual paired results.
- Actual pairs require independent initial completed Direct and Multi runs with identical immutable
  TaskContracts. A run can enter only one pair. Request counts come from the existing ledger; quality
  grades are explicit user judgments from 0 to 4.
- Promotion requires at least two independent actual pairs, all beneficial: higher quality, or equal
  quality with fewer requests. An estimate cannot promote, and a no-benefit pair closes eligibility.
  Explicit user policy is still required. GraphPlanner records generation-time auto-run eligibility;
  task-class Replan uses the same evidence and rechecks existing risk/permission constraints at apply.
- CLI and authenticated Core API use the same management commands, receipts and projections. GUI adds
  the Feedback and Evaluation view and Learning review cards, refreshes through the existing bounded
  query fallback, and preserves command identity on uncertain retries.
- Operational Store v29 adds feedback, review and evaluation records. Doctor/Backup verification checks
  identities, workspace ownership, evidence references and paired-run independence. The existing
  only-Draft integrity bug was corrected by checking Agent references in the Revision loop.

## Scope and metric definitions

LearningReview is a deterministic typed projection within the existing Learning management surface;
there is no new Provider, background worker or automatic policy writer. Auto-run is eligibility
bookkeeping; the editor still requires the user to freeze and run. No dependency, bundled runtime
policy default, public event lifecycle or ordinary-chat ownership changed. Subplan 12 is not activated.

Dead nodes are completed read nodes outside the backward dependency closure of required outputs,
including both edges and input bindings. This does not judge write-node side effects. Edit frequency
is edited root tasks divided by all root tasks; edits to a subsequently frozen Draft count for its
runs. Reviewer value uses the latest judgment for each rated root task, excluding unrated tasks.
Absent denominators remain unavailable rather than zero. Evaluation currently compares completed
runs; scripted acceptance demonstrates gate mechanics, not real Provider quality or cost benefit.

## Deterministic acceptance

`tests/test_stage8_feedback_evaluation.py` contains 14 cases covering:

| Case | Evidence |
|---|---|
| Atomic edit capture | A recording failure rolls back the Draft edit; no-op/repeated updates do not duplicate feedback |
| Independent learning | Repeated edits within one Draft do not propose; two independent Planner removals or Reviewer additions do |
| Exact model preference | Two independent role/model swaps preserve the exact ModelRef and only propose a policy |
| Run feedback and review | Post-run evidence, candidate acceptance/replay, rejection and unchanged routing before acceptance |
| Policy publication | Interrupted YAML acceptance recovers with the same command; workspace and global revision drift reject stale candidates |
| Paired metrics and promotion | Estimates, actual counts, duplicate-run refusal, positive promotion, explicit-policy gate and no-benefit demotion |
| Runtime edit ownership | A real user Patch records once against the original root task and marks the dashboard as modified |
| Integrity and migration | v28 data survives v29; unused read-node accounting is exact; broken feedback references fail shared integrity verification |
| CLI/API parity | Actual Typer query agrees with the API, and an actual CLI mutation reuses the API receipt |
| Editable invalid Draft | Missing Agent references remain repairable and queryable without feedback capture crashing |

Initial full regression found ten stale migration-name expectations and two fixture client-message
IDs: **1700 passed, 12 failed, 2 skipped, 2 Live deselected**. These were corrected; the migration-focused
follow-up passed **93 tests**. A later focused run exposed a missing test import (62 passed, 1 failed);
that fixture was corrected and its targeted test passed. Only the final full run counts as the gate.

## GUI and browser acceptance

- `pnpm --dir gui typecheck`: passed.
- `pnpm --dir gui test`: **98 passed**, including five new evaluation/review cases for metric
  denominators, absent evidence, escaping, settled-run feedback, stale review and generation-time state.
- `pnpm --dir gui build`: passed. JS **513.4 KiB / 700 KiB**, CSS **44.6 KiB / 120 KiB**;
  all assets **957.9 KiB**. Vite's generic chunk-size advisory is below the project budget.
- Ruff format/check, Python compileall, CLI/manage help and whitespace checks: passed.

Browser acceptance used `scripts/gui_evaluation_smoke_server.py`, an isolated scripted fixture under
`/tmp/morrow-subplan11-browser-2` on 127.0.0.1:8811. Screenshots and accessible DOM inspection verified:

1. The dashboard shows two beneficial implementation pairs while automation remains closed without
   an authorizing user policy.
2. Accepting a complexity candidate moves it into accepted history and preserves approval-only modes.
3. Recording Reviewer usefulness refreshes the metric to 1/1 (100%).
4. Saving a Direct request estimate labels it as user estimation and leaves actual paired evidence at 2/2.
5. Learning History shows the same accepted candidate, complete policy and supporting feedback.

No real Provider, MCP service, external-network test, user credential or production workspace data
was used. The fixture server and task-created browser tab are closed after acceptance.

## Full regression, package and integration

Final full offline gate: **1723 passed, 2 skipped, 2 Live deselected in 1296.13 seconds**, exit 0.
Both skips are existing host-level Seatbelt cases that cannot run inside the nested Codex sandbox.
All 14 new feedback/evaluation cases passed. Output: `/tmp/morrow-subplan11-final-offline.log`.
Command: `UV_CACHE_DIR=/tmp/morrow-uv-cache UV_OFFLINE=1 uv run pytest -m 'not live' -q --tb=short`.

`uv build --offline` successfully produced the sdist and wheel using the existing system uv cache.
Wheel inspection verified all **18 GUI assets** and **six new feedback/evaluation modules** byte-for-byte
against the final source/build. No dependency or version was added.

Implementation and acceptance were committed as `cf3bd09` and fast-forwarded into local main from
`acf3923`. The topic had zero commits absent from main before deletion, and the original checkout
is the only remaining worktree. Main was 43 ahead / 0 behind locally recorded origin/main before
the documentation closure commit. No fetch or push was attempted.
Remote fetch/push and Live tests remain unauthorized. Subplan 12 remains pending activation.
