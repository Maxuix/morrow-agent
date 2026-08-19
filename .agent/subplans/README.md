# Subplans

Stage 4 Durable Task, Session, Artifact, and Recovery is the active master plan. Subplan 35 is the
only active subplan; it changes contracts and runs disposable design spikes but does not implement
production persistence. Later subplans remain inactive until the preceding gate passes and execution
state is updated.

| Order | File | Status |
|---|---|---|
| 35 | `35-stage4-contract-activation.md` | active |
| 36 | `36-stage4-operational-store.md` | pending |
| 37 | `37-stage4-durable-session-conversation.md` | pending |
| 38 | `38-stage4-tool-journal-approval.md` | pending |
| 39 | `39-stage4-recovery-crash.md` | pending |
| 40 | `40-stage4-task-outcome.md` | pending |
| 41 | `41-stage4-artifact-store.md` | pending |
| 42 | `42-stage4-context-fork.md` | pending |
| 43 | `43-stage4-api-cli-doctor-backup.md` | pending |
| 44 | `44-stage4-full-access-manual.md` | pending |
| 45 | `45-stage4-acceptance.md` | pending |

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
