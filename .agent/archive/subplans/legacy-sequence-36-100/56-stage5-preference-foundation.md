# Subplan 56 — Generic Preference Foundation and Migrations

> Status: completed
> Branch: `feat/stage5-preference-foundation`
> Base: latest verified local `main` after the plan is accepted
> Owns: generic Preference contracts, YAML migration codecs, Operational Store v13

## Goal

Create the stable data and persistence foundation for Preference v2 without activating the new
Reviewer or changing foreground behavior. This subplan reserves every v13 table/constraint needed by
later slices so its migration checksum never changes.

## Locked deliverables

### S56.1 Generic domain contracts

- Add focused models for `PreferenceEntry`, status/scope, add/replace/remove operations,
  enable/disable lifecycle commands, frozen active summaries, proposals, and same-scope write batches.
- Normalize whitespace and reject empty/control-character/oversized statements; do not introduce a
  topic, language, detail, taxonomy, semantic-key, or keyword classifier.
- Implement a pure batch reducer that either returns one valid next document or raises before any
  state change. Exact duplicate checks are same-scope across `active | disabled`; disabled matches
  require enable/replace, while deleted tombstones do not block a new add with a new ID/revision 1.
  Cover unique IDs, entry revisions, duplicate targets, cross-scope coexistence, and mixed-scope
  rejection explicitly.
- Keep this code outside `core/learning.py`; split models and operation logic when responsibilities
  diverge.

### S56.2 Versioned YAML codecs and migration

- Decouple global config, workspace Preference document, Profile document, and workspace-index schema
  constants.
- Define global config v2 and workspace Preference document v3 using `entries` as the only active
  Preference payload. Global v2 remains the complete aggregate of Preferences, Providers, and
  `active_model` under one revision; Profile/index schema and payloads remain unchanged.
- Implement the master plan's exact legacy sentences and conversion order. Derive `pref_` IDs from
  scope/path/original value/occurrence, not rendered text, and exact-deduplicate with the documented
  mapped-entry winner.
- Preserve a compatibility decoder for immutable legacy AgentRun snapshots; do not rewrite historical
  rows.
- Implement migration prepare/backup/publish behavior, idempotent replay, OCC, corrupt/future-schema
  refusal, and rollback/failure injection tests. Do not activate automatic publication until S57.
- Move Preference-specific YAML code into `adapters/state/preference_yaml.py`; keep the existing
  facade thin.

### S56.3 Operational Store v13

Reserve and implement the complete bounded Preference v2 persistence schema:

- `preference_review_jobs`: workspace/session/turn identity, review version, status, source Preference
  revisions, Reviewer metadata, lease/retry/row version/timestamps/failure code, plus a frozen Active
  snapshot JSON, count, byte count, and digest constrained to at most 256 entries and 192 KiB;
- `preference_evidence`: exactly one `pev_` current-user row per job, same-workspace source pointer,
  bounded redacted excerpt, digest, safety rejection, and timestamps;
- `preference_proposals` plus Evidence links: immutable proposed operation, expected target/document
  revisions, fingerprint, status, optional final edited operation, decision metadata, and OCC;
- `preference_write_batches`: same-scope prepared operations with allocated add IDs, command identity,
  candidate links, before/after revisions and digests, saga status, recovery metadata, and timestamps.

Put v13 statements in `adapters/state/migrations_v13_preferences.py` and register them thinly from the
existing migration module. Update `core/store.py` to v13. Add workspace/FK/unique/check constraints,
indexes for pending leases and Inbox queries, strict codecs, bounded ports, transaction forwarding,
doctor-count hooks, fresh-store creation, v12→v13 upgrade, rollback, future-schema refusal,
corruption, and cross-workspace tests. No later subplan may edit the committed v13 statements or
checksum.

### S56.4 Compatibility inventory

- Add fixtures for legacy global/workspace Preferences, cleared/missing documents, historical
  AgentRun snapshots, accepted activations, unresolved legacy Preference Candidates, and corrupt/
  future state.
- Record the exact legacy handling matrix: decode-on-read/no-publish in S56, full-aggregate first-write
  publication in S57, immutable snapshot decoder activation and unresolved Candidate translation in
  S57, and migration-superseded legacy undo in S57.
- Update architecture/roadmap only to describe foundation code actually present; do not claim the new
  Reviewer or async worker is active.

## Primary files

- `src/morrow/core/preference_models.py`
- `src/morrow/core/preference_operations.py`
- `src/morrow/core/preferences.py`
- `src/morrow/adapters/state/preference_yaml.py`
- `src/morrow/adapters/state/preference_journal.py`
- `src/morrow/adapters/state/migrations_v13_preferences.py`
- thin registration in `src/morrow/adapters/state/migrations.py`
- `src/morrow/core/store.py`
- bounded journal/port exports and focused tests

## Acceptance

- Pure reducer proves a valid 1–8 operation same-scope batch is all-or-nothing.
- Legacy fixed fields deterministically map to independent entries and repeated migration is
  byte/semantic stable.
- Global v1 decode preserves Providers/active model without publishing; a simulated full-aggregate
  v2 write preserves them and increments the shared revision once.
- Backup and primary behavior is correct for success, publish failure, corrupt input, and future
  schema.
- v13 migration/rollback/checksum and all workspace/FK/lease constraints pass.
- Frozen snapshot count/byte/digest constraints and exactly-one `pev_` Evidence linkage pass.
- No production code path can yet enqueue or execute a Preference v2 Review.
- No new production file becomes a god file and no third-party dependency is added.

## Validation

```bash
uv run pytest -q tests/test_preference_domain.py tests/test_preference_yaml.py \
  tests/test_preference_store.py tests/test_operational_store.py \
  tests/test_stage4_session_conversation.py tests/test_state_and_workspace.py
uv run pytest -m 'not live'
uv run ruff format --check .
uv run ruff check .
uv run python -m compileall -q src tests
git diff --check
```

## Review and closeout

After the implementation checkpoint and gates pass, run exactly one `$grok-delegate` `/review` for
S56, wait for the complete result, independently adjudicate it, apply confirmed/valuable fixes once,
and rerun the affected and S56 gates. Do not run a second Grok review for the fix. Commit closeout,
fast-forward merge to `main`, retire the clean branch, then activate S57.
