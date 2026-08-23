# Progress Tracker

## Current status

The complete Stage 5 Preference Learning v2 implementation plan is finalized after the S56 review
and independent plan-fix pass. S57–S60 are merged locally; S61 is now active.

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

S61 implementation and pre-review gates are complete on
`refactor/stage5-preference-v2-closeout`. The single planned read-only Grok review is now active;
confirmed findings will receive one independent fix pass without a second S61 review.

## Next action

Create the verified implementation checkpoint, run exactly one S61 `$grok-delegate` review, then
adjudicate once and rerun the affected/full gates before branch closeout.

## Blockers

No code blocker for S61. The opt-in real-Provider corpus and post-implementation user acceptance
remain on hold until the master plan's clean-tree condition is met.

## S61 implementation evidence

- Public `update_configuration` now manages only Workspace Profile; `/config edit` is retired and
  generic Preferences use `manage_preferences`/`/preferences`. Fixed-field validation is isolated
  in an explicitly named legacy compatibility module, marker classification remains legacy-only,
  and new AgentRun snapshots no longer freeze a fixed effective Preference projection.
- Doctor and SQLite backup verification now cover v13 Preference job/Evidence/proposal/write-batch
  links, snapshot digests/budgets, retry/lease lifecycle, and isolated YAML/credential boundaries.
- Added the frozen `preference-v2-natural-language-v1` 22-case corpus and pure scripted scoring
  harness. It records the 11/12, 90%, 6 target hits, zero safety writes, and 9/10 adherence gates
  without running or claiming real-Provider quality.
- Added migration/restore acceptance and reconciled architecture, roadmap, README, CLI help,
  acceptance, and hold documentation. The old v12 evaluator is explicitly preserved as a legacy
  baseline under its renamed resource.
- Focused S61 acceptance passed `37` tests; configuration regressions passed `34`; affected
  Preference/Stage 5 regressions passed `231` with one explicit live skip. Full offline validation
  passed `932 passed, 2 deselected`. Ruff format/check, compileall, four CLI help commands, and
  `git diff --check` passed. No Live, Provider, credential, or network path ran.

## S60 implementation evidence

- Added a pre-transaction Preference reload hook for each new AgentRun. Valid global/workspace YAML
  replaces restored Session caches; corrupt/future state becomes a degraded empty layer with a
  bounded reason, so stale rules fail closed while ordinary chat remains available.
- Added a 64-entry/8-KiB frozen generic projection with deterministic scope precedence, budget
  selection, render ordering, digest, omitted count, source scopes, and refresh status. Recovery
  rebuilds only from this frozen projection and quarantines digest mismatch instead of consulting
  current YAML.
- Context renders typed `[scope:id]` entries in a dedicated lower-authority system block after the
  safety boundary. Disabled/deleted entries are omitted, exact duplicates respect scope precedence,
  and adversarial rules cannot grant tools, skip approval, change sandbox scope, or override policy.
- Added `preferences status` and application projections separating live YAML counts/revisions,
  frozen injected count/digest/omissions, and MemorySelection ID/revision/item count. Doctor reads
  YAML without creating paths and validates frozen projection digests without exposing statements.
- Focused S60 validation passed `112` tests before the final doctor tamper regression; the complete
  offline suite passed `924 passed, 2 deselected`. Ruff format/check, compileall, `morrow --help`,
  and `git diff --check` passed. No Live, Provider, credential, or network path ran.
- The one planned Grok review completed read-only and changed no project files. It confirmed the
  reload/freeze/recovery boundaries and found one correctness bug: Memory doctor reused the combined
  Preference+Memory projection and mislabeled a Preference digest failure as
  `memory_agent_run_projection`.
- The independent fix keeps Memory doctor on its own frozen MemorySelection reference check and
  adds a mixed valid-Memory/tampered-Preference regression. Restored-Session next-Turn coverage,
  exact cross-scope render ordering, and hostile-Preference tool-list coverage were also strengthened.
- Final S60 focused validation passed `98` tests; full offline validation passed `926 passed, 2
  deselected`. Ruff format/check, compileall, `morrow --help`, and `git diff --check` passed.

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
