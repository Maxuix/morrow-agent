# Stage 5 Reviewable Learning and Long-Term Memory Plan

> Status: Subplan 55 complete; Live model-quality hold remains pending
> Active subplan: Subplan 55 — Stage 5 Simulated-User Remediation
> Baseline: `5cfb99f` on local `main`
> Target schema: Operational Store v9 → v10 → v11 → v12

## Objective

Build a governed, reviewable learning loop over the completed Stage 4 Task/Session/Outcome
foundation:

```text
accepted TaskOutcome
→ durable pending LearningReview
→ bounded Evidence and structured Candidate drafts
→ deterministic safety, scope, duplicate, conflict, and suppression checks
→ explicit user decision
→ one Promotion Service changes Active state
→ one frozen MemorySelection per new foreground AgentRun
→ ContextBuilder receives only selected, attributable long-term state
```

The stage succeeds when Morrow can safely propose and use long-term Preferences, Profile changes,
and Project Knowledge without giving the model direct write authority, inventing a second
configuration authority, or promising a hidden worker that does not exist.

## Authority

1. The current user request and later explicit decisions.
2. Current code and validation just run.
3. This master plan and the one active Stage 5 subplan when implementation is in progress.
4. `docs/roadmap/stage-5-reviewable-learning-and-memory.md` after its code-alignment corrections.
5. The two Stage 5 research discussions as design input, not a parallel implementation spec.

If code or validation contradicts this plan, stop the active task and update the stale plan or
roadmap before continuing.

## Current-code anchors

The plan starts from these verified implementation facts:

- `TaskRunStatus` is `open | ready_for_acceptance | accepted | cancelled | failed | abandoned`;
  there is no `completed` or `corrected` status.
- `TaskService.accept()` returns a `TaskCommandResult` that already carries the accepted
  `TaskOutcome`.
- `OperationalApplicationService._task_command()` owns the outer SQLite transaction, application
  event, and receipt; nested journal transactions join that outer transaction.
- The Operational Store supports schema v12 and the journal is partitioned into bounded repositories
  over one shared transaction backend; v12 adds MemorySelection/items and rebuildable lexical terms.
- `ConfigPatchService` exposes prepared, revision-protected Profile/Preferences YAML writes, and
  persistent Session projections/AgentRun snapshots retain their source revisions.
- `AgentRunSnapshot` freezes model, provider, Profile/Preferences, MemorySelection, source
  revisions, policy, tool, permission, and runtime identity; durable `ContextBuilder` consumes the
  verified RunContextProjection while process-local Sessions retain an explicit fallback.
- `complete_structured()` builds from the full structured Session context and therefore is not a
  valid Learning Reviewer boundary.
- `/accept` is already the alias for `/task accept`; the existing Terminal preview + `y/N` pattern
  is the correct interaction model for a separate `/learn accept <candidate-id>` flow.
- There is no background worker or scheduler. All Stage 5 review execution must be an explicit,
  observable foreground action until a later stage introduces durable background work.

## Locked product and architecture contracts

### 1. Trigger and Task lifecycle

- Automatic review eligibility starts only from an explicit transition to `accepted` that produced
  a `TaskOutcome`.
- `cancelled`, `failed`, and `abandoned` Outcomes are retained Stage 4 facts but do not automatically
  request learning.
- Do not add `completed` or `corrected` to `TaskRunStatus`.
- A follow-up User Turn after `ready_for_acceptance` continues to reopen the Task through the
  existing transition. The Review extractor may use that User Turn plus the transition as bounded
  correction evidence; Stage 5 does not invent a second Task lifecycle.
- The first release does not add a separate `TaskFeedback` table. It stores typed LearningEvidence
  that references the existing User Turn/Task transition. A future explicit feedback command may
  add another typed evidence source without changing Task status.
- An explicit `learning review <outcome-id>` command may request a new Review version. It cannot
  change the Task or Outcome.

### 2. Transaction and execution boundary

- The accepted Task, TaskOutcome, pending LearningReview, `task.accepted` event,
  `learning.review_requested` event, and command receipt commit in one outer SQLite transaction.
- No model call, terminal wait, YAML write, or other external operation occurs inside that
  transaction.
- Claim/lease, Reviewer execution, and Review finalization are three separate phases. Reviewer
  execution happens outside a write transaction.
- Interactive REPL acceptance may run one bounded Review immediately after commit, in the
  foreground. Headless Task acceptance only enqueues truthfully; a separate learning command runs
  it. There is no hidden future-processing promise.
- Review failure, cancellation, timeout, invalid output, or process death never rolls back or
  changes the accepted TaskOutcome.

### 3. Model authority

- The Reviewer can only return strict `CandidateDraft` objects referencing Evidence IDs supplied in
  its bounded `LearningContext`.
- The Reviewer has no tools, no whole ConversationLog, no CredentialStore, no raw Artifact bytes,
  and no direct journal/configuration writer.
- Model confidence is ignored. `confidence_band` and its basis are computed by deterministic policy.
- Raw model output and provider-private reasoning are never persisted.
- Zero candidates is a successful Review result. The hard limit is three candidates per Review.

### 4. Active-state authority

```text
YAML authority
  - global Preferences
  - workspace Preferences
  - workspace Profile

SQLite authority
  - LearningPolicy and immutable Review/Evidence/Candidate/decision history
  - suppressions and promotion recovery operations
  - Project Knowledge heads and immutable revisions
  - configuration activation provenance
  - MemorySelection audit records and rebuildable lexical terms
```

- SQLite never becomes a second runtime value authority for Profile or Preferences.
- Project Knowledge has one authority: SQLite. It is not copied into YAML.
- Search terms/indexes are rebuildable projections, never authorities.
- Only `LearningPromotionService` can convert a Candidate into Active state.

### 5. First-release LearningPolicy

- LearningPolicy is workspace-scoped SQLite state.
- If no row exists, the effective mode is `review_only`.
- The only user-selectable modes in Stage 5 are `off` and `review_only`.
- `explicit_auto` may remain a reserved domain token for forward compatibility, but it is rejected
  by the public command and cannot be persisted or activated in this stage.
- Policy changes use expected row version, command ID, sanitized application events, and receipts.
- Candidate TTL and per-Review budgets are policy fields with bounded defaults; expiry is applied
  lazily by query/command transactions or an explicit foreground maintenance command, never a
  scheduler.

### 6. Classification and scope

| Type | Boundary | Stage 5 acceptance result |
|---|---|---|
| Preference | How Morrow collaborates: language, response detail, durable interaction instruction | Config promotion through `ConfigPatchService` |
| Profile | Explicit stable user/project identity fields already supported by Profile | Config promotion through `ConfigPatchService` |
| Project Knowledge | Project fact, decision, command, convention, or constraint | Active immutable SQLite revision |
| SkillCandidate | Reusable single-Agent procedure signal | Accepted candidate only; no Skill file or activation |
| WorkflowFeedback | Evidence about a Workflow edit/result | Accepted feedback only; no orchestration change |
| OrchestrationPolicyCandidate | Proposed Task→Workflow rule | Candidate only; Stage 8 owns activation |

- “This project uses `uv run pytest`” is Project Knowledge (`convention/testing.command`), not a
  catch-all Preference instruction.
- Reviewer-created persistent candidates default to workspace scope. A global Preference scope is
  available only after the user explicitly edits/selects it in the promotion preview.
- Profile and Project Knowledge remain workspace-scoped.
- Session-only or one-shot instructions produce Evidence or no result, not long-term candidates.

### 7. Evidence and safety

- Evidence records actor (`user | assistant | tool | system`), authority, explicitness, polarity,
  source reference/pointer, bounded redacted excerpt, and content digest.
- Assistant output, repository text, web/tool output, or Skill instructions cannot alone support an
  Active Preference/Profile and cannot grant a capability.
- Deterministic accepted Task/Artifact facts may support Project Knowledge only under the defined
  eligibility matrix.
- Secrets, credential-shaped data, prohibited personal data, capability authorization, prompt
  injection text, and hidden Unicode controls fail closed.
- When raw content is prohibited, persistence is limited to a digest, source reference, and bounded
  rejection reason; secret material does not enter candidates, events, logs, YAML, or model context.

### 8. Candidate decisions

- `/accept` and `/task accept` accept only a Task result.
- `/learn accept <candidate-id>` accepts only one identified LearningCandidate.
- Accept, edit-and-accept, reject, and reject-with-suppression require workspace ID, candidate ID,
  expected row version, and command ID.
- UI adapters show type, scope, evidence, current target, conflicts, and final diff before `y/N`.
- The original Candidate proposal is immutable. Edit-and-accept stores a separate immutable
  decision/final value and preserves the original-versus-final diff.
- Reject and “never suggest again” are separate decisions. The latter creates a first-class
  suppression.
- Natural-language acceptance is deferred. No interpretation of “好的/可以/都行” can write memory.

### 9. Project Knowledge lifecycle

- Knowledge uses a stable head plus immutable revisions and a workspace-global monotonic memory
  revision.
- `active` records are selectable. `disabled`, `disputed`, and `deleted` records are excluded.
- Disable is reversible and retains history. Delete is a logical tombstone and retains immutable
  audit/revision rows. Physical purge/secure erasure belongs to Stage 10 and is not claimed here.
- Replacing a semantic key creates a new revision and `supersedes` link; it never overwrites an old
  revision in place.

### 10. Configuration Promotion Saga

- `ConfigPatchService` gains public `prepare()` and `apply_prepared()` contracts with expected
  revision plus before/after digests.
- A SQLite `PromotionOperation` is durable before YAML is attempted.
- If YAML is still at the exact before revision/digest, retry is safe. If it is at the exact expected
  applied revision and after digest, SQLite may finalize. The same value at any later revision is
  still drift. Any other revision/digest becomes `needs_resolution` and is never overwritten.
- Finalization atomically stores the Candidate decision, activation provenance, events, and command
  receipt in SQLite.
- Undo is another explicit previewed configuration operation and is allowed only when current YAML
  still matches the activation being reversed.

### 11. Memory selection and runtime freeze

- The first implementation selects Project Knowledge; existing small Profile/Preferences remain a
  compatibility baseline frozen into the AgentRun snapshot.
- Selection uses hard workspace/status filters, semantic/category match, deterministic lexical
  overlap, stable tie-breaking, category diversity, item count, and rendered-character budget.
- Tokenization supports dotted/snake/camel identifiers, commands/paths, Latin words, and CJK
  bigrams without an embedding service or new dependency.
- Every new foreground AgentRun records `memory_selection_id`, digest, and workspace memory revision
  in `AgentRunSnapshot`.
- A recovery AgentRun in the same Turn reuses the interrupted Run's exact selection. A new User Turn
  creates a new selection.
- `ContextBuilder` consumes the frozen run projection. Config or Knowledge changes made during a Run
  become visible only to a later new AgentRun.
- Missing/mismatched selection or immutable Knowledge revision fails closed; recovery never silently
  substitutes current memory.

### 12. Events, ownership, and layering

- Learning facts use the existing sanitized `ApplicationEvent` stream. Stage 5 does not change the
  public runtime `AgentEvent` lifecycle.
- `AgentLoop` consumes a frozen context only. It does not create Reviews/Candidates, query memory,
  or promote state.
- `TaskService` remains unaware of Learning. The application acceptance coordinator owns the hook.
- Terminal/Typer code only parses, renders, confirms, and calls application services. It never
  writes SQL or YAML directly.
- `ConversationLog` remains the only chat-history writer.

## Target code modules

| Boundary | Planned modules | Responsibility |
|---|---|---|
| Learning domain | `src/morrow/core/learning.py` | Review, Evidence, Candidate union, policy, suppression, decisions, budgets |
| Memory domain | `src/morrow/core/memory.py` | Knowledge head/revision, query, selection, reason codes |
| Ports | `src/morrow/core/learning_ports.py` | Learning/Knowledge/Selection journal and Reviewer contracts |
| Learning application | `src/morrow/application/learning/` | trigger, context, extraction, validation, coordination, promotion, recovery, queries/commands |
| Memory application | `src/morrow/application/memory/` | knowledge lifecycle, lexical selection, frozen projection |
| SQLite adapters | `src/morrow/adapters/state/learning_journal.py`, `knowledge_journal.py`, `memory_selection_journal.py` | bounded codecs and repositories over the shared backend |
| Reviewer adapter | `src/morrow/adapters/models/learning_reviewer.py` | no-tool, bounded structured completion from explicit LearningContext |
| Composition | `src/morrow/bootstrap.py` | shared headless/interactive service construction and injection |
| UI adapters | `src/morrow/application/commands.py`, `src/morrow/interfaces/terminal.py`, focused CLI registration module | `/learn`, `/memory`, preview/confirm, top-level commands |

Package/file names may be adjusted once imports are exercised, but ownership may not collapse back
into `AgentLoop`, `TaskService`, Terminal, or one new God Class.

## Schema reservation and ownership

| Version | Owning subplan | New authority |
|---:|---|---|
| 10 | 49 | LearningPolicy, Review, Evidence, Candidate links, suppression |
| 11 | 51 | Candidate decisions, PromotionOperation/config activation provenance, Project Knowledge versions, workspace memory revision |
| 12 | 53 | MemorySelection/items and rebuildable lexical token projection |

Each migration is ordered and checksummed, preserves v1–v9 fixtures, adds row-level bounds and
workspace/FK/index constraints, and updates supported/reserved versions only in its owning
subplan. A later subplan cannot edit an already-committed migration checksum.

## Subplan route

1. **Subplan 49 — Learning domain and v10 persistence**
   Lock the Stage 5 governance decision, domain models, safety budgets, Reviewer/journal ports,
   workspace LearningPolicy, v10 migration, adapters, and deterministic fakes. No Task hook or
   production model call yet.
2. **Subplan 50 — Accepted Outcome to Candidate pipeline**
   Add the atomic acceptance hook, explicit re-review, claim/lease/retry, bounded Evidence and
   LearningContext, validation/dedup/conflict/suppression pipeline, foreground runner, and Fake
   Reviewer integration.
3. **Subplan 51 — Inbox, decisions, and Project Knowledge**
   Add v11, command/query DTOs, preview-confirm UI, accept/edit/reject/suppress/expiry, Project
   Knowledge lifecycle, non-activating future candidate decisions, and CLI/REPL inbox surfaces.
4. **Subplan 52 — Profile/Preferences Promotion Saga**
   Publish prepared configuration contracts, implement crash-recoverable YAML promotion and undo,
   maintain Session revision projections, and route Preference/Profile candidates through the one
   Promotion Service.
5. **Subplan 53 — MemorySelection and ContextBuilder integration**
   Add v12, deterministic lexical selection and audit reasons, AgentRun snapshot/freeze/resume
   behavior, context projection, inspection commands, and cross-workspace/budget tests.
6. **Subplan 54 — Production Reviewer, safety evaluation, and Stage 5 acceptance**
   Add the no-tool production Reviewer adapter, finish UX/policy controls, adversarial and quality
   datasets, doctor/backup/docs/acceptance evidence, real multi-task trial hold point, and full
   offline/quality closeout.
7. **Subplan 55 — Simulated-user remediation**
   Repair the typed Candidate decision CLI, truthful reject previews, and Project Knowledge
   timestamp precision exposed by the isolated simulated-user evaluation; rerun that user flow and
   reconcile Stage 5 acceptance evidence before restoring the user-ready claim.

Subplans are sequential. Do not implement later-slice production logic early. The only intentional
forward reservation is the complete v11 table set needed so its checksum never changes while
Subplan 52 begins using the already-reserved Saga rows.

## Cross-cutting invariants

1. No Active state exists without an explicit configuration command or an accepted Candidate.
2. Every Candidate Evidence ID exists, is workspace-authorized, and was present in the Review input
   that first attached that Evidence; later Reviews may add new verified Evidence to an existing
   duplicate Candidate without changing its original proposal.
3. Same command ID + same request replays; same ID + different request conflicts.
4. Same accepted Outcome/review version never creates duplicate proposed Candidates.
5. Provider/model failure never changes Task acceptance.
6. No write transaction spans a model call, terminal confirmation, or YAML operation.
7. YAML success before SQLite finalize is recoverable; YAML drift is never overwritten.
8. Assistant/tool/repository content cannot alone activate a Preference/Profile or capability.
9. Prohibited content is absent from model context, events, logs, YAML, and stored candidate values.
10. Workspace A Reviews, Candidates, Knowledge, suppressions, and selections are inaccessible from
    Workspace B even when IDs are known.
11. Disable, logical delete, rejection, suppression, supersession, and physical purge remain
    distinct operations.
12. Every selection explains selected items, reason codes, deterministic order, and omitted count.
13. The same AgentRun uses one frozen selection across every model/tool cycle.
14. Existing Stage 4 ConversationLog, runtime-event, Task, recovery, permission, and transaction
    ownership remains intact.

## Validation strategy

Every logical task runs its touched test files and touched-file Ruff checks. Every subplan closes
with its declared focused regression set, Ruff format/check, compileall, CLI help for new surfaces,
and `git diff --check`.

The final Stage 5 gate is:

```bash
uv run pytest -m 'not live'
uv run ruff format --check .
uv run ruff check .
uv run python -m compileall -q src tests
uv run morrow --help
uv run morrow learning --help
uv run morrow memory --help
git diff --check
```

Required acceptance evidence includes:

- positive/negative classification fixtures for durable, temporary, quoted, hypothetical,
  negated, Assistant-authored, tool/repository, correction, and injection cases;
- zero Active writes for every safety-negative case, even when a scripted Reviewer proposes one;
- crash injection before/after Review claim/finalize and before/after YAML apply/finalize;
- replay, stale version, duplicate, suppression, expiry, workspace isolation, and selection-budget
  proofs;
- fresh-store creation plus v9→v10→v11→v12 upgrade and future-schema refusal;
- restart/restore using exact AgentRun selection and config revisions;
- one end-to-end REPL and one headless CLI acceptance flow.

Live Provider evaluation is `@pytest.mark.live`, opt-in, and requires explicit user authorization
and a compatible credential. It is never part of the default gate. Subplan 54 records a hold point:
without live authorization, scripted/offline acceptance may finish but the roadmap must truthfully
record live evaluation as pending unless the user explicitly accepts equivalent evidence.

## Completion and Git discipline

For each subplan:

1. Create its listed `feat/...` branch from the latest verified `main`.
2. Activate only that subplan and one TODO task at a time.
3. Commit small, coherent, verified progress with conventional commits.
4. Update plan/TODO/tracker/log and current architecture/roadmap only when truth changes.
5. Run the subplan gate, commit the closeout, fast-forward merge into `main`, verify the branch has
   no commits absent from `main`, then delete it.
6. Activate the next subplan only after the previous gate passes.

Subplan 54's automated offline gate is complete, but the later simulated-user evaluation at
`5cfb99f` confirmed two P1 defects and one P2 preview defect. Stage 5 user acceptance is therefore
reopened. It may be restored only after proposed Subplan 55 passes, its isolated simulated-user flow
is rerun, all verified work is on `main`, and the roadmap/acceptance evidence matches the observed
product behavior. The optional Live model-quality hold remains pending explicit authorization and a
compatible credential.

## Non-goals

- Background workers, schedulers, notification delivery, or automatic retry loops.
- Broad automatic/`explicit_auto` activation.
- Natural-language candidate acceptance.
- Embeddings, vector databases, knowledge graphs, external memory services, or a new dependency.
- Automatic Skill creation/modification/activation or Workflow/orchestration policy changes.
- Full conversation search or treating TaskOutcome history as long-term memory.
- Team/global Project Knowledge sharing, cross-device sync, or remote API transport.
- Physical purge, secure erasure, backup redaction, or complete export/deletion guarantees.
- Changes to bundled `agent-policy.toml` defaults or the public runtime event lifecycle.
