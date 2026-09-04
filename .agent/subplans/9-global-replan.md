# Subplan 9 — Global Future-Only Replan

> Status: active (2026-09-05); base `a799982`, current worktree
> Branch: `feat/stage8-global-replan`
> Activation base: latest verified `main` with Subplan 8 integrated
> Prerequisite: Subplan 8 verified
> Roadmap authority: stage-8 §6.5 (as revised 2026-09-03), §4.6, §8E, §16.4

## Objective

Let Node Agents and the Orchestrator request graph-level correction through
ReplanSignal/ReplanProposal, with ReplanCoordinator as the sole automatic Patch proposer and the
Subplan 2 `PatchApplicationService` as the only write path — gated by risk-tiered autonomy rather
than blanket approval.

## Deliverables

- `ReplanSignal` (Node Agent → Orchestrator) and `ReplanProposal` types; Node Agents can never
  modify a Revision directly. Signal lifecycle is fixed: a running leaf never suspends itself
  waiting for a global Replan and never converts itself back to editable Pending (roadmap §6.3).
  A signal is durable evidence attached to the node's own closure — carried on the typed
  `submit_node_result` submission (e.g. ReviewReport findings) or a bounded signal record written
  at the node's terminal/error boundary — and is consumed only after that node settles. The
  Scheduler/Orchestrator then requests Pause/Drain, and only a fully `paused` run with no Active
  nodes may enter the handoff transaction; auto-applied low-risk patches obey the same window, so
  no patch ever lands on a mid-flight graph.
- `ReplanCoordinator`: the sole automatic Patch proposer (not required to be an LLM Agent); turns
  signals into concrete `FutureGraphPatch` proposals against the exact current base Revision.
- Deterministic patch risk classification per master-plan §3.2 and contracts doc C8: low risk =
  Future-node-only edits with no permission/explicit-guardrail/role expansion and no unauthorized
  Provider/Model/Skill change. Additionally, these are never low risk: removing a
  Reviewer/approval gate, loosening an output contract, dropping a test/report dependency,
  deleting a control edge, reordering Writer nodes, re-pointing required outputs, changing
  `conversation_scope`, changing the Provider/Model data boundary, or removing a user-explicit
  node; unclassifiable diffs default to approval-required. The classifier is data-derived from the
  patch diff, not model-declared.
- Risk-tiered application: `auto_replan_mode=approval_only` (default) queues every proposed patch
  for user approval; `allow_low_risk` auto-applies low-risk patches through the same
  PatchApplicationService (Compiler + OCC/CAS), with every auto-application recorded and
  afterwards visible/auditable in UI and CLI. Privilege- or explicit-guardrail-expanding patches are never
  auto-applied under any policy; they surface as pending proposals with the escalation reason.
- Replan UI/CLI: proposal diff, risk class, escalation rationale, approve/reject; auto-applied
  history with the applied patch and its classification.
- Stale-base handling: a concurrent user edit or another proposal invalidates the base; conflicts
  regenerate or surface for user resolution, never silently replay.
- Blocked/outcome-unknown parents produce proposals only, per Subplan 2 semantics.

## Key semantics

- User exact edits and Agent proposals converge on the single patch path; the autonomy tier decides
  only which gate fires before application.
- The signal/admission race is closed at the Subplan 1 admission transaction: the node terminal
  commit records the signal durably, and every subsequent admission re-checks for unconsumed
  signals in the same transaction (contracts C2), so no node can be admitted between signal
  persistence and the drain decision.
- Auto-accept never bypasses Compiler/OCC; compile failure falls back per §4.5.
- Task-class-level default automation stays closed until §4.6 paired evidence exists.

## Validation

- Deterministic tests per roadmap §16.4: low-risk auto-apply under `allow_low_risk` with audit
  trail; same patch held under `approval_only`; escalation patches never auto-applied under any
  policy; stale-base conflict; Node-Agent signal cannot touch the Revision; auto path never
  bypasses Compiler/OCC.
- Standard offline/static gates.

## Out of scope

Feedback-driven OrchestrationPolicy candidates and evaluation dashboards (Subplan 11); nested
dynamic subgraphs or leaf-created DAGs (never).
