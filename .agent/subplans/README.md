# Subplans

Stage 4 and Stage 5 are complete through Subplan 62 and remain recoverable in Git history. On
2026-08-24 the user explicitly requested a fact-checked final Stage 6方案 and a complete executable
implementation plan. Stage 6 is complete locally through Subplan 77, including the user-requested
runtime-policy and remaining Skill Script diagnostics/context repairs.

On 2026-08-25 the user opened Stage 7 pre-baseline item 2: test the current Direct Agent with the
completed Code Agent Mini Eval, then repair only evidence-backed tool gaps that block complex Stage
7 tasks. Subplan 78 is complete, integrated into local `main`, and retired.

On 2026-08-26 the user approved the ordered Direct Agent reliability checklist and opened S7P-00.
Subplan 79 completed the evaluation protocol and failure-taxonomy freeze, passed internal review,
and was fast-forward integrated into local `main` before any measured Direct Agent repair begins.
The user then requested the remaining items in strict order. Subplan 80 completed S7P-01 safe
AgentRun observability and a one-shot headless execution entrypoint. Subplan 81 completed S7P-02
Provider-visible tool contracts and recoverable argument validation and was integrated locally.
Subplan 82 completed S7P-03 Direct Coding prompt and scoped project-instruction assembly and was
integrated locally. Subplan 83 completed S7P-04 workspace change lifecycle repair and was integrated
locally. Subplan 84 completed S7P-05 validation and completion truth and was integrated locally.
The subsequent post-fix audit found cross-cutting reliability defects, and the user explicitly
opened Subplan 86 to repair them before S7P-06. Subplan 85 was integrated into local `main`. On
2026-08-27 the user explicitly opened Subplan 88. Its implementation and remediation are complete,
formally approved and fast-forward integrated into local `main`; the sequence is paused before
S7P-08. The user then explicitly opened S7P-08. Subplan 89 completed the 18-cell offline
single-Agent matrix, published Stage 1–6 regression, real current-platform Seatbelt gate and
current acceptance evidence, and was integrated locally. The user then explicitly opened S7P-09;
Subplan 90 is active and owns the repeated Morrow campaign,
same-condition Pi comparison and immutable Direct baseline. Its live lane is held until an exact
common Provider/model and campaign spend ceiling are approved.

## Stage 7 reliability sequence

| Order | File | Status |
|---|---|---|
| 79 | `79-s7p-00-evaluation-protocol.md` | completed and integrated locally |
| 80 | `80-s7p-01-observability-headless.md` | completed and integrated locally |
| 81 | `81-s7p-02-tool-contracts.md` | completed and integrated locally |
| 82 | `82-s7p-03-direct-coding-prompt.md` | completed and integrated locally |
| 83 | `83-s7p-04-workspace-change-lifecycle.md` | completed and integrated locally |
| 84 | `84-s7p-05-validation-completion-truth.md` | completed and integrated locally |
| 85 | `85-s7p-06-budget-context-no-progress.md` | completed and integrated locally |
| 86 | `86-s7p-00-05-audit-remediation.md` | completed and integrated locally |
| 87 | `87-remove-runtime-outcome-gate.md` | completed and integrated locally |
| 88 | `88-s7p-07-runtime-control-steering.md` | completed and integrated locally |
| 89 | `89-s7p-08-single-agent-function-matrix.md` | completed and integrated locally |
| 90 | `90-s7p-09-repeated-direct-pi-baseline.md` | active; live campaign at hold point |

## Stage 7 pre-baseline

| Order | File | Status |
|---|---|---|
| 78 | `78-stage7-direct-baseline-tool-gaps.md` | completed and integrated locally |

## Stage 6 sequence

| Order | File | Status |
|---|---|---|
| 63 | `63-stage6-dependency-contract-spike.md` | completed |
| 64 | `64-stage6-agent-run-preparation.md` | completed |
| 65 | `65-stage6-skill-catalog-foundation.md` | completed; review repair applied locally |
| 66 | `66-stage6-skill-lifecycle-bindings.md` | completed locally; branch unavailable |
| 67 | `67-stage6-skill-selection-context.md` | completed locally; branch unavailable |
| 68 | `68-stage6-skill-drafts-usage.md` | completed locally; branch unavailable |
| 69 | `69-stage6-skill-script-execution.md` | completed locally |
| 70 | `70-stage6-provider-model-control.md` | completed locally |
| 71 | `71-stage6-dynamic-tool-contracts.md` | completed locally |
| 72 | `72-stage6-mcp-control-catalog.md` | completed locally |
| 73 | `73-stage6-mcp-runtime-security.md` | completed locally |
| 74 | `74-stage6-backup-doctor.md` | completed locally |
| 75 | `75-stage6-acceptance-closeout.md` | completed locally |
| 76 | `76-stage6-runtime-policy.md` | completed locally |
| 77 | `77-stage6-script-diagnostics-context.md` | completed locally |

## Completed retained subplans

| Range | Stage | Status |
|---|---|---|
| 36–47 | Stage 4 implementation/remediation | completed |
| 48–55 | Stage 5 original implementation | completed |
| 56–62 | Stage 5 Preference v2/live remediation | completed |

Individual completed files remain in this directory where retained by the previous plan; older
retired files are available from Git history.

## Rules

- `.agent/PLAN.md` is the active master plan and cross-cutting contract.
- `.agent/TODO.md` contains tasks for the one active subplan only.
- Start a child from latest verified `main`; do not implement later schemas/interfaces early.
- At most one child is active. Mark `[>]` only when work actually begins and `[x]` only after its
  declared validation succeeds.
- Code and validation outrank stale planning text; update the plan before continuing with changed
  scope.
- Keep production changes inside the active child's ownership. Preserve other user/worktree changes.
- Record accepted decisions, failures, gates, dependency approvals and transitions in `.agent/LOG.md`.
- Before closing a child: commit verified progress, run gates, update execution state, fast-forward
  merge, verify no topic commits are absent from `main`, and retire the clean branch/worktree.
- Subplan 63 may recommend dependencies but cannot add them. Subplan 72 cannot edit dependency files
  until the user approves the exact change.
- No live Provider/MCP/network/credential test runs without explicit authorization and compatible
  credentials. Offline Fake/Scripted fixtures are the default.
