# Stage 8 Adaptive Orchestration and GUI Implementation Plan

> Status: active 2026-09-03
> Active subplan: `2-future-graph-patch-continuation` closeout (review remediation integrated;
> remote publication still requires explicit approval)
> Next subplan: `3-core-api-local-server` (after explicit activation and its authorizations are confirmed)
> Roadmap authority: `docs/roadmap/stage-8-adaptive-orchestration-and-gui.md`
> (revised 2026-09-03: risk-tiered Replan autonomy; runtime-kernel-first ordering)
> Entry evidence: Stage 7 completed and remediated; full offline gate 1538 passed, 2 skipped,
> 2 Live deselected; Ruff format/check, compileall and `git diff --check` green
> Previous master plan: Stage 7, archived under `.agent/archive/subplans/stage7-workflow-runtime/`

## 1. Objective

Turn the Stage 5–7 capabilities into a user-controllable personal Agent workbench:

```text
risk-tiered runtime control (Pause/Drain + future-only Patch + continuation child runs)
→ versioned local Core API (Command/Query/Event/Approval) with a scripted verification client
→ read-only Web GUI observer → Workflow editor and Agent inspector
→ task-specialized constrained GraphPlanner Drafts
→ global future-only Replan with risk-tiered autonomy
→ Context/Learning/Skill management GUI, feedback/evaluation loop
→ (gated) bounded read-only parallelism
```

The GUI is a client of Morrow Core, not a second Agent implementation: it consumes the same
application services as the CLI and never reads or writes SQLite, YAML, CredentialStore or Skill
files directly.

## 2. Authority and execution rules

1. Current user decisions and later explicit scope changes.
2. Current code and validation just run.
3. This plan and the one active child subplan.
4. The Stage 8 roadmap.
5. Acceptance, proposals and reviews as decision history rather than parallel specifications.

Implementation follows these rules:

- at most one child subplan is active and it starts from the latest verified `main`;
- later subplan schemas, APIs and runtime behavior are not implemented early;
- Session-owned `ConversationLog` remains the only chat-history writer; ordinary chat stays on
  `AgentLoop.run_task()`; the Stage 7 Scheduler/leaf composition is reused, not re-implemented;
- no third-party dependency is added without explicit user approval (Subplans 3 and 4 each carry
  a dependency decision that must be approved before their implementation starts);
- no public `ApplicationEvent` lifecycle extension is implemented before the explicit authorization
  recorded as a precondition of Subplan 3; Query/polling delivery must remain a complete path;
- no Live Provider/MCP/network test runs without separate authorization and compatible
  credentials; the local loopback server is exercised by scripted in-process clients only;
- planning alone does not authorize production implementation; each child is activated explicitly.

## 3. Design decisions recorded from the 2026-09 activation discussion

### 3.1 Continuation child runs instead of in-place graph mutation

Running-edit uses supersede-parent + create-child + atomic root-ownership handoff in one
Operational Store transaction, never in-place mutation of a frozen Revision. Rationale: evidence
and audit chains are anchored to frozen Revisions (Doctor, Backup/Verify, Artifact provenance);
crash mid-mutation must have exactly two recoverable outcomes, which only a single-transaction
handoff guarantees; and the Past/Future boundary used by the admission guard is only well-defined
against a frozen base. In-place editing exists before freeze: Draft editing produces no Revisions
until the user freezes/runs. Continuation is not a rerun — the child inherits exact immutable Past
Artifacts and executes only the Compiler-closed future `execution_node_ids`.

### 3.2 Risk-tiered Replan autonomy

Aligned with mainstream harness practice (Codex approval policies, Claude Code permission modes,
OpenCode per-tool allow/ask/deny): autonomy is the default inside the permission envelope, and
user involvement happens at envelope boundaries.

- Leaf-local self-correction within the frozen Node Contract, ToolSet and budget remains free and
  never asks the user; graph-level Replan does not change this.
- A FutureGraphPatch is risk-classified deterministically at proposal time. Low risk means: no new
  or widened permissions, no cap/deadline increase, no added or replaced roles, no switch to an
  unauthorized Provider/Model/Skill, Future-node-only edits. Low-risk patches may be applied
  without interactive approval when `OrchestrationPolicy.auto_replan_mode=allow_low_risk`, and are
  always visible and auditable afterwards. The default is `approval_only`.
- Any privilege- or budget-expanding patch requires explicit user approval regardless of policy
  or evidence; an Agent signal or template can never silently widen the envelope.
- Task-class-level default automation remains a separate promotion gate requiring paired
  Direct/Multi benefit evidence per roadmap §4.6.

The write path is unchanged: user exact edits and Agent proposals converge on the sole
`PatchApplicationService` (pure Compiler + OCC/CAS publication). What changed is only which gate
fires before application.

### 3.3 Runtime kernel before GUI

Per the roadmap entry condition naming child-run continuation the highest-priority runtime
follow-up, Subplans 1–2 deliver the 8C runtime kernel (Pause/Drain, patch application,
continuation handoff, lineage budget) with deterministic offline evidence and no GUI. The versioned
API and GUI slices then consume settled semantics instead of freezing a protocol around unsettled
ones. Roadmap slice numbering is unchanged.

### 3.4 Frozen runtime contracts

Two review rounds against the Stage 7 code were verified and their confirmed findings frozen as
eight runtime contracts in `docs/decisions/stage-8-runtime-contracts.md`: the lineage data model
(execution-set and artifact-imports tables plus a single EffectiveOutputResolver), one atomic
admission transaction, lineage budget enforcement at the existing durable request-admission seam
(no second ledger), the single-writer Core Host model for ASGI hosting, generalized migration
metadata with one merged v26 rebuild, run-local Revisions that never move the Definition head, the
retry/rerun derivation matrix, and the expanded patch risk-classification dimensions. Subplans 1–3
and 8 cite these as contracts authority; deviations require updating the decision document first.

## 4. Subplan sequence

| Order | File | Roadmap slice | Depends on |
|---|---|---|---|
| 1 | `1-pause-drain-runtime.md` | 8C (runtime, part 1) | activation |
| 2 | `2-future-graph-patch-continuation.md` | 8C (runtime, part 2) | 1 |
| 3 | `3-core-api-local-server.md` | 8A (protocol/server) | 2; ApplicationEvent + web-framework authorization |
| 4 | `4-web-gui-observer.md` | 8A (GUI) | 3; frontend toolchain authorization |
| 5 | `5-workflow-editor-agent-inspector.md` | 8B | 4 |
| 6 | `6-run-control-gui.md` | 8C (GUI) | 5 |
| 7 | `7-graph-planner-draft.md` | 8D | 6 |
| 8 | `8-global-replan.md` | 8E | 7 |
| 9 | `9-context-learning-skill-gui.md` | 8F | 4 (no dependency on 5–8; sequenced to keep one active subplan) |
| 10 | `10-feedback-evaluation.md` | 8G | 8 |
| 11 | `11-read-only-parallelism.md` | 8H | 2 plus its own roadmap entry conditions; allowed to slip |

Subplan 9 may be re-sequenced earlier by explicit decision; the one-active-subplan rule still
applies. Subplan 11 starts only when its roadmap entry conditions (stable ToolEffect
classification, provider rate-limit ownership, atomic per-request budget claim, isolation stress
evidence, visibility barrier) are verified.

## 5. Cross-cutting invariants

These restate, without weakening, the roadmap's fixed semantics; subplans add detail, never
exceptions:

1. One `WorkflowRun` references exactly one frozen Revision; Past/Active nodes, old Revisions and
   historical Artifact provenance are never rewritten in place.
2. Pause is the durable orthogonal fact `pause_requested` plus nonterminal `draining`/`paused`
   states; admission rechecks both in the same authoritative store transaction, so Pause and
   queued→running have exactly one winner; pause intent survives blocked/restart and is never
   inherited by a handoff child.
3. Supersession handoff (old Run terminal `superseded`, future NodeRuns cancelled by supersession,
   child created, root ownership transferred) is a single transaction with no ownership gap; a
   non-empty-execution-set child starts `running,pause_requested=false`.
4. `execution_node_ids` is the Compiler-closed set of retained execution-required nodes not mapped
   to immutable inherited Past — never a frontier/descendants subset; attempt-1 rows are
   pre-created for the whole set in the handoff transaction; an empty set closes child and root
   terminally in the same transaction when inherited contracts satisfy required outputs, and the
   Compiler rejects the patch otherwise.
5. Continuation children inherit `lineage_budget_root_run_id` consumption and
   `admission_deadline_at`; only an explicit user-approved patch may raise cap/deadline, under OCC
   with the parent facts; `rerun`/new Runs start a new budget root and the UI/CLI says so.
6. Blocked/outcome-unknown parents may save patches but never start children until Recovery
   resolves and the root remains nonterminal; abandon closes the lineage on that root.
7. GUI and CLI invoke the same application services; the API never exposes credentials, full
   sensitive tool arguments or runtime-internal objects; UI caches are projections.
8. Every durable datum keeps exactly one writer, extending the Stage 7 ownership matrix: the patch
   application path publishes Revisions through the existing compilation/publication service, and
   run-state transitions stay with the Workflow transition owner.
9. Session-owned `ConversationLog` remains the only chat-history authority; `AgentLoop.run_task()`
   stays the ordinary leaf loop with no graph branches.
10. No secret, reasoning, complete tool arguments/results, raw SDK object or traceback enters
    events, API payloads, logs, YAML or terminal diagnostics.
11. New hard gates must meet the proportionality test (protects a live invariant, cheapest
    enforcement point, one deterministic rejection test plus one adjacent legal acceptance test).

## 6. Validation strategy

Every production subplan runs focused deterministic tests plus:

```bash
uv run ruff format --check .
uv run ruff check .
uv run python -m compileall -q src tests
git diff --check
```

Subplans 1, 2, 3 and 11 additionally run the full offline gate (`uv run pytest -m 'not live'`);
every subplan runs the Stage 7/8 matrices it touches. Concurrency and Pause/admission races are
proven with barriers/events, never wall-clock sleeps. GUI slices add deterministic contract-level
tests of snapshot + event-stream + resync behavior; browser-level checks use the scripted local
client or the browser-testing skill on the loopback server. Live Provider evaluation remains
separate and requires explicit authorization.

Final stage gate: full offline gate, Ruff format/check, compileall, CLI help smoke, GUI–CLI
parity evidence, and the roadmap §16 acceptance matrix with each §18 criterion backed by runnable
evidence recorded under `docs/acceptance/`.

## 7. Git, recovery and publication discipline

- Create every production child branch from the latest verified `main` with the branch named in
  its file; commit coherent verified progress; no `wip:` commit remains in merged history.
- Close a child only after its declared validation, execution-state update, fast-forward merge,
  topic ancestry verification and clean branch/worktree removal.
- The retired Stage 7 sequence remains archived under
  `.agent/archive/subplans/stage7-workflow-runtime/` and is never renumbered or reactivated.
- Remote publication requires explicit authorization; the current review-remediation request does
  not authorize a GitHub push.

## 8. Explicitly out of scope

Per roadmap §19: desktop installers/auto-update, background daemons and scheduling (Stage 9),
model-generated arbitrary code nodes or unbounded graphs, leaf Agents modifying the global DAG or
second Replan writers, completed-node partial rerun and transitive Artifact invalidation,
multi-user collaboration, distributed workers, and multiple frontend frameworks or message
channels. A desktop shell is Stage 10 work.
