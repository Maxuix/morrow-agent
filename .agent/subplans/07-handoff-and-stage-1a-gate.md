# Subplan 07 — Handoff and Stage 1A Gate

> Stage: 1A  
> Status: completed
> Parent: [Stage 1 implementation plan](../PLAN.md)

## Objective

Close the Stage 1A product loop with reliable Profile/Handoff onboarding, model-assisted handoff generation, deterministic recovery, safe exit behavior, and full P0 acceptance.

## Prerequisites

- Subplans 03 through 06 are complete.

## Tasks

1. Fill Subplan 06's new-workspace onboarding hook: accepted name/path, optional project summary to `profile.summary`, and optional current step to `handoff.current_goal`. If a current step is saved, set its new revision as this session's `handoff_source_revision` so the session is a continuation from that checkpoint.
2. Treat a skipped current-step answer as absent data and begin an independent session without fabricated context or a loaded Handoff revision.
3. Implement exit-time Handoff generation for continuation sessions through Subplan 05's `ContextBuilder`-backed structured helper, with total timeout and cancellation support. During exit generation, `Ctrl+C` cancels the exit itself: cancel the model call, perform no fallback or write, preserve the full session, and return to the REPL. Do not expose `/handoff update` or an independent-session save command until Subplan 09.
4. Validate generated, repaired, and deterministic-fallback Handoffs through Subplan 03's complete Handoff model, including normalized decision uniqueness. Allow at most one model repair attempt within the total budget.
5. Implement the deterministic fallback rules for an existing continuation, preserving prior decisions/open items and adding a bounded sanitized recovery note.
6. Limit and sanitize recovery-note content; ensure the next successful generated Handoff can absorb and clear it.
7. Publish Handoffs through expected revision and atomic state writes.
8. On storage/revision failure, preserve the previous valid Handoff, make the failure visible, and exit with code `2`; a successful deterministic fallback exits with code `0` and an explicit degraded-Handoff notice.
9. After onboarding writes succeed, replace the typed Profile/Handoff snapshots before constructing the first conversation baseline so the first model turn sees exactly the state just saved.
10. Finalize Stage 1A `/workspace`, `/handoff`, `/status`, `/continue`, `/exit`, and EOF behavior.
11. Map every `S1A-01` through `S1A-08` requirement to automated or explicit manual/Live evidence.
12. Run real-project trials in isolated test projects and record handoff usefulness, generation outcome, fallback occurrence, cancellation, and terminology observations without storing sensitive conversation content.

## Verification

- A continuation session's normal exit saves a valid generated Handoff; an independent session's exit performs no Handoff generation and leaves any on-disk Handoff byte-identical.
- Cancelling exit-time generation returns to the REPL with the in-process session and on-disk Handoff unchanged; timeout/model/schema failure instead exercises fallback and continues exit.
- Timeout, Provider error, and invalid structured output produce a valid deterministic fallback where storage is healthy.
- Generated, repaired, and fallback Handoffs all reject duplicate normalized decision text.
- Every failure-injected storage path leaves the old Handoff valid and visible.
- Starting again displays the new revision but does not inject it until explicit continuation.
- The first post-onboarding model turn observes the newly saved Profile and, when present, Handoff revision.
- The full offline Stage 1A suite passes with no unexpected skips or network requests.
- Real Provider and real terminal smoke checks satisfy the manual acceptance checklist.
- A credential sentinel scan passes across persisted state, events, logs, terminal capture, and Handoff content.

## Completion criteria

- All `S1A-01` through `S1A-08` gates pass with recorded evidence.
- The vertical product claim is demonstrated: identify the workspace, converse continuously, and leave a reliable next handoff.
- Any failed gate reopens its owning subplan; no failure is deferred to Stage 1B.
- Stage 2 may be discussed, but its implementation remains blocked until Subplan 11.

## Deliverables

- Reliable Handoff generation and deterministic fallback.
- Stage 1A end-to-end acceptance suite and manual/Live record.
- Product feedback notes used to refine only allowed presentation/default choices.
