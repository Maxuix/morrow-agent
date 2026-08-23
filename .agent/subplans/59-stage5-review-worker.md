# Subplan 59 — Durable Asynchronous Review Worker

> Status: completed and merged at `f855c64`
> Branch: `feat/stage5-review-worker`
> Prerequisite: Subplan 58 merged
> Owns: terminal-Turn enqueue, leases/retries, process-local worker, status/notification UX

## Goal

Make Review durable and asynchronous so the main Agent returns the user's result without waiting for
Preference or accepted-Task model review. SQLite remains the queue authority; this subplan does not
introduce a daemon or external scheduler.

## Tasks

### S59.1 Atomic Turn hook

- In the existing terminal Turn commit transaction, create exactly one current-user `pev_` Evidence
  row and pending `PreferenceReviewJob` for each eligible ordinary User Turn.
- Key replay by workspace, turn, and review version. Do not enqueue slash/control commands, policy-off
  work, duplicate terminal replay, safety-rejected content, or a Turn with a successful approved
  `manage_preferences` batch.
- Persist the complete bounded frozen Active snapshot JSON/count/bytes/digest and source revisions
  needed by S58. No model call, YAML operation, or notification occurs inside the transaction.
- Keep ConversationLog as the only history writer; the hook stores references and Review state only.

### S59.2 Worker lifecycle

- Add a small `ReviewWorker` application service with explicit `start`, `wake`, `drain_once`, and
  `stop` boundaries. A process-local async signal wakes it; pending SQLite rows remain the truth.
- Claim with lease and row-version OCC, execute Provider calls outside transactions, then finalize
  proposals/job state idempotently. Expired leases are reclaimable.
- Serialize work per workspace and permit bounded cross-workspace concurrency without sharing
  mutable Session objects.
- On shutdown/cancellation, leave or release work in a retryable state. Tests use injected futures,
  clocks, and schedulers; no wall-clock sleep assertions.

### S59.3 Retry and configuration

- Add independent Preference Reviewer model configuration with fallback to the active model, default
  60-second timeout, bounded timeout validation, at most three attempts, deterministic backoff, and
  sanitized terminal failure codes.
- Provider unavailable, timeout, malformed output, lost lease, cancellation, and internal persistence
  failure never change the foreground Turn/Task result.
- Retryable codes are timeout, Provider unavailable, malformed output, lost lease, cancellation, and
  transient persistence. Terminal codes are context/request budget, safety-rejected current Evidence,
  and missing/corrupt/unsupported frozen snapshot. Manual retry accepts retryable codes only and does
  not duplicate proposals; corrected terminal input requires a new review version.

### S59.4 Existing Learning Review scheduling

- Remove interactive foreground execution of accepted-Task `LearningReview`; wake the same worker
  after the already-atomic request commits.
- Let the worker route Preference jobs to `ModelPreferenceReviewer` and legacy non-Preference jobs to
  their existing runner. Do not combine their output schemas or model authorities.
- The worker exclusively claims `PreferenceReviewJob`. For a legacy `LearningReview`, it selects a
  pending ID and calls `LearningReviewRunner.run()` without pre-claiming; the runner remains that
  table's sole claim authority and increments attempts once.
- Preserve explicit run/retry commands for deterministic headless operation and migration recovery.

### S59.5 Product observability

- Add bounded list/show/status/retry/run-pending surfaces for Preference jobs and retain existing
  Learning Review inspection.
- Between interactive prompts, show a non-blocking notice only when new proposals exist or a job has
  exhausted retries. Zero-operation completion is quiet.
- A one-shot headless command truthfully reports pending work and does not promise a hidden daemon;
  the next long-lived startup or explicit run-pending command resumes it.
- Do not publish Reviewer details through the public `AgentEvent` lifecycle; use sanitized
  application events/query projections.

## Primary files

- `src/morrow/application/preferences/worker.py`
- `src/morrow/application/preferences/jobs.py`
- one thin enqueue call in `src/morrow/application/turn_lifecycle.py`
- thin worker composition/wakeup in `src/morrow/bootstrap.py`
- focused CLI/terminal notification modules
- Preference journal claim/retry methods

## Acceptance

- A blocked fake Reviewer does not delay or alter foreground Turn completion.
- Duplicate terminal commit/replay creates one job; restart and expired lease resume once.
- Timeout and two retries are controlled by injected time, and exhaustion is visible without an
  Active write.
- Two jobs in one workspace never run concurrently; isolated workspaces can progress independently.
- Existing accepted-Task Review no longer blocks interactive Task acceptance and still persists its
  non-Preference results.
- Worker cancellation/startup/headless behavior is truthful and leaves no untracked task that only
  exists in memory.

## Validation

```bash
uv run pytest -q tests/test_preference_review_jobs.py tests/test_review_worker.py \
  tests/test_turn_lifecycle.py tests/test_stage5_review_pipeline.py \
  tests/test_stage5_learning_application.py tests/test_terminal.py tests/test_cli.py
uv run pytest -m 'not live'
uv run ruff format --check .
uv run ruff check .
uv run python -m compileall -q src tests
uv run morrow learning --help
git diff --check
```

## Review and closeout

After the implementation checkpoint and gates pass, invoke exactly one `$grok-delegate` `/review`
for S59 in the same branch/worktree and wait. Independently validate all findings, fix confirmed and
valuable items once, rerun affected/S59 gates, and do not re-review the fixes. Commit closeout,
fast-forward merge, retire the clean branch, and activate S60.
