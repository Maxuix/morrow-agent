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

S59.2 — add the lease-based process-local Review Worker lifecycle on
`feat/stage5-review-worker`.

## Next action

Commit the verified S59.1 enqueue checkpoint, then inspect the existing Review runner and v13 job
state boundaries before implementing only the worker lifecycle. Keep retry policy and model fallback
for S59.3.

## Blockers

No code blocker for S59. Real-Provider acceptance remains on hold; S59 must use scripted offline
Provider/Reviewer doubles and injected time/scheduling.

## S59.1 evidence

- Terminal Turn enqueue is now inside the existing terminal SQLite transaction. It creates one
  `(workspace_id, turn_id, review_version)` job and one current-user Evidence row, freezes the
  bounded global/workspace Preference snapshot, suppresses slash/control, policy-off, safety-
  rejected, and successful `manage_preferences` Turns, and returns replay-safe existing rows.
- `tests/test_preference_review_jobs.py` covers frozen metadata, context reconstruction, replay
  idempotency, and terminal-append rollback when enqueue fails.
- Validation passed: `891 passed, 2 deselected`; Ruff format/check, compileall, `morrow learning
  --help`, and `git diff --check`.

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
