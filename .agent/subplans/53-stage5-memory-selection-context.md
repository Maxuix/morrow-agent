# Subplan 53 — MemorySelection and ContextBuilder Integration

> Status: planned
> Branch: `feat/stage5-memory-selection`
> Prerequisite: Subplan 52 complete and merged into verified `main`
> Owns: v12 selection/token projection, deterministic selector, AgentRun freeze/resume, context use
> Does not own: production Reviewer, embeddings, Task/conversation history search

## Objective

Make Active Project Knowledge useful without injecting all memory into every prompt. Each new
foreground AgentRun must receive one deterministic, explainable, workspace-isolated selection that
is frozen with its resolved Profile/Preferences baseline and reused throughout the Run.

## Domain models

### Query

```text
MemoryQuery
- workspace_id
- task_run_id / turn_id
- task_goal
- requested_categories[]
- explicit_semantic_keys[]
- agent_role (reserved/default foreground)
- max_items
- max_rendered_chars
```

The query is built from the current accepted User input/Task goal and fixed runtime metadata. It
does not contain full conversation history, raw Tool output, or credentials.

### Selection

```text
MemorySelection
- selection_id (`msel_...`)
- workspace_id
- query_digest
- source_memory_revision
- selected_items[]
- omitted_count
- rendered_chars
- selection_digest
- created_at

MemorySelectionItem
- record_kind: project_knowledge
- record_id / record_revision_id / revision
- rank / score_band
- reason_codes[]
- estimated_chars
- rendered_content_digest
```

Reason codes are a closed enum such as `explicit_key`, `category`, `identifier_overlap`,
`command_path_overlap`, `lexical_overlap`, `recent_confirmation`, `category_diversity`, and
`budget_omitted`. Raw internal numeric weighting need not be a public compatibility contract, but
ordering must be deterministic for a fixed input/state.

### Frozen run projection

Extend `AgentRunSnapshot` with:

```text
memory_selection_id
memory_selection_digest
memory_snapshot_revision
```

Add an in-process `RunContextProjection` owned by the turn lifecycle/memory projection layer:

- exact persisted AgentRunSnapshot;
- exact selected immutable Project Knowledge revisions;
- canonical rendered memory block/digest.

Session may retain this current projection for ContextBuilder, but it is not a second authority and
is rebuilt from the durable AgentRun/selection/revision records on restore.

## v12 schema

### `memory_selections`

- selection ID, workspace, query digest, memory revision, item/omitted counts, rendered chars,
  selection digest, created timestamp.
- A selection is immutable and may be referenced by more than one recovery AgentRun in the same
  Turn.

### `memory_selection_items`

- selection ID, ordinal/rank, record kind/ID/revision ID/revision, reason JSON + byte count,
  estimated chars, rendered content digest.
- composite primary key `(selection_id, ordinal)` and uniqueness of one record revision per
  selection.
- FKs to immutable Knowledge revision where practical without creating cross-workspace ambiguity.

### `memory_search_terms`

- workspace ID, knowledge revision ID, token kind/token, weight band.
- derived solely from Active Knowledge revision semantic key/category/statement.
- rebuildable and never consulted as authority without rechecking the head/revision/status.
- unique term rows and indexes by workspace/token/revision.

Do not depend on optional FTS5. Use a portable normalized token table and deterministic
application-layer scoring. Update supported/reserved schema to v12; preserve v1–v11 checksums and
upgrade fixtures.

## Tokenization and candidate retrieval

Implement a pure deterministic tokenizer:

- casefold Latin words;
- split dotted keys, snake_case, kebab-case, slash paths, and shell-like command tokens;
- split camelCase/PascalCase identifiers while retaining the original normalized identifier;
- retain bounded version/number tokens only when meaningful;
- produce CJK bigrams for contiguous Chinese/Japanese/Korean text;
- remove a small fixed stop-token set and enforce per-record/query token budgets;
- never execute, import, resolve, or normalize a token as a filesystem/network target.

Terms are regenerated transactionally on Knowledge activation/revision/enable and removed or
ignored on disable/delete/dispute. Doctor can rebuild/compare them because revisions are authority.

## Selection algorithm

Apply the following order:

1. Hard filter workspace, active head, current immutable revision, validity window, and safety.
2. Hard include valid explicit semantic-key matches, subject to total budget.
3. Compute requested category match and token overlap bands.
4. Add identifier/command/path overlap reason codes.
5. Add bounded last-confirmed/stability signal; recency alone can never select an unrelated record.
6. Sort by deterministic score tuple, then semantic key, revision, and opaque ID.
7. Apply category diversity without displacing explicit-key matches.
8. Render items in rank order until item/character budget; record omitted count/reasons.
9. Canonically digest the query, item refs/reasons, memory revision, and rendered content digests.

Defaults are bounded and policy/test configurable, for example at most 12 Knowledge items and 6 KiB
of canonical rendered content. Use rendered characters as the enforceable local budget; do not claim
Provider-token exactness.

Zero-item selections are persisted and valid. They prove that memory was considered at a particular
workspace memory revision.

## AgentRun creation and recovery

Integrate at `TurnSubmissionCoordinator`/run admission, not in AgentLoop:

### New User Turn / new foreground AgentRun

Inside the existing short outer admission transaction:

1. Resolve current effective Profile/Preferences and source revisions.
2. Build MemoryQuery from current Task/User input.
3. Read active Knowledge and build selection object through `MemorySelector` without external I/O.
4. Build AgentRunSnapshot containing selection ID/digest/memory revision and effective
   Profile/Preferences.
5. Persist AgentRun, immutable selection/items, Turn/User records, and receipt in the same outer
   transaction in an FK-safe order.
6. Install `RunContextProjection` in Session only after commit.

If any selection/snapshot write fails, Turn admission rolls back and no UserMessage is published.

### Recovery AgentRun in the same Turn

- Load the interrupted AgentRun's selection ID and exact immutable revisions.
- Reuse that selection ID/digest/memory revision in the new recovery AgentRun snapshot.
- Do not re-run selection against newer Active memory.
- If selection/revision/digest is missing or corrupt, fail closed into the existing recovery/health
  path; never silently substitute current memory.

### New Turn after state changes

A new Turn creates a new selection. A config/knowledge promotion during an existing Run does not
change later model cycles of that same Run.

## Frozen Profile/Preferences baseline

Correct `build_agent_run_snapshot()` so its `preferences` field contains the effective merge of
global, workspace, and session layers at admission, not only the session-local layer. Record all
relevant source revisions/digests supplied by Subplan 52.

For Stage 5:

- language and response detail remain small always-present baseline fields;
- existing bounded instructions remain in the frozen compatibility baseline to preserve current
  behavior;
- Project Knowledge is selected;
- a future richer per-record Preference authority may select instructions individually, but Stage 5
  does not create a second SQLite Preference value store just to do so.

## ContextBuilder integration

`ContextBuilder` reads the current `RunContextProjection` for durable AgentRuns and falls back to
current Session state only for the explicit process-local/test path.

Recommended message order:

```text
fixed safety boundary
task contract
relevant user state (frozen Profile + effective Preferences + selected Project Knowledge)
future Skills
Artifact/context projection
ConversationLog projection
```

Project Knowledge is rendered as canonical bounded JSON with record/revision/category/key/statement
and is explicitly labeled untrusted user/project state. It cannot grant tools, change approval,
replace the system identity, or override safety policy.

ContextBuilder must not query the Learning/Knowledge journal directly. The memory projection layer
loads/verifies selection records and supplies a bounded projection. AgentLoop remains unchanged
except consuming ContextBuilder as it already does.

## Inspection surface

Add authorized queries/commands:

```text
morrow memory selection list
morrow memory selection show <selection-id>
/memory selection [show <selection-id>]
```

Show AgentRun reference(s), source memory revision, selected record/revision, reason codes,
rendered-character totals, omitted count, and digest. Do not expose hidden scoring prompts or
unbounded content by default.

Extend doctor to verify:

- selection/item counts/order/digests;
- referenced Knowledge revisions exist and share workspace;
- AgentRun snapshot selection ID/digest/revision match;
- derived term rows reference current immutable revisions and are rebuildable;
- no disabled/deleted/disputed Knowledge appears in new selections.

Backup already copies SQLite; restore verification must exercise selection and Knowledge references.

## Tasks

### S53.1 v12 models, ports, and migration

- Implement MemoryQuery/Selection/Item/reason models and journal methods.
- Add v12 migration/repository/codecs plus v11 upgrade/future/corruption/rollback tests.

### S53.2 Token projection

- Implement pure tokenizer, derived term maintenance/rebuild, bounded candidate retrieval, and mixed
  Chinese/English/code/command/path fixtures.

### S53.3 Deterministic selector

- Implement scope/status/validity filters, scoring tuple, diversity, budgets, reasons, omitted
  counts, canonical digests, and stable tie-break tests.

### S53.4 AgentRun freeze and recovery reuse

- Integrate selection into admission transaction and AgentRunSnapshot.
- Add effective configuration snapshot/source revisions and post-commit Session projection.
- Reuse exact selection on recovery and fail closed on missing/mismatch.

### S53.5 Context projection

- Render selected Knowledge at the correct trust level and make ContextBuilder consume frozen state
  for every model/tool cycle.
- Prove same-Run state changes do not leak and next-Run changes do appear.

### S53.6 Inspection, doctor, backup, and docs

- Add selection query/CLI/REPL inspection, doctor invariants, backup/restore fixture, architecture and
  user documentation.

### S53.7 Closeout

- Run focused/full gates, merge verified work, and prepare Subplan 54.

## Fault and regression matrix

- v11→v12 migration rollback/checksum/future refusal;
- tokenizer Unicode/control/oversize input, deterministic output, and no optional extension;
- no match, explicit key, category only, mixed-language, tie, diversity, max items, exact char budget,
  invalid/expired/disputed/disabled/deleted records;
- cross-workspace record/search-term/selection ID attacks;
- memory changes racing admission: one transaction yields one coherent source revision/digest;
- failure after Turn/AgentRun/selection/item writes rolls back all admission state;
- recovery after newer Knowledge/config activation reuses old selection and source revisions;
- missing item/revision/digest mismatch causes truthful recovery/doctor failure;
- config/knowledge update between model tool cycles does not mutate same-Run ContextBuilder output;
- process-local Session remains usable with explicit fallback;
- public runtime event schema/cardinality and ConversationLog ownership remain unchanged.

## Planned tests and gate

New tests:

- `tests/test_stage5_memory_selection.py`
- `tests/test_stage5_memory_context.py`

Regression set includes ContextBuilder/runtime/projection, turn lifecycle, durable conversation,
recovery crash, AgentRun snapshot, Knowledge/application, operational store/doctor/backup, terminal,
CLI, and architecture tests. Finish with the full non-live and all quality/CLI gates.

## Completion gate

Every new durable foreground AgentRun has a persisted, explainable, bounded selection at one
workspace memory revision; every recovery Run reuses the exact selection; ContextBuilder uses the
frozen Profile/Preferences/Knowledge projection throughout the Run; unrelated/cross-workspace/
inactive memory never enters context; no embeddings or background work exist; and verified work is
merged into `main`.

## Out of scope

- Embeddings, FTS5 requirement, vector store, external memory service.
- Conversation/Task history search.
- Selecting individual YAML Preference records beyond the frozen compatibility baseline.
- Skill content selection.
- Production Reviewer/evaluation (Subplan 54).
