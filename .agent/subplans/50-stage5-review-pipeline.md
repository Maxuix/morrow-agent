# Subplan 50 — Accepted Outcome to Candidate Pipeline

> Status: planned
> Branch: `feat/stage5-review-pipeline`
> Prerequisite: Subplan 49 complete and merged into verified `main`
> Owns: Task acceptance hook, explicit re-review, Evidence/LearningContext, lease runner, candidates
> Does not own: Candidate user decisions, Active Knowledge/config writes, production Reviewer adapter

## Objective

Turn an accepted Stage 4 Outcome into zero to three safe, deduplicated, reviewable Candidates while
proving that Task acceptance and Review execution remain independent. This slice uses an injected
Fake Reviewer for deterministic tests and permits only deterministic production draft rules; the
real Provider-backed Reviewer arrives in Subplan 54.

## End-to-end sequence

```text
OperationalApplicationService.task_accept()
  BEGIN outer SQLite transaction
    TaskService.accept() → accepted Task + TaskOutcome
    LearningReviewRequestService.ensure_pending(outcome, policy snapshot)
    application events: task.accepted, learning.review_requested
    application command receipt
  COMMIT

foreground Review runner (optional immediately in REPL; explicit in headless CLI)
  transaction A: claim pending/expired-running Review, create lease, increment attempt
  outside transaction: extract Evidence, build LearningContext, call Reviewer
  transaction B: validate + dedup/conflict/suppress, persist Evidence/Candidates, complete Review
```

No await/model call occurs before the Task acceptance transaction commits.

## Trigger contract

- `TaskService` remains unchanged and unaware of Learning.
- Add a small acceptance coordinator/result hook used inside
  `OperationalApplicationService._task_command()` only when `operation == task_accept` and
  `result.outcome is not None`.
- Effective policy `off` returns no Review. An accepted Outcome with unresolved/unknown execution
  facts is not automatically reviewed; emit a bounded not-requested reason or expose it through the
  query result without persisting unsafe content.
- Ensure exactly one Review version 1 for one automatic accepted Outcome. Task command replay never
  creates a second Review or event.
- Event order is `task.accepted` then `learning.review_requested` in the same transaction. The Task
  receipt continues to identify the Task command's primary event and result.
- Add explicit `RequestLearningReviewCommand(outcome_id, expected_latest_version, command_id)` for a
  user-requested new Review. It creates version N+1, supersedes the prior Review, and never changes
  Task/Outcome state.

## Claim, lease, retry, and cancellation

- Claim runs in a short write transaction and requires `pending`, or `running` with an expired
  lease. It stores a new opaque lease ID, expiry, Reviewer metadata, attempt count, row version, and
  `learning.review_started` event.
- Lease duration and max attempts are bounded policy constants with injected clock tests; no test
  sleeps on wall time.
- Finalize requires the same lease ID and current row version. A stale worker cannot write results.
- A process crash after claim leaves `running`; the next explicit runner may reclaim after expiry.
- Provider/cancellation/timeout/invalid-result failures commit only Review failure/retry facts and a
  sanitized `learning.review_failed` event. They do not touch Task/Outcome.
- Explicit retry creates a new claim attempt for a retryable failed Review or a new Review version
  when the previous result is intentionally superseded. Never mutate completed Candidates in place.
- There is no loop/scheduler that searches indefinitely. One runner call handles one identified
  Review and returns a truthful terminal result.

## Deterministic Evidence extraction

Implement `EvidenceExtractor` over narrow read ports. It may inspect only records already linked to
the accepted Task/Outcome:

- Outcome and its typed Evidence/Artifact refs.
- User-authored conversation records belonging to Task Turns, subject to count/character budget.
- the existing `ready_for_acceptance → open` transitions paired with the following User Turn as a
  correction/continuation hint—not a new Task state;
- deterministic Tool/Artifact facts already summarized in TaskOutcome;
- current active Profile/Preferences summaries needed for duplicate/conflict checks;
- applicable suppressions.

It does not copy the entire ConversationLog, raw Tool results, Artifact bytes, credentials, or
unbounded Assistant text. Assistant messages may be represented only as low-authority source refs;
they cannot become explicit user Evidence.

This first release does not introduce a standalone `TaskFeedback` table. The User Turn and Task
transition remain the durable source; LearningEvidence is the typed learning projection.

Extraction order:

1. Build source candidates and authority/actor metadata.
2. Normalize/bound excerpts.
3. Run secret, prohibited-data, Unicode, capability, and injection safety scans.
4. Reuse or persist safe Evidence (or digest-only blocked Evidence) and link it to the current
   Review.
5. Apply the candidate eligibility matrix before Reviewer input.

Evidence may be saved even when no Candidate is eligible. This is expected for a first behavioral
signal or a rejected unsafe source.

## LearningContext

Add a strict bounded model containing only:

- accepted `TaskOutcome` projection;
- eligible Evidence records (IDs plus bounded safe excerpt/authority metadata);
- relevant current target summaries/digests, not the full global state;
- active suppressions relevant to those semantic keys;
- immutable LearningPolicy snapshot and candidate budget.

Maximum rendered input is a named constant and is validated before the Reviewer call. Context
construction is independent of `ContextBuilder`, Session history projection, tools, and
`complete_structured()`.

## Candidate validation pipeline

Apply this deterministic order to every draft:

```text
strict discriminated schema
→ evidence IDs are a non-empty subset of LearningContext
→ evidence eligibility matrix for candidate type
→ temporary/durable and scope validation
→ secret/PII/capability/injection/hidden-Unicode safety
→ type-specific semantic validation
→ canonical fingerprint
→ current Active-state duplicate/conflict check
→ proposed-Candidate duplicate check
→ suppression match
→ confidence band/basis calculation
→ per-Review budget
→ persistence
```

Eligibility minimums:

| Candidate type | Minimum authority |
|---|---|
| Preference | explicit durable user intent, or repeated user-authored behavior across independent Tasks; first version only emits the explicit case |
| Profile | explicit user statement; never infer personality/identity from style |
| Project Knowledge | user confirmation, or deterministic Task/Artifact fact plus accepted Outcome |
| SkillCandidate | verified multi-step procedure evidence; acceptance still cannot create a Skill |
| WorkflowFeedback | actual future Workflow edit/run evidence; normally zero in Stage 5 |
| OrchestrationPolicyCandidate | signal only; cannot activate before Stage 8 |

Exact duplicate behavior:

- Existing proposed same fingerprint: attach new eligible Evidence; do not create another Candidate.
- Existing Active equivalent state: retain Evidence/Review result and create no Candidate.
- Same semantic key with different value: create one Candidate with explicit conflict refs.
- Active suppression match: persist the Review's suppressed count/reason, not a new Candidate.
- Prior rejected Candidate without suppression may be proposed again only with new explicit evidence
  or after the defined cooldown; deterministic tests freeze the clock.

## Foreground execution and composition

- Add `LearningReviewCoordinator` and one-shot `LearningReviewRunner` to shared operational
  composition.
- Interactive `/accept` returns a typed `learning_review_pending` action after the acceptance commit.
  Terminal prints a short “reviewing” status, awaits exactly one runner call, then reports zero or
  candidate IDs/count and `/learn inbox`; it does not force an immediate decision.
- `morrow task accept` enqueues only and prints a truthful pending-review hint/ID when eligible.
- Add an explicit headless `morrow learning review <review-id>` or equivalent command that composes
  the Provider only when execution is requested. In this subplan production may report Reviewer
  unavailable except for deterministic rules; it must not install the Fake Reviewer.
- Ctrl+C during Review releases/requeues safely or records a retryable cancellation according to
  whether the lease is still owned. The accepted Task remains accepted.

## Application events

Add sanitized events with IDs/counts/status/reason codes only:

- `learning.review_requested`
- `learning.review_started`
- `learning.review_completed`
- `learning.review_failed`
- `learning.candidate_proposed`

Candidate values and Evidence excerpts are queried through authorized services and never embedded
in event payloads.

## Tasks

### S50.1 Acceptance hook and explicit request

- Add the application-level hook inside the current outer transaction.
- Add Review request/query DTOs and idempotent explicit re-review.
- Prove policy-off, unsafe Outcome, task replay, transaction rollback, and event ordering.

### S50.2 Claim/lease lifecycle

- Implement claim, lease expiry/reclaim, max attempts, finalize/fail, and stable errors.
- Add deterministic crash points around claim and finalize without holding transactions externally.

### S50.3 Evidence and LearningContext

- Implement bounded source reads, actor/authority classification, safety/redaction, correction
  hints, current-state/suppression projection, and exact context budgets.
- Prove full conversation/tool bytes/credentials cannot enter the Reviewer input.

### S50.4 Reviewer draft validation

- Integrate the Fake Reviewer through the port.
- Implement the ordered validation/eligibility/confidence/fingerprint pipeline and maximum-three
  budget.
- Persist only validated Evidence/Candidates and bounded Review outcome counts.

### S50.5 Duplicate/conflict/suppression/re-review

- Implement exact proposed/Active duplicate handling, semantic-key conflicts, suppression match,
  rejected cooldown, evidence aggregation, and Review supersession.
- Prove replay and concurrent finalize cannot duplicate candidates.

### S50.6 Foreground runner and composition

- Add one-shot interactive/headless orchestration without a worker.
- Preserve Task API result compatibility and public runtime events.
- Add truthful UI actions/hints but defer Inbox decisions to Subplan 51.

### S50.7 Closeout

- Reconcile docs, run focused/full gates, merge verified work, and prepare Subplan 51.

## Fault and regression matrix

- failure before/after Task/Outcome/Review/event/receipt writes rolls back the entire acceptance
  transaction;
- failure after Task commit but before Review claim leaves pending Review and accepted Task;
- crash after claim, lease expiry, stale lease finalization, competing claim, and max attempts;
- Fake Reviewer zero/one/three/four drafts, unknown fields, invented Evidence ID, wrong workspace,
  oversize output, secret output, injection output, and exception/cancellation;
- same Task command replay, explicit re-review replay, duplicate fingerprint, current Active
  duplicate, semantic conflict, suppression, and cooldown;
- cancelled/failed/abandoned Tasks never auto-request Review;
- Stage 4 Task/Outcome/application event/recovery behavior remains unchanged.

## Planned tests and gate

New tests:

- `tests/test_stage5_review_pipeline.py`
- `tests/test_stage5_learning_context.py`

Focused gate includes those plus:

```text
tests/test_stage4_task_outcome.py
tests/test_stage4_application_api.py
tests/test_stage4_recovery_crash.py
tests/test_stage4_session_conversation.py
tests/test_configuration_tool.py
tests/test_structured.py
tests/test_architecture_boundaries.py
tests/test_terminal.py
tests/test_stage4_cli_operational.py
```

Finish with Ruff format/check, compileall, relevant CLI help, `git diff --check`, and the full
non-live suite because this slice changes the Task acceptance transaction.

## Completion gate

An accepted eligible Task atomically owns exactly one pending Review; one explicit foreground
runner can safely produce zero-to-three validated Candidates through a Fake Reviewer; every failure
leaves Task/Outcome truth intact; duplicates/suppressions/conflicts are deterministic; no model call
occurs in a write transaction; no background promise exists; and all gates pass on merged `main`.

## Out of scope

- Candidate accept/edit/reject commands.
- Active Project Knowledge or Profile/Preference writes.
- Production Provider-backed Reviewer.
- Memory selection or context injection.
- Natural-language commands or automatic retry worker.
