# Subplans

Stage 4 and Stage 5 are complete through Subplan 62 and remain recoverable in Git history. On
2026-08-24 the user explicitly requested a fact-checked final Stage 6方案 and a complete executable
implementation plan. Stage 6 is now complete locally through Subplan 75; the requested full
post-closeout review and fix cycle is also complete.

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
