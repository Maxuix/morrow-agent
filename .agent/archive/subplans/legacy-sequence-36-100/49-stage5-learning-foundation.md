# Subplan 49 — Learning Domain and v10 Persistence

> Status: ready; implementation not started
> Branch: `feat/stage5-learning-foundation`
> Prerequisite: Subplan 48 complete on verified `main`
> Owns: Stage 5 governance decision, Core Learning contracts, v10 schema/adapters, LearningPolicy
> Does not own: Task acceptance hook, production Reviewer calls, candidate decisions, Active memory

## Objective

Create the smallest durable, typed foundation on which later Stage 5 slices can operate without
coupling Learning to TaskService, AgentLoop, Terminal, or YAML internals. At this gate Morrow can
store/query safe LearningPolicy, Review, Evidence, Candidate, and suppression records, but no
production path automatically creates a Review and no Candidate can become Active.

## Deliverables

- `docs/decisions/stage-5-learning-governance.md` with accepted authority, lifecycle, safety, and
  non-goal decisions.
- `src/morrow/core/learning.py` with bounded Pydantic v2 domain models and discriminated payloads.
- `src/morrow/core/learning_ports.py` with narrow journal and Reviewer contracts.
- v10 ordered/checksummed migration and the corresponding bounded SQLite repository.
- Workspace LearningPolicy command/query foundation using existing application receipts/events.
- Deterministic IDs, clocks, and Reviewer fixtures for later offline pipeline tests.
- Architecture tests that preserve the new ownership boundaries before behavior is connected.

## Locked Core vocabulary

### Policy

```text
LearningMode: off | review_only | explicit_auto(reserved, not selectable/persistable)

LearningPolicy
- workspace_id
- mode
- candidate_ttl_days                 bounded; default 30
- max_candidates_per_review          1..3; default 3
- max_evidence_per_review            bounded; default 32
- row_version
- created_at / updated_at
```

Absence of a row projects to immutable default `review_only` without performing a write. A first
policy command creates row version 1. Public validation accepts only `off` and `review_only`.

### Review

```text
LearningReview
- review_id (`lrv_...`)
- workspace_id / task_run_id / task_outcome_id
- review_version
- trigger: task_accepted | explicit_request
- status: pending | running | completed | failed | superseded
- policy_snapshot_json + policy_digest
- reviewer_provider_id / reviewer_model_id (nullable before claim)
- reviewer_prompt_version / reviewer_schema_version
- supersedes_review_id
- lease_id / lease_expires_at
- attempt_count / row_version
- created_at / started_at / completed_at
- failure_code
```

Review failure codes are stable bounded tokens such as `provider_unavailable`, `timeout`,
`invalid_output`, `safety_rejected`, `lease_lost`, and `internal`. They never contain tracebacks,
Provider payloads, prompts, or raw output.

### Evidence

```text
LearningEvidence
- evidence_id (`lev_...`)
- workspace_id / origin_review_id / task_run_id
- source_kind / source_id / source_pointer
- actor: user | assistant | tool | system
- authority
- explicitness: explicit | behavioral | inferred
- polarity: positive | negative | neutral
- scope_hint
- excerpt_redacted (optional and bounded)
- content_digest
- observed_at / created_at
```

Authority is a closed enum that distinguishes explicit persistent user intent, user correction,
user acceptance, configuration change, deterministic Task/Artifact fact, behavioral signal, and
untrusted external content. Source IDs are opaque references and never interpreted as authority.

### Candidate

Common envelope:

```text
LearningCandidate
- candidate_id (`lcn_...`)
- workspace_id / origin_review_id
- type / operation / semantic_key / scope
- typed proposal payload
- fingerprint
- status: proposed | promoting | accepted | edited_and_accepted | rejected | expired | superseded
- confidence_band + confidence_basis[]
- sensitivity
- expected_target_revision
- duplicate_of_id / supersedes_id / conflict_refs[]
- expires_at / row_version
- created_at / resolved_at
```

Discriminated proposal payloads are separate models:

- `PreferenceCandidatePayload`: Core-owned operation/path/value literals mirroring the allowed
  Preferences surface without importing `application.configuration`.
- `ProfileCandidatePayload`: Core-owned operation/path/value literals for the allowed Profile
  surface, workspace only.
- `ProjectKnowledgeCandidatePayload`: category, dotted semantic key, bounded statement, optional
  validity window.
- `SkillCandidatePayload`: title, problem pattern, bounded observed steps/tool names; never Skill
  bytes or executable content.
- `WorkflowFeedbackCandidatePayload`: bounded result/edit facts only.
- `OrchestrationPolicyCandidatePayload`: bounded proposed rule only, never Active policy.

No generic unvalidated `dict[str, Any]` crosses the application boundary. SQLite may serialize the
discriminated union as canonical JSON only after strict validation. Subplan 52 owns the explicit
mapping from these Core payloads to the existing application-layer `ConfigurationCommand`.

### Suppression and draft boundary

```text
LearningSuppression
- suppression_id (`lsp_...`)
- workspace_id / candidate_type / scope
- semantic_key and optional exact fingerprint
- source_candidate_id / reason / status
- expires_at / row_version / created_at / updated_at

CandidateDraftBatch
- drafts[]                          maximum 3

CandidateDraft
- candidate_type / operation / semantic_key / proposed_scope
- typed proposed payload
- evidence_ids[]                    must be a subset of LearningContext IDs
- temporary_or_durable classification
```

The Reviewer never supplies Candidate ID, status, confidence, sensitivity, fingerprint, conflict,
or resolution fields. Those remain application policy decisions.

## Bounds and normalization

Define constants in Core and enforce them at construction and row decode:

- Review policy snapshot ≤ 8 KiB.
- Evidence excerpt ≤ 512 normalized characters; no more than 32 Evidence records per Review.
- Candidate proposal ≤ 8 KiB; semantic key ≤ 128 characters; at most 16 evidence/conflict refs.
- Candidate batch ≤ 3.
- Suppression reason ≤ 256 characters.
- Canonical payloads use existing `canonical_json_bytes`, digest helpers, and secret refusal where
  applicable.
- Fingerprints hash candidate type, normalized scope, semantic key, operation, and canonical typed
  proposal. User-visible text normalization never changes commands/paths inside the stored proposal.
- Dotted semantic keys are bounded lowercase tokens; arbitrary paths, credentials, URLs containing
  credentials, and capability/approval mutations are invalid candidate keys/values.

Add a dedicated conservative Learning safety scanner for secret-shaped material, prohibited
personal categories, bidi/zero-width controls, capability authorization phrases, and prompt
injection markers. This scanner returns typed reason codes and redacted metadata; it never returns
the secret match in an exception or event.

## Ports

`LearningJournalPort` owns only v10 operations:

- effective/get/save LearningPolicy;
- create/get/list/update Review with expected row version and lease checks;
- put/get/list immutable Evidence, Review/Evidence links, and Candidate/Evidence links;
- put/get/list/update Candidate with expected row version;
- put/get/list/update suppression;
- transaction composition through the existing shared journal protocol.

`LearningReviewerPort` is asynchronous and narrow:

```python
async def review(
    context: LearningContext,
    *,
    model: ModelRef,
    timeout_seconds: float,
) -> CandidateDraftBatch: ...
```

The port exposes no tools, Session, ContextBuilder, journal, writer, or Provider-private response.
`FakeLearningReviewer` belongs in `morrow.testing` or test fixtures and is never selected by
production bootstrap.

Knowledge/selection port protocols may be declared now only where Core types require them; their
methods and adapters are implemented by their owning subplans rather than speculative stubs.

## v10 schema

Add these tables in one immutable migration:

1. `learning_policies`
   - primary key `workspace_id`, allowed persisted modes `off | review_only`, bounded numeric checks,
     row version and timestamps.
2. `learning_reviews`
   - unique `(workspace_id, task_outcome_id, review_version)`, self-FK for supersession, status/lease
     checks, bounded snapshot/failure fields, indexes by workspace/status/lease and Outcome.
3. `learning_evidence`
   - origin Review FK, workspace/task/source columns, digest/redacted excerpt budgets, unique source
     observation identity, indexes by source/authority.
4. `learning_review_evidence`
   - composite Review/Evidence link used to reuse an immutable source observation in a later Review;
     validates same workspace and preserves the Evidence's first-observed Review.
5. `learning_candidates`
   - FK to origin Review, canonical proposal JSON + byte count, fingerprint, status/version,
     conflict/duplicate/supersedes references, expiry indexes.
6. `learning_candidate_evidence`
   - composite primary key, FKs to Candidate/Evidence, and a trigger/application check that both
     records belong to the same workspace. Evidence keeps its owning Review, so later Reviews may
     add verified Evidence to an existing duplicate Candidate without rewriting its origin Review.
7. `learning_suppressions`
   - workspace/type/scope/key/fingerprint matching fields, status/version/expiry, unique active
     suppression identity.

Use a partial unique index to prevent two simultaneous `proposed | promoting` Candidates with the
same workspace fingerprint. Historical resolved Candidates remain immutable and queryable.

Update `SUPPORTED_SCHEMA_VERSION` and reserved versions only to 10 in this subplan. Add clean v9→v10
upgrade, v10 reopen, v11 future refusal, checksum mismatch, migration rollback, and backup/restore
coverage. Do not add empty v11/v12 migrations.

## Application policy surface

Introduce a focused `LearningPolicyService`, composed beside the existing operational API rather
than implemented in Terminal:

- `get_status()` returns effective mode, whether it is persisted, budgets, and row version.
- `set_mode(command)` validates workspace/expected row version/command ID.
- The policy row, `learning.policy_changed` event, and command receipt commit atomically.
- Replays return the existing policy result; conflicting command reuse fails.
- Events contain mode, budgets, and row version only—never candidate/evidence content.
- No Task hook or Review execution is added in this subplan.

## Tasks

### S49.1 Governance decision and architecture guards

- Write the accepted decision with the master plan's trigger, authority, policy, model, promotion,
  deletion, and freeze contracts.
- Extend architecture tests so Core imports no outer layer; TaskService and AgentLoop do not import
  Learning application modules; Reviewer adapters cannot import tool/Session/ContextBuilder; new
  Learning UI modules cannot import SQLite/YAML adapters; and, within the new Learning application
  package, only Promotion may depend on configuration mutation. Do not broaden existing unrelated
  CLI adapter imports as part of this rule.
- Update the Operational Store decision's schema reservation table only when v10 is implemented.

### S49.2 Core domain

- Implement enums, typed payloads, models, validators, canonical fingerprints, budgets, and stable
  errors.
- Add positive/negative model tests, strict-extra/type tests, payload-budget tests, identifier and
  source authorization tests, and secret/Unicode/prohibited-content tests.

### S49.3 Ports and deterministic fakes

- Define the minimum v10 journal surface and asynchronous Reviewer contract.
- Add Fixed Learning clock/ID helpers and scripted zero/one/many/raising Reviewer fixtures.
- Prove production composition has no Fake Reviewer.

### S49.4 v10 migration

- Add migration constants/statements/checksum registration and update supported/reserved version.
- Test fresh creation, v9 upgrade, rollback on each injected statement failure, foreign/future
  refusal, required constraints/indexes, and immutable older checksums.

### S49.5 SQLite adapter

- Add row codecs and repository methods over `SqliteJournalBackend`.
- Compose the repository into `SqliteOperationalJournal` without duplicating transaction state.
- Test same-workspace round trips, cross-workspace known-ID refusal, stale row versions, duplicate
  fingerprints, lease constraints, canonical JSON corruption, and outer-transaction rollback.

### S49.6 LearningPolicy application foundation

- Implement query/set-mode DTOs/service, events, receipts, replay, and error mapping.
- Compose it for headless and interactive products without adding UI commands yet.
- Test absent default, off/review_only transitions, explicit_auto rejection, stale/replay/conflict,
  read-only/future schema, and rollback of state/event/receipt.

### S49.7 Closeout

- Reconcile roadmap/decision/architecture now-statements.
- Run focused and full declared gates, commit, fast-forward to `main`, retire the branch, and update
  execution state so Subplan 50 is ready but not started.

## Focused validation

Planned new tests:

- `tests/test_stage5_learning_domain.py`
- `tests/test_stage5_learning_store.py`
- `tests/test_stage5_learning_policy.py`

Regression set:

```bash
uv run pytest -q \
  tests/test_stage5_learning_domain.py \
  tests/test_stage5_learning_store.py \
  tests/test_stage5_learning_policy.py \
  tests/test_operational_store.py \
  tests/test_stage4_journal.py \
  tests/test_stage4_application_api.py \
  tests/test_architecture_boundaries.py
uv run ruff format --check .
uv run ruff check .
uv run python -m compileall -q src tests
git diff --check
```

Run the full non-live suite before closing the schema-bearing subplan.

## Completion gate

Subplan 49 is complete only when a fresh or v9 store safely opens as v10; every v10 row round-trips
through typed models with workspace isolation and bounds; LearningPolicy defaults to `review_only`
and rejects `explicit_auto`; the Fake Reviewer is test-only; no production Task/Agent/UI path has
gained learning behavior; all gates pass; and verified commits are fast-forwarded to `main`.

## Out of scope

- Creating pending Reviews from Task acceptance.
- Claiming or running Reviews.
- Candidate accept/edit/reject UI.
- Project Knowledge or config promotion tables/logic.
- Memory selection/context integration.
- Production Provider/model calls.
