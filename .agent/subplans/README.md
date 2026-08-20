# Subplans

Stage 4 Durable Task, Session, Artifact, and Recovery is accepted. Subplan 46 is an explicitly
requested post-acceptance architecture refactor; Stage 5 remains inactive.

Subplan 35 and the conditional review remediation were accepted on 2026-08-19 and preserved in Git
history at `20fb43e`; its retired task file is no longer kept in the active subplan directory.

| Order | File | Status |
|---|---|---|
| 36 | `36-stage4-operational-store.md` | completed |
| 37 | `37-stage4-durable-session-conversation.md` | completed |
| 38 | `38-stage4-tool-journal-approval.md` | completed |
| 39 | `39-stage4-recovery-crash.md` | completed |
| 40 | `40-stage4-task-outcome.md` | completed |
| 41 | `41-stage4-artifact-store.md` | completed |
| 42 | `42-stage4-context-fork.md` | completed |
| 43 | `43-stage4-api-cli-doctor-backup.md` | completed |
| 44 | `44-stage4-full-access-manual.md` | completed |
| 45 | `45-stage4-acceptance.md` | completed |
| 46 | `46-stage4-boundary-refactor.md` | active |

Completed Stage 3 Subplans 29–34 were removed from the active directory when this master plan was
created; they remain recoverable in Git history together with their accepted evidence.

## Rules

- `.agent/PLAN.md` is the living master index and cross-cutting contract.
- `.agent/TODO.md` contains executable tasks for the one active subplan only.
- Start one subplan only after its prerequisite gate passes and the user-authorized execution state
  is updated.
- Keep production changes inside the active subplan's ownership; do not implement a later slice
  early.
- When code or validation conflicts with a plan, update the stale plan before continuing.
- Record accepted decisions, meaningful failures, gates, and transitions in `.agent/LOG.md`.
- Mark a task complete only after its declared validation succeeds. Before closing a subplan,
  commit verified progress and activate the next subplan explicitly.
- Do not recreate completed subplans in this directory; use Git history for old execution detail.
