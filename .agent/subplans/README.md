# Subplans

Stage 4 and the original Stage 5 v12 implementation are complete through Subplan 55. The real-
Provider evaluation then demonstrated that the fixed-field, marker-gated Preference path has
unacceptable natural-language recall and stale existing-Session injection. On 2026-08-21 the user
approved drafting a Preference v2 refactor with a separate asynchronous Reviewer and deterministic
Writer. Subplans 56–61 and the separate integrated review/fix/gate pass are complete. The 2026-08-23
live corpus exposed a remaining Reviewer quality gap; Subplan 62 is active.

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
| 46 | `46-stage4-boundary-refactor.md` | completed |
| 47 | `47-stage4-real-user-remediation.md` | completed |
| 48 | `48-pre-stage5-boundary-refactor.md` | completed |
| 49 | `49-stage5-learning-foundation.md` | completed |
| 50 | `50-stage5-review-pipeline.md` | completed |
| 51 | `51-stage5-inbox-project-knowledge.md` | completed |
| 52 | `52-stage5-configuration-promotion.md` | completed |
| 53 | `53-stage5-memory-selection-context.md` | completed |
| 54 | `54-stage5-reviewer-acceptance.md` | completed |
| 55 | `55-stage5-simulated-user-remediation.md` | completed |
| 56 | `56-stage5-preference-foundation.md` | completed |
| 57 | `57-stage5-preference-writer.md` | completed |
| 58 | `58-stage5-preference-reviewer-inbox.md` | completed |
| 59 | `59-stage5-review-worker.md` | completed |
| 60 | `60-stage5-preference-context.md` | completed |
| 61 | `61-stage5-preference-closeout.md` | completed |
| 62 | `62-stage5-live-reviewer-remediation.md` | active |

Completed Stage 3 Subplans 29–34 were removed from the active directory when this master plan was
created; they remain recoverable in Git history together with their accepted evidence.

## Rules

- `.agent/PLAN.md` is the living master index and cross-cutting contract.
- `.agent/TODO.md` contains executable tasks for the one active subplan only.
- Start one subplan only after its prerequisite gate passes and the user-authorized execution state
  is updated. For Preference v2, create the branch listed in that subplan from the latest verified
  `main`.
- Keep production changes inside the active subplan's ownership; do not implement a later slice
  early.
- When code or validation conflicts with a plan, update the stale plan before continuing.
- Record accepted decisions, meaningful failures, gates, and transitions in `.agent/LOG.md`.
- Mark a task complete only after its declared validation succeeds. Every S56–S61 implementation
  result receives exactly one `$grok-delegate` `/review`, followed by one independently adjudicated
  fix pass and no second review. Before closing a subplan, commit verified progress and activate the
  next subplan explicitly.
- Do not recreate completed subplans in this directory; use Git history for old execution detail.
