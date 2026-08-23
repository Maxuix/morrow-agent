# Progress Tracker

## Current status

The complete Stage 5 Preference Learning v2 implementation plan is finalized after the S56 review
and independent plan-fix pass. S57 and S58 are merged locally; S59 is now active.

## Last completed work

The original Stage 5 v12 pipeline and Subplan 55 remediation are complete. The subsequent isolated
real-Provider evaluation is committed at `c6031d2` and records:

- natural-language add/overwrite recall `0/3`;
- remove recall `0/2`;
- Mimo Review timeout under the foreground 15-second deadline;
- successful structured promotion and new-Session injection;
- stale Preference behavior in one restored existing Session.

The user accepted the refactor direction: generic atomic natural-language rules, a separate no-tool
semantic Reviewer, deterministic add/replace/remove Writer, Candidate Inbox, durable asynchronous
execution, and next-AgentRun refresh.

## Active task

The single planned Grok review and independent fix pass completed on
`feat/stage5-review-worker`. All affected and full offline gates pass; S59 is ready for its closeout
commit and fast-forward merge. No second Grok review was run.

## Next action

Commit the verified S59 closeout, fast-forward local `main`, retire the clean topic branch, and
activate S60. Do not stage the two untracked research documents.

## Blockers

No code blocker for S59. Real-Provider acceptance remains on hold; S59 must use scripted offline
Provider/Reviewer doubles and injected time/scheduling.

## S59 review and independent fix evidence

- The single Grok review completed read-only with model `grok-4.6` at `xhigh`; it changed no project
  files. Independent source and plan checks confirmed three execution defects: ordinary terminal
  Turns did not wake the worker after commit, one non-completed result stopped a whole drain, and the
  explicit Preference review command bypassed worker claim/finalization.
- Fixed all three through a post-dispatch non-blocking wake, drain continuation across deferred and
  terminal outcomes, and a worker-owned explicit `run_job` path. Added injected retry scheduling so
  lease backoff wakes without another user action.
- Also fixed two confirmed durable edge cases: oversized Active snapshots now skip supplemental
  Review without rolling back a completed foreground Turn, and an expired third-attempt lease is
  finalized as visible `exhausted/lease_lost` instead of remaining permanently claimable.
- Status projections now expose the plan-required bounded Reviewer IDs, prompt/schema versions,
  proposal count, and derived notification state while still excluding frozen context and raw model
  output. The independent model override/fallback already exists in composition; adding persistent
  config would cross the frozen config-schema boundary. The optional worker-module split is deferred
  as a dedicated refactor, not a correctness fix.
- Affected S59 regression passed `77` tests. Final offline validation passed `917 passed, 2
  deselected`; Ruff format/check, compileall, `morrow learning --help`, and `git diff --check`
  passed. No Provider, credential, Live, or real-network path ran.

## S59.1 evidence

- Terminal Turn enqueue is now inside the existing terminal SQLite transaction. It creates one
  `(workspace_id, turn_id, review_version)` job and one current-user Evidence row, freezes the
  bounded global/workspace Preference snapshot, suppresses slash/control, policy-off, safety-
  rejected, and successful `manage_preferences` Turns, and returns replay-safe existing rows.
- `tests/test_preference_review_jobs.py` covers frozen metadata, context reconstruction, replay
  idempotency, and terminal-append rollback when enqueue fails.
- Validation passed: `891 passed, 2 deselected`; Ruff format/check, compileall, `morrow learning
  --help`, and `git diff --check`.

## S59.2 evidence

- Added v13 claimable-job listing, lease/OCC claim, and immutable identity-preserving job saves.
  `ReviewWorker` now exposes explicit async `start`, signal-based `wake`, one-job `drain_once`, and
  cancellation-safe `stop` boundaries; Provider work runs outside SQLite transactions.
- Worker completion persists deterministic proposals before idempotently closing the Job. One
  workspace serializes drains through an async lock; an expired RUNNING lease is reclaimable by a
  replacement worker without sharing Session state.
- `tests/test_review_worker.py` covers proposal completion, start/stop lease recovery, and same-
  workspace serialization. Validation passed: `894 passed, 2 deselected`; Ruff format/check,
  compileall, and `git diff --check` passed. No Provider, network, credential, or Live path was used.

## S59.3 evidence

- Preference Review uses a validated default 60-second timeout, finite bounded timeout inputs, and
  deterministic 5/15-second lease backoff. Retryable provider, timeout, malformed-output,
  lease-loss, cancellation, and persistence failures are retried at most three attempts; terminal
  context/request-budget, safety, and frozen-snapshot failures become sanitized `failed` rows.
- Third retryable failure becomes `exhausted` with a v13 allowlisted failure code. Failure handling
  never stores provider messages, exceptions, or tracebacks. Tests cover retry scheduling,
  exhaustion, terminal context budget, and timeout validation. Validation passed: `901 passed,
  2 deselected`; Ruff format/check, compileall, CLI help, and `git diff --check` passed.

## S59.4 evidence

- `OperationalApplicationService.task_accept` wakes the process-local worker only after the atomic
  accepted-Task transaction commits. The REPL now reports accepted-Task Learning Review as queued and
  continues; explicit `/learn review` and `/learn retry` remain foreground commands.
- `ReviewWorker` serializes one workspace while routing Preference jobs to the leased Preference
  runner and pending legacy Learning Reviews to `LearningReviewRunner`, which remains the sole legacy
  claim authority. Bootstrap and REPL composition start/stop the same worker lifecycle.
- `tests/test_stage5_review_pipeline.py` covers post-commit wakeup and legacy Learning routing. The
  accepted-Task path no longer waits on a Reviewer.

## S59.5 evidence

- Added bounded Preference job list/show/status projections that omit frozen snapshot JSON and
  Reviewer/provider metadata, plus retry support limited to retryable terminal failure codes.
- Added explicit `preferences inbox jobs|job|status|retry|run-pending` surfaces. `run-pending` is a
  bounded one-shot drain and reports remaining pending/running work with `daemon: false`; no hidden
  daemon or external scheduler was added.
- Added process-local sanitized notices for new Preference proposals and exhausted retries. Zero-op
  completion remains quiet, and no Reviewer detail is emitted through `AgentEvent`.
- Corrected focused validation passed: `66 passed`; full offline validation passed: `910 passed,
  2 deselected`; Ruff format/check, compileall, `morrow learning --help`, and `git diff --check`
  passed. The plan's referenced `tests/test_turn_lifecycle.py` and `tests/test_cli.py` do not exist
  in this tree; the corresponding existing tests were used instead.

## Preserved workspace state

`docs/research/stage5-overview-pipeline.md` and `docs/research/stage5-overview-review.md` are untracked
user files. They remain untouched and must not be included in plan or implementation commits without
explicit authorization.

## Locked boundary

- Main Agent performs the user task; background Reviewer performs Preference semantics; deterministic
  Writer mutates state only after acceptance/direct approval.
- YAML remains Active Preference authority; SQLite stores jobs, Evidence, proposals, decisions,
  write-batch recovery, and events.
- Profile and Project Knowledge remain separate; Preference injection is not MemorySelection.
- `manage_preferences` may reuse the existing configuration-write approval/recovery class, but no
  keyword semantic classifier, fixed Preference taxonomy, auto-activation, daemon, new dependency,
  bundled capability-policy default change, or public AgentEvent change is authorized.
