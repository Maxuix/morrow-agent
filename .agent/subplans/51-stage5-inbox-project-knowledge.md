# Subplan 51 — Learning Inbox, Decisions, and Project Knowledge

> Status: complete
> Branch: `feat/stage5-inbox-knowledge`
> Prerequisite: Subplan 50 complete and merged into verified `main`
> Owns: v11, candidate decisions/UI, suppressions/expiry, Project Knowledge lifecycle
> Does not own: YAML Profile/Preferences promotion, MemorySelection, production Reviewer

## Objective

Give users a complete reviewable Inbox and make Project Knowledge the first Active long-term memory
type. All read/write clients use the same typed application services, and every accepted/edited/
rejected/suppressed decision is replay-safe, workspace-isolated, and auditable. Preference/Profile
candidates are visible but remain non-promotable until Subplan 52.

## v11 schema

Create one immutable migration containing all Active-state/provenance tables needed by Subplans 51
and 52 so the migration checksum is not edited later.

### `learning_candidate_decisions`

Immutable decision rows:

```text
decision_id (`lcd_...`) primary key
workspace_id / candidate_id
kind: accept | edit_and_accept | reject | reject_and_suppress | expire | supersede
actor: user | policy
original_proposal_digest
final_proposal_json / final_proposal_bytes (nullable except edited/accepted types)
scope / conflict_resolution
command_id
created_at
```

The Candidate row holds current status/version; the immutable decision preserves what the user
actually accepted. Original Candidate proposal JSON is never overwritten.

### Project Knowledge tables

```text
project_knowledge_heads
- knowledge_id (`knw_...`) primary key
- workspace_id / semantic_key
- category
- status: active | disabled | disputed | deleted
- current_revision_id / row_version
- created_at / updated_at
- unique(workspace_id, semantic_key)

project_knowledge_revisions
- knowledge_revision_id (`krv_...`) primary key
- knowledge_id / workspace_id / revision
- statement / statement_digest
- source_candidate_id / source_decision_id
- supersedes_revision_id
- sensitivity / valid_from / valid_until
- created_at / last_confirmed_at
- unique(knowledge_id, revision)

project_knowledge_evidence
- knowledge_revision_id / evidence_id
- composite primary key and same-workspace validation

memory_workspace_state
- workspace_id primary key
- memory_revision, row_version, updated_at
```

Every activation/disable/enable/delete/supersede increments `memory_revision` in the same SQLite
transaction. Immutable revisions are never updated except a narrowly justified confirmation
timestamp; prefer a new confirmation/provenance row if mutation would weaken history.

### Configuration Saga reservation

Reserve the SQL columns/constraints for these v11 tables, but Subplan 52 owns their Python domain
models, codecs, repository methods, and behavior:

```text
promotion_operations
- operation_id / command_id / request_digest / workspace_id / candidate_id
- candidate_row_version / target / scope / path
- prepared_change_json + digest
- state: prepared | finalized | aborted | needs_resolution
- before_revision/digest / after_digest / applied_revision
- row_version / failure_code / timestamps

configuration_activations
- activation_id / workspace_id / candidate_id / decision_id / operation_id
- target / scope / path / operation
- applied_revision / before_digest / after_digest / value_digest
- bounded inverse_command_json / inverse_command_digest
- supersedes_activation_id / reverses_activation_id / status / timestamps
```

No production code reads or writes these reserved Saga tables in Subplan 51; migration constraint
tests inspect them directly. Project Knowledge promotion is fully SQLite and does not create a fake
YAML Saga.

Update supported/reserved schema to v11, preserving v1–v10 migration checksums and fixtures.

## Application API

Create a focused `LearningApplicationService` and `MemoryApplicationService` composed by the shared
operational service factory. The existing `OperationalApplicationService` may expose thin delegates
or a typed child service, but it must not absorb all implementation methods.

### Queries

- `learning_status()` — policy, pending/running/failed/proposed counts.
- `list_candidates(status, type, cursor, limit)` — stable bounded page.
- `get_candidate(candidate_id)` — candidate, bounded Evidence summaries, conflicts, duplicate and
  suppression state, current target summary.
- `preview_candidate_decision(candidate_id, edit?, scope?, conflict_resolution?)` — pure/read-only
  typed preview with expected row version and before/after representation.
- `list_reviews()` / `get_review()` — status, versions, attempt metadata, counts, never raw prompts.
- `list_knowledge(status/category/cursor/limit)`.
- `get_knowledge(knowledge_id, revision?)` — head, immutable revision, evidence/provenance timeline.

Every query validates workspace ownership even when a globally unique ID is supplied. Cursor/order
contracts are deterministic and bounded.

### Candidate commands

Typed commands carry `workspace_id`, `candidate_id`, `expected_row_version`, and `command_id`:

- `AcceptLearningCandidateCommand`
- `EditAndAcceptLearningCandidateCommand`
- `RejectLearningCandidateCommand(never_suggest: bool)`
- `ExpireLearningCandidatesCommand(cutoff, limit)` for explicit foreground maintenance

Rules:

- Only `proposed` Candidates accept/reject/expire. A `promoting` Candidate is owned by its Promotion
  operation and cannot be concurrently resolved.
- Same command/request replays the prior decision and Active result. Different request conflicts.
- Stale expected version fails before any decision/event/receipt write.
- Edit-and-accept revalidates the final typed payload, scope, safety, fingerprint, duplicate, and
  conflict against current state. It does not mutate the original Candidate.
- Reject does not create a suppression. `never_suggest` atomically creates an exact/key suppression
  with the rejection decision.
- Expiry is deterministic/lazy: Inbox and mutation paths first expire due rows in a bounded
  transaction; an explicit maintenance command handles larger batches. No scheduled expiry.

## Project Knowledge promotion

`LearningPromotionService` is introduced as the only Candidate→Active dispatcher.

For `ProjectKnowledgeCandidate`:

1. Re-read Candidate and expected row version inside one outer SQLite transaction.
2. Revalidate Evidence workspace, safety, expiry, suppression, and current semantic-key head.
3. Apply the user's explicit conflict resolution from the preview.
4. Write immutable decision.
5. Create/update the Knowledge head and immutable revision; attach Evidence.
6. Increment workspace memory revision.
7. Transition Candidate to accepted/edited-and-accepted.
8. Write sanitized events and command receipt.
9. Commit atomically.

Conflict behavior:

| Current state | Default accept |
|---|---|
| No head | create revision 1 |
| Active same normalized statement | accept Candidate as confirmation, attach provenance without duplicate head |
| Same semantic key, different statement | refuse until user chooses replace/edited merge |
| Disabled head | preview re-enable + new revision; require explicit confirmation |
| Disputed/deleted head | refuse ordinary accept; require an explicit type-specific resolution |

Replace creates revision N+1 and a supersedes link. No SQL update rewrites statement text in an old
revision.

For other candidate types in this subplan:

- Preference/Profile: preview and query work, but accept returns stable `unavailable` until Subplan
  52; never write YAML directly as a shortcut.
- SkillCandidate, WorkflowFeedback, OrchestrationPolicyCandidate: an explicit user acceptance writes
  a decision and accepted Candidate status only. The result says “accepted as candidate/feedback;
  not created or activated.” No Skill/Workflow/policy files or runtime state change.

## Knowledge management commands

All commands use expected row version, command ID, event, and receipt:

- `disable_knowledge` — active→disabled, reversible, increments memory revision.
- `enable_knowledge` — disabled→active after safety/current-revision validation.
- `delete_knowledge` — any non-deleted state→logical tombstone; retains immutable revisions and
  provenance, increments memory revision.
- `mark_knowledge_disputed` / resolve through a new accepted revision where needed.

There is no physical purge command. User-facing output and docs must state that logical deletion
removes selection eligibility but historical rows/backups remain until a later data-erasure design.

## CLI and REPL interaction

Add thin interfaces over the same services:

```text
/learn status
/learn inbox
/learn show <candidate-id>
/learn accept <candidate-id>
/learn edit <candidate-id>
/learn reject <candidate-id>
/learn reject <candidate-id> --never-suggest
/learn reviews

/memory list [--type knowledge]
/memory show <knowledge-id>
/memory disable <knowledge-id>
/memory enable <knowledge-id>
/memory delete <knowledge-id>
```

Top-level Typer mirrors these under `morrow learning` and `morrow memory`.

Interaction contract:

- `/accept` remains Task-only.
- `learn accept/edit` first returns a `LearningPromotionPreview` containing Candidate ID/type,
  scope, evidence summaries, current/after value, conflict, and expected versions.
- Terminal/Typer renders the preview and asks `y/N`; only `yes` sends the typed command.
- Inbox notification after Task review is non-blocking. Entering Inbox may offer one-key navigation,
  but every write still receives a final explicit preview/confirmation.
- REPL edit collects type-specific bounded input and shows a diff. Top-level CLI uses explicit
  options and the same preview. The model never edits on the user's behalf.
- Natural-language acceptance is not registered.

Split CLI registration into a focused module if needed to avoid further growth of
`interfaces/cli.py`; it receives composed services and contains no SQL/YAML logic.

## Events

Sanitized event types:

- `learning.candidate_accepted`
- `learning.candidate_edited_and_accepted`
- `learning.candidate_rejected`
- `learning.candidate_expired`
- `learning.suppression_created`
- `memory.record_activated`
- `memory.record_confirmed`
- `memory.record_superseded`
- `memory.record_disabled`
- `memory.record_enabled`
- `memory.record_deleted`

Payloads contain IDs, type/category, semantic-key digest or bounded non-sensitive key, status,
revision, row version, and reason codes—not full candidate statements or Evidence excerpts.

## Tasks

### S51.1 v11 models and migration

- Add decision, Knowledge, and workspace-memory models/ports.
- Implement the complete v11 migration, active Subplan 51 codecs/repository composition,
  corruption/upgrade tests, reserved Saga table constraint tests, and schema documentation.

### S51.2 Query and preview services

- Implement paginated Review/Candidate/Knowledge queries and pure decision previews.
- Add workspace, status, stale target, current-value, conflict, and evidence projections with strict
  response budgets.

### S51.3 Reject, suppression, and expiry

- Implement immutable decisions, candidate transitions, receipts/events, exact/key suppression,
  lazy expiry, explicit batch maintenance, replay, and concurrency tests.

### S51.4 Project Knowledge promotion

- Implement create/confirm/replace/re-enable conflict paths in one SQLite transaction.
- Attach Evidence, increment workspace memory revision, and preserve immutable revisions.
- Add acknowledged-only acceptance for Skill/Workflow/Orchestration candidates.

### S51.5 Knowledge lifecycle

- Implement list/show/timeline, disable/enable/dispute/delete, supersession, and logical-deletion
  truth in queries/docs.

### S51.6 CLI/REPL Inbox

- Add `/learn`/`/memory` and top-level Typer commands, preview/diff/confirm actions, pagination/JSON
  read output where appropriate, stable errors/exits, and no direct state adapter imports.

### S51.7 Closeout

- Reconcile docs, run focused/full gates, merge verified work, and prepare Subplan 52.

## Fault and regression matrix

- v10→v11 upgrade/rollback/checksum/future refusal and fresh v11 create;
- command replay/conflict/stale row, cross-workspace known IDs, two candidates racing for one key;
- decision/event/receipt/Knowledge/memory revision all-or-nothing rollback;
- original proposal preserved across edit-and-accept and failed edit validation;
- rejection versus suppression, suppression expiry/re-enable, lazy candidate expiry boundaries;
- same Knowledge confirmation versus conflicting replace, disabled/disputed/deleted heads;
- physical old revisions remain queryable after logical delete but never appear in Active lists;
- Skill/Workflow/Orchestration acceptance causes zero runtime/config/file mutations;
- Terminal `no`, EOF, Ctrl+C, malformed IDs/options, stale preview, and repeated confirmation;
- `/accept` still only accepts Task results.

## Planned tests and gate

New tests:

- `tests/test_stage5_learning_application.py`
- `tests/test_stage5_project_knowledge.py`
- `tests/test_stage5_learning_cli.py`

Regression set includes v10 tests, Stage 4 application/CLI/terminal/store/backup/doctor tests,
configuration tests proving no YAML writes, and architecture boundaries. Finish with full non-live,
Ruff, compileall, main/learning/memory CLI help, and `git diff --check`.

## Completion gate

A user can inspect every Candidate and its bounded sources; explicitly accept/edit/reject/suppress
through preview + confirmation; activate/version/manage Project Knowledge; acknowledge but not
activate future Skill/Workflow candidates; and restart without losing decisions. All writes are
idempotent, optimistic, workspace-isolated, evented, and atomic. Preference/Profile acceptance still
fails closed until Subplan 52. Verified work is merged into `main`.

## Out of scope

- Any YAML Profile/Preferences write from Learning.
- MemorySelection/context injection.
- Provider-backed Reviewer.
- Physical purge or secure erasure.
- Natural-language acceptance or GUI.
