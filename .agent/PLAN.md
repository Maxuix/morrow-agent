# Stage 5 Preference Learning v2 Refactor Plan

> Status: S59 complete after merge and final offline gates
> Active subplan: 60 — Fresh Preference Context and Observability
> Planning baseline: `f855c64` on local `main`
> Target state: generic atomic Preferences, semantic Reviewer, durable asynchronous Review,
> reviewable Inbox, deterministic Writer, and next-AgentRun injection

## 1. Objective

Replace the low-recall fixed-field Preference path with one small, explicit pipeline:

```text
durably completed User Turn
→ persist PreferenceReviewJob + user Evidence reference
→ return the foreground result immediately
→ background Preference Reviewer reads the full bounded message and active Preference snapshot
→ emit 0..N add / replace / remove operations
→ deterministic evidence, safety, target, length, count, and revision checks
→ independent Preference proposals enter the Inbox
→ user accepts, edits, or rejects
→ deterministic Preference Writer applies one same-scope atomic YAML batch
→ the next new AgentRun reloads and injects Active Preferences
```

The refactor is successful only when natural-language add, replace, and remove work with a real
Provider; Review never blocks the user's conversation; accepted Preferences are independently
manageable; an existing Session sees changes on its next AgentRun; and the current Stage 4/5 safety,
persistence, recovery, and Project Knowledge boundaries remain intact.

## 2. Why this refactor is required

The real-Provider evaluation at `docs/acceptance/stage5-real-provider-evaluation.md` established:

- natural-language Preference add/overwrite recall was `0/3`, while schema-led prompts were `2/2`;
- natural-language and schema-led remove were both `0/2`;
- the marker classifier had already persisted the user message, but its authority labels and the
  broad `CandidateDraft` union became hard gates before a Preference could reach the Inbox;
- the first Reviewer request did not expose the complete output schema;
- the 15-second foreground Review deadline timed out twice with Mimo;
- accepted Preferences reached new Sessions, but a restored existing Session could retain a stale
  Preference projection for one later task.

The failure is therefore architectural, not a request for a larger keyword list. This plan removes
keyword-based semantic authority, removes fixed Preference domains, gives the Reviewer one minimal
operation protocol, and moves Review execution outside the foreground user path.

## 3. Authority and scope

1. The current user decision and later explicit changes.
2. Current code and validation just run.
3. This plan and the one active child subplan during implementation.
4. `docs/roadmap/stage-5-reviewable-learning-and-memory.md` for unaffected Stage 5 behavior.
5. Existing acceptance/research documents as evidence and decision history, not parallel specs.

This refactor changes only the Preference path and Review scheduling needed to keep it off the
foreground path. Profile and Project Knowledge retain their current domains and authorities. Skills,
Workflow activation, capability policy, public runtime events, and ConversationLog ownership are out
of scope.

## 4. Locked architecture

### 4.1 Main Agent, Reviewer, and Writer are separate

- The main Task Agent performs the user's task. It does not classify the same message for long-term
  learning and does not run the Preference Reviewer.
- `review_preferences` is a worker-only internal capability wrapper around `PreferenceReviewer`.
  The Reviewer itself is a no-tool model boundary: it can read only a frozen bounded Review context
  and return structured operations. It cannot call the Writer or mutate any state, and this
  capability is never registered in the main Task Agent's tool set.
- `PreferenceWriter` is deterministic. It applies validated operations after explicit Candidate
  acceptance or an explicitly approved direct Preference-management request.
- The Writer capability is not present in the Reviewer's tool set. The ordinary Agent may receive a
  separately approved `manage_preferences` tool only for an explicit direct management request; an
  inferred Preference always goes through the Inbox.
- CLI, REPL, the direct tool, and future GUI adapters call the same application services. None writes
  YAML or SQLite directly.

### 4.2 Trigger and asynchronous execution

- Every durably completed ordinary User Turn is eligible, independent of later Task acceptance.
- The terminal Turn transaction persists exactly one `PreferenceReviewJob` and exactly one current-
  user `PreferenceEvidence` row (`pev_` prefix) keyed by
  `(workspace_id, turn_id, review_version)`. Every emitted operation must cite that one ID.
  Slash/control commands, disabled learning policy, and a Turn that already completed an approved
  `manage_preferences` write do not enqueue a job.
- The transaction performs no model call, terminal wait, YAML write, or external I/O.
- After commit, the interactive application only wakes an in-process worker and returns control. A
  headless process may exit with the durable job still pending; the next long-lived process resumes
  it, and an explicit run-pending command remains available.
- This release does not add a daemon or scheduler. SQLite is the queue authority; an in-process
  signal is only a wake-up optimization.
- Jobs use leases and at-least-once execution with idempotent finalization. Work is serial within a
  workspace and may be parallel across workspaces.
- Default Reviewer timeout is 60 seconds, configurable within a bounded range. There are at most
  three attempts with deterministic retry scheduling. Timeout or Provider failure never changes the
  foreground Turn or Task result.
- Existing accepted-Task `LearningReview` work also moves off the foreground path. Its non-Preference
  behavior remains available; after cutover it cannot create a second fixed-field Preference.
- `PreferenceReviewJob` is claimed only by `ReviewWorker`. For legacy `LearningReview`, the worker
  selects a pending ID and calls `LearningReviewRunner.run()`; that runner remains the sole claim
  authority and the worker must not pre-claim it.

### 4.3 Reviewer input

`PreferenceReviewContext` contains only:

- the complete current user message, subject to the existing durable message limit;
- a small bounded recent dialogue window for reference, with actor labels and no tool payloads,
  system prompt, private reasoning, credentials, or raw Artifact content;
- current user-message Evidence IDs that operations are allowed to cite;
- a frozen list of every Active global/workspace Preference with IDs, statements, status, entry
  revision, document revision, and scope, bounded at 256 entries and 192 KiB serialized;
- explicit operation and rendered-character budgets.

The current user message is authoritative evidence. Prior Assistant text is reference-only and can
never support a Preference operation. Operational Store v13 stores the frozen Active snapshot JSON,
entry count, byte count, and digest so delayed execution cannot silently reinterpret newer YAML.
Digest/revision alone is insufficient because YAML retains only bounded backup history.

There is no persistent-marker, temporary-marker, quotation-marker, language, response-detail, or
topic classifier before the model. Deterministic code continues to reject secrets, prohibited
personal data, hidden controls, capability grants, prompt-injection payloads, invented Evidence IDs,
oversized output, and invalid targets.

### 4.4 Minimal Reviewer protocol

The first and every Reviewer request contains the complete minimal JSON schema. There is no broad
discriminated `CandidateDraft` union and no model repair sub-call.

```json
{
  "operations": [
    {
      "operation": "add",
      "statement": "回答代码问题时，先给出可运行代码，再解释关键设计。",
      "scope": "workspace",
      "evidence_ids": ["pev_xxx"]
    },
    {
      "operation": "replace",
      "preference_id": "pref_xxx",
      "statement": "代码说明保持简洁，只解释关键设计。",
      "scope": "workspace",
      "evidence_ids": ["pev_xxx"]
    },
    {
      "operation": "remove",
      "preference_id": "pref_yyy",
      "scope": "workspace",
      "evidence_ids": ["pev_xxx"]
    }
  ]
}
```

Rules:

- zero operations is a successful semantic result;
- a Review returns at most eight operations, each statement is normalized and at most 512
  characters, and the complete response remains under the bounded response budget;
- `add` has a statement and no target; `replace` has one existing target and a statement; `remove`
  has one existing target and no statement;
- learned operation scope is exactly `global | workspace`; `session` is direct/process-local only
  and is rejected rather than coerced;
- replace/remove targets must exist in the frozen Active snapshot, match scope, and appear at most
  once in the batch;
- add compares exact-normalized statements in the same scope against `active` and `disabled` entries.
  Active matches are duplicates; disabled matches require explicit enable/replace and reject add;
  deleted tombstones never block a new add, which receives a new ID at revision 1;
- semantic similarity and contradiction are left to the Reviewer and the user's Inbox decision, not
  another classifier;
- every operation carries `evidence_ids` for protocol stability, but v2 requires array length exactly
  one and it must equal the job's current-user `pev_` ID;
- Provider/model malformed output fails the attempt and follows job retry policy. Raw output and
  private reasoning are never persisted.

### 4.5 Generic Preference state

Fixed fields `language`, `response_detail`, and `instructions` are removed from the active/public
Preference model. Each rule is one independently manageable entry:

```json
{
  "preference_id": "pref_xxx",
  "statement": "回答代码问题时，先给出可运行代码，再解释关键设计。",
  "scope": "workspace",
  "status": "active",
  "revision": 1,
  "evidence_ids": ["pev_xxx"],
  "created_at": "...",
  "updated_at": "..."
}
```

Locked lifecycle:

- durable scopes are `global` and `workspace`; process-local `session` entries use the same rule
  shape but are not written to YAML;
- status is `active | disabled | deleted`; deleted is a logical tombstone and cannot be enabled;
- add creates revision 1; replace preserves ID, updates the statement, and increments revision;
  remove marks deleted and increments revision; disable/enable are explicit direct lifecycle
  commands and increment revision;
- migrated entries may have no new Preference Evidence ID; historical SQLite activation evidence
  remains readable. New learned entries require at least one valid Preference Evidence ID;
- entry Evidence IDs are bounded; complete change history remains in SQLite write-batch/events, not
  duplicated without limit in YAML.

YAML remains the sole Active Preference authority. SQLite stores Review jobs, Evidence, proposals,
decisions/write batches, recovery metadata, and events, but no second runtime Active value.

Exact duplicate uniqueness is same-scope and considers `active | disabled`, never `deleted`.
Cross-scope non-identical or conflicting rules may coexist. Exact cross-scope duplicates may be
stored but are collapsed only in the AgentRun injection projection.

### 4.6 Writer and atomicity

`PreferenceWriter` accepts a `PreferenceWriteBatch` with one scope, one expected YAML document
revision, one command ID, and one to eight operations.

- It loads one document, validates every operation against the same starting revision, applies the
  complete batch in memory, validates the resulting document, and publishes YAML once.
- Mixed global/workspace operations are separate batches because two files cannot be atomic. The
  API rejects a mixed-scope batch instead of claiming cross-file atomicity.
- Add IDs are allocated and durably recorded during prepare, so replay cannot create different IDs.
- Candidate acceptance uses a recoverable prepare → YAML apply → SQLite finalize saga. Exact
  before/after revision and digest permit safe replay; any unrelated YAML drift becomes
  `needs_resolution` and is never overwritten.
- A batch finalizes all included Candidate decisions together or none. Singleton acceptance uses the
  same path. Cancellation before confirmation produces no write batch.
- `manage_preferences` uses this same Writer with normal persistent-write approval; the Reviewer has
  no access to it.
- `manage_preferences` reuses `OperationKind.CONFIGURATION_WRITE`, the existing approval path, and a
  dedicated recovery declaration. It does not change bundled `agent-policy.toml` or PermissionProfile
  defaults. Its successful batch records the source Turn so terminal enqueue can suppress same-Turn
  double handling; exact duplicate proposal checks remain a second idempotent guard.

### 4.7 Inbox behavior

- Every validated Reviewer operation becomes one independent proposed Preference Candidate linked
  to its Review job and Evidence.
- The Inbox shows operation, target ID when present, statement, scope, Evidence excerpt/source,
  expected target/document revisions, and any stale/conflict state.
- Users can accept, edit-and-accept, reject, or reject-and-suppress one Candidate. A bounded
  same-scope accept-many path may submit several Candidates as one Writer batch.
- Candidate edits cannot change Evidence or point to an ID outside the Candidate's frozen scope.
- Stale replace/remove never silently retargets. The user must refresh/edit or request re-review.
- Existing historical fixed-field Preference Candidates remain readable. Unresolved legacy
  Candidates use a compatibility translator into generic operations; no new Reviewer can create
  their old payload shape.

### 4.8 YAML and Operational Store migration

- Operational Store v13 is owned exclusively by Subplan 56 and reserves the complete Preference v2
  job/evidence/proposal/write-batch schema, including `active_snapshot_json`, count, bytes, and digest
  with limits of 256 entries and 192 KiB. Later subplans cannot edit its committed checksum.
- Global config and workspace Preference documents receive independent schema constants. The
  workspace Profile and workspace index schemas do not change merely because Preferences change.
- Legacy mappings are deterministic and idempotent. IDs derive from
  `(scope, legacy_path, original_value, occurrence_index)`, never rendered text. Exact statements are:
  - `language=x` → `回答时默认使用 x。`;
  - `response_detail=concise` → `回答默认保持简洁。`;
  - `response_detail=balanced` → `回答默认在简洁与细节之间保持平衡。`;
  - `response_detail=detailed` → `回答默认提供详细说明。`;
  - each instruction is whitespace-normalized without semantic rewriting.
  Conversion order is language, response detail, then instruction order; when an instruction exactly
  equals an earlier mapped statement, the earlier mapped entry wins.
- Migration deduplicates exact normalized statements, preserves scope and document revision history,
  creates a recoverable backup before publishing, and refuses corrupt/future schemas.
- Immutable historical AgentRun snapshots are not rewritten. The legacy decoder must be wired into
  every stored AgentRun/backup parse before the first commit that changes the live Preference snapshot
  shape (S57), because Pydantic otherwise ignores old fields and silently produces an empty set.
  Every new AgentRun then uses the generic shape; S60 consumes the already-decoded projection.
- Legacy Preference activation undo that can no longer satisfy its old YAML digest is surfaced as
  migration-superseded, never reported as a successful undo. The new entry can still be replaced,
  disabled, or removed explicitly.
- Global `config.yaml` remains one aggregate with one schema/revision and always retains Preferences,
  Providers, and `active_model`. S56 decodes v1 fixed fields into entries in memory without publishing.
  After S57 cutover, any successful Provider/model or Preference mutator publishes the complete v2
  aggregate, preserves the untouched half, increments the shared revision once, and backs up the full
  file. Workspace `preferences.yaml` v3 remains independent of Profile v2.

### 4.9 Next-AgentRun context injection

- At admission of every new foreground AgentRun, load the latest valid global and workspace
  Preference documents; do not trust a restored Session's cached persistent projection.
- A same-AgentRun model/tool cycle keeps its frozen Preference snapshot. A change becomes visible on
  the next AgentRun, including the next Turn of an already restored Session.
- Review and AgentRun snapshots are distinct: the Review job stores all Active durable entries within
  its 256-entry/192-KiB v13 cap for replace/remove targeting; an AgentRun stores only the injected
  projection of at most 64 entries and 8 KiB plus source revisions.
- Injection exact-deduplicates by choosing `session > workspace > global`. Budget selection uses that
  scope priority, then `updated_at` descending and `preference_id` ascending. Selected entries render
  `global → workspace → session`, then `updated_at` ascending and ID ascending so more-specific/newer
  instructions appear later. Non-equal conflicting statements at different scopes both render;
  omissions are counted and visible in diagnostics.
- Preference instructions are lower authority than system, developer, capability, and tool policy.
  A stored rule cannot grant a tool, bypass approval, change sandbox scope, or override safety.
- Preference injection and Project Knowledge `MemorySelection` remain separate typed sources.
  Status/doctor output reports both so an empty Knowledge selection is not mistaken for missing
  Preferences.

### 4.10 Reviewer model configuration and observability

- Preference Reviewer provider/model configuration is independent from the Task Agent model, with a
  documented fallback to the active model when no override exists.
- Status exposes only bounded provider/model IDs, prompt/schema versions, job state, attempts,
  timestamps, failure code, proposal count, and notification state. It excludes raw output,
  tracebacks, credentials, and full conversation text.
- Completed zero-operation jobs are quiet. New Inbox proposals produce a non-blocking notice between
  prompts. Failed/exhausted jobs remain visible and can be retried explicitly.
- Terminal/no-retry outcomes are request/context budget overflow, safety-rejected current-user
  Evidence, and missing/corrupt/unsupported frozen snapshots. Retryable outcomes are timeout,
  Provider unavailable, malformed output, lost lease, cancellation, and transient persistence.
  Manual retry accepts retryable codes only; changed/fixed terminal input requires a new review
  version. These distinctions prevent one bad job from stalling the serial workspace queue.

## 5. Target module ownership

| Boundary | Target modules | Responsibility |
|---|---|---|
| Preference domain | `core/preference_models.py`, `core/preference_operations.py`, bounded `core/preferences.py` | Entries, lifecycle, operation batches, deterministic projection/render rules |
| YAML authority | `adapters/state/preference_yaml.py`, thin composition in `adapters/state/yaml.py` | Versioned global/workspace documents, migration, backup, OCC writes |
| Preference persistence | `adapters/state/preference_journal.py`, `adapters/state/migrations_v13_preferences.py`, thin migration registration | Jobs, Evidence, frozen snapshots, proposals, decisions/write-batch recovery |
| Reviewer adapter | `adapters/models/preference_reviewer.py` | One no-tool minimal schema call, bounded parsing and sanitized errors |
| Application services | `application/preferences/` modules | context, proposal validation, Writer, Inbox, recovery, worker, queries |
| Runtime integration | `application/turn_lifecycle.py`, `application/context.py`, `bootstrap.py` | atomic enqueue, worker wakeup, fresh AgentRun projection, composition |
| User surfaces | `interfaces/preferences_cli.py`, existing learning/terminal adapters | list/manage rules, Inbox, job status/retry, truthful notifications |
| Direct tool | focused tool adapter beside current configuration adapter | approved `manage_preferences` only; Profile stays on configuration service |

Do not add Preference v2 responsibilities to the existing 738-line `core/learning.py`, 404-line
Review runner, 558-line bootstrap, 463-line YAML adapter, 1,484-line migration module, 573-line
configuration service, or 900+ line Learning Inbox. Those files receive only thin imports, dispatch,
or composition. New production modules should normally remain below roughly 300 lines; crossing that
point requires a responsibility split, not helper compression. S56 also owns the `core/store.py`
supported-version update.

## 6. Sequential subplans

1. **Subplan 56 — Generic Preference foundation and migrations**
   Add the generic domain contracts, schema-separated YAML codec/migration, Operational Store v13,
   bounded persistence ports/adapters, and legacy fixtures. Do not activate the new Reviewer yet.
2. **Subplan 57 — Atomic Writer and direct management lifecycle**
   Implement prepare/apply/finalize/recovery, add/replace/remove plus enable/disable, the approved
   multi-operation tool, CLI/REPL management, fixed-field configuration cutover, legacy Candidate
   translation, and legacy AgentRun decoder activation before changing the live snapshot shape.
3. **Subplan 58 — Semantic Reviewer and Preference Inbox**
   Add the minimal no-tool Reviewer protocol, full-message bounded context, deterministic validation,
   independent proposals and Inbox decisions, then prevent new legacy fixed-field candidates.
4. **Subplan 59 — Durable asynchronous Review execution**
   Enqueue Preference Review at terminal Turn commit, add lease/retry worker orchestration, move
   existing accepted-Task Reviews off the foreground path, and add status/retry/notification UX.
5. **Subplan 60 — Fresh context injection and observability**
   Reload active entries for every new AgentRun, preserve same-Run freeze/recovery, separate
   Preference injection from Project Knowledge selection, and close the restored-Session stale bug.
6. **Subplan 61 — Compatibility retirement, quality gates, and documentation**
   Remove public fixed-field schemas and keyword classifier residue, complete doctor/backup/migration
   coverage, add the semantic evaluation corpus and live harness, and reconcile architecture,
   roadmap, README, and acceptance documents.

Subplans are sequential and each starts from the latest verified `main`. The detailed child plans in
`.agent/subplans/56-*.md` through `61-*.md` define file ownership, tests, and completion gates.

## 7. Mandatory Grok review protocol

Every implementation subplan performs exactly one review/fix cycle through the user-specified
`$grok-delegate` skill:

1. Implement the subplan on its dedicated topic branch, run focused tests and required quality
   checks, and commit the verified implementation checkpoint.
2. Record `git status --short --branch`, then invoke in the same branch/worktree:

   ```bash
   bash /Users/ruirui/.codex/skills/grok-delegate/scripts/run-grok.sh -- \
     "/review <complete subplan review request>"
   ```

   Use the skill default `grok-4.6` with `xhigh` effort unless the user explicitly changes it. Keep
   the process in the foreground and wait for the complete result; do not create a delegated
   worktree, branch, commit, or push.
3. Codex independently verifies every reported problem against code, tests, and the locked contract.
   Suggestions are adopted only when real and valuable; speculative or out-of-scope findings are
   recorded with a reason for rejection.
4. Apply confirmed fixes once and rerun the affected and subplan gates. Do **not** ask Grok for a
   second review of the review-fix.
5. Commit the review-fix/closeout, fast-forward merge to `main`, verify no topic commits are missing,
   and retire the clean branch before activating the next subplan.

After Subplans 56–61 are all merged, run one additional Grok `/review` over the complete integrated
diff and architecture. Independently adjudicate and fix confirmed issues once, rerun the complete
gate, and do not run a second final review.

## 8. Validation and acceptance

Each subplan runs its focused files plus:

```bash
uv run ruff format --check .
uv run ruff check .
uv run python -m compileall -q src tests
git diff --check
```

The final integrated offline gate is:

```bash
uv run pytest -m 'not live'
uv run ruff format --check .
uv run ruff check .
uv run python -m compileall -q src tests
uv run morrow --help
uv run morrow preferences --help
uv run morrow learning --help
uv run morrow memory --help
git diff --check
```

Required deterministic evidence includes:

- natural paraphrases without marker words can produce add/replace/remove proposals with a scripted
  semantic Reviewer;
- temporary, quoted, hypothetical, Assistant-only, tool/repository, secret, injection, and
  capability-grant cases produce zero Active writes;
- multi-operation same-scope Writer success is one YAML revision; any invalid operation yields zero
  YAML/decision changes;
- crash points before/after YAML apply and SQLite finalize recover exactly or stop at drift;
- duplicate enqueue, worker restart, expired lease, retry exhaustion, cancellation, and cross-
  workspace isolation are deterministic with injected clocks/futures and no wall-clock sleeps;
- global/workspace migration is idempotent, backed up, future-schema safe, and preserves legacy
  snapshot recovery;
- an accepted replace/remove is visible on the next AgentRun of the same restored Session while the
  current AgentRun remains frozen;
- Preference injection counts/revisions and Project Knowledge MemorySelection are separately
  observable.

## 9. Post-implementation simulated-user and live protocol

Only after all six implementation subplans, their one-time reviews, the integrated final review/fix,
and the complete offline gate pass:

1. Commit the verified closeout so `main` is clean except for explicitly preserved user files.
2. Use a fresh isolated state root and fixture workspace; do not mutate production user state.
3. Run 2–3 simple natural-language tasks, then more complex coding/multi-tool tasks while continuing
   the same Session. Exercise background completion, Inbox accept/edit/reject, multi-operation write,
   restart, TaskRun/Tool/Review/Memory persistence, doctor, backup, and restore.
4. With the already user-authorized compatible credential, run an opt-in real-Provider corpus in the
   isolated environment. Record model/config versions and sanitized outcomes only.
5. Measure Preference operation quality on 12 declared positive intents (including at least seven
   replace/remove target cases across add, paraphrase, replace, remove, multi-operation, and scope)
   and at least 8 negative/temporary/quoted/safety cases:
   - positive operation recall is at least 11/12 (no fractional rounding ambiguity);
   - proposed-operation precision ≥ 90%;
   - replace/remove target accuracy is at least 6/7;
   - safety-negative Active writes = 0;
   - after accepted changes, next-AgentRun behavioral adherence is at least 9/10 on ten declared
     probes.
6. Verify overwrite, disable, enable, and delete semantics; verify removed/deleted rules are absent
   from the next-AgentRun context and that a later explicit add can create a new independent rule.
7. Deliver a detailed chronological report with commands, bounded IDs/counts, expected/actual results,
   persistence evidence, quality numerator/denominator, UX findings, and root-cause analysis.

If the post-implementation report finds a real issue, Stage 5 acceptance remains open: write a
focused remediation subplan, fix it, run that subplan's one Grok review/fix cycle, and replay the
complex scenario. Do not hide a failing real-Provider result behind scripted tests.

## 10. Cross-cutting invariants

1. ConversationLog remains the only chat-history writer.
2. Main Agent completion is independent from Review success, timeout, retry, or cancellation.
3. Reviewer has no tools and Writer access; Writer has no model semantics.
4. No model call or terminal confirmation occurs inside a SQLite or YAML write transaction.
5. Every learned operation cites valid same-workspace user Evidence supplied to that Review.
6. YAML is the only runtime Active Preference authority; SQLite history is not merged into context.
7. Same command/job replay is idempotent; stale revisions and drift fail closed.
8. Workspace A cannot read or mutate Workspace B jobs, Evidence, proposals, batches, or entries.
9. Stored Preferences cannot grant capabilities or override higher-authority instructions.
10. Profile, Project Knowledge, MemorySelection, Task, Tool, permission, and public AgentEvent
    ownership remain unchanged unless a child plan explicitly names a compatibility edit.
11. No secrets, raw Provider output, reasoning, full tool payloads/results, tracebacks, or credentials
    enter YAML, events, reports, terminal output, or Reviewer context.
12. No new third-party dependency is added without explicit approval.

## 11. Non-goals

- A persistent daemon, cron scheduler, remote worker, or cross-device sync.
- Automatic Candidate activation without explicit user acceptance.
- Semantic vector deduplication, embeddings, a taxonomy, fixed Preference domains, or a keyword
  intent classifier.
- Using Project Knowledge MemorySelection as a second Preference store.
- Profile schema redesign, automatic Skill creation, Workflow activation, or orchestration policy
  learning.
- Rewriting immutable historical AgentRun snapshots or erasing SQLite audit history.
- Changing bundled capability policy defaults or the public runtime event lifecycle.

## 12. Completion condition

The implementation is complete when Subplans 56–61 and their one-time Grok review/fix cycles are
merged, the integrated Grok review/fix and full offline gate pass, public fixed-field Preference
interfaces are retired, migration/recovery evidence is complete, and the working tree is clean.

Stage 5 user acceptance is complete only after the post-implementation simple, complex, persistence,
and real-Provider evaluations also meet the declared quality/safety targets or all confirmed defects
have been remediated and replayed successfully.
