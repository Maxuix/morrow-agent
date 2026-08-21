# Subplan 57 — Atomic Preference Writer and Direct Lifecycle

> Status: planned; not active
> Branch: `feat/stage5-preference-writer`
> Prerequisite: Subplan 56 merged and v13 checksum frozen
> Owns: YAML-authoritative Writer, recovery saga, direct management tool/commands

## Goal

Cut active Preference management over to generic entries and provide one deterministic, recoverable
Writer for all direct and Candidate-driven writes. Profile configuration remains on its current
service; no model semantics are added here.

## Tasks

### S57.1 Writer prepare and atomic apply

- Implement `PreferenceWriter.prepare()` from one scope, expected document revision, command ID, and
  one to eight operations.
- Load and validate the source document once, allocate stable add IDs, run the pure reducer, and
  persist a bounded prepared write batch before YAML changes.
- Publish one global or workspace YAML document exactly once; mixed scopes are rejected.
- Finalize the batch, sanitized event, command receipt, and linked proposal decisions atomically in
  SQLite. Same command + same digest replays; same command + different digest conflicts.

### S57.2 Recovery and drift

- Implement recovery for crash before YAML, after YAML/before finalize, after finalize, retry, and
  process restart.
- Retry only at the exact before revision/digest; finalize only at the exact expected after
  revision/digest; every other state is `needs_resolution` and is never overwritten.
- Add deterministic failure injection around prepare, backup, YAML publish, and finalize.
- Surface legacy fixed-field activation undo as migration-superseded when its digest can no longer
  apply; preserve historical rows and provide the new explicit lifecycle alternatives.

### S57.3 Generic lifecycle

- Add, replace, and remove go through Writer batches. Disable/enable are explicit lifecycle commands
  using the same OCC/recovery path; deleted entries are terminal.
- Replace preserves ID and scope; remove produces a tombstone; all successful operations increment
  entry revision and one document revision.
- Bound entry Evidence references while retaining full audit in SQLite batches/events.
- Add query projections for list/show by ID, scope, and status without exposing raw YAML internals.

### S57.4 Direct tool and user surfaces

- Add approved `manage_preferences` with one-scope multi-operation arguments and truthful preview.
  It reuses `OperationKind.CONFIGURATION_WRITE`, the existing persistent-write approval boundary, and
  a dedicated recovery declaration; it does not change bundled policy/PermissionProfile defaults.
- Keep `update_configuration` for Profile and unrelated configuration only. Remove fixed Preference
  paths from its new public schema; temporary compatibility parsing must not appear in model tool
  definitions or help.
- Add `morrow preferences list/show/add/replace/remove/disable/enable` and bounded REPL equivalents.
  UI code parses, renders, confirms, and calls services only.
- Update Session-local Preference operations to use generic in-memory entries and clear them on
  Session reset; do not persist session scope to YAML.
- Record successful direct batches with their source Turn. Terminal Review enqueue skips that Turn;
  exact-duplicate validation remains a second guard against race/replay.

### S57.5 Legacy Candidate bridge

- Before generic YAML becomes writable, add an internal, non-tool translator from historical/live v12
  `PreferenceCandidatePayload` into one generic Writer batch. Map language/detail to the deterministic
  migrated IDs/statements; map instruction tuples to independent operations in one batch.
- Route existing Preference Candidate preview/accept/edit through this bridge so an S57 commit never
  leaves unresolved or newly emitted v12 Candidates with a dead apply path.
- Keep the old broad Reviewer temporarily able to emit those internally translated Candidates; S58
  disables new fixed-field Preference emission only after the new Reviewer/Inbox path exists.
- Preserve original Candidate/Evidence/decision history and expose translation/stale failures as typed
  reasons, never by mutating the old proposal.

### S57.6 Cutover and migration

- Activate global/workspace legacy migration with backup on the first successful write, never on
  read. Global `config.yaml` is published as the complete v2 aggregate through the existing locked
  mutator: Preference writes preserve Providers/active model, Provider/model writes preserve entries,
  and the shared revision increments once. Workspace Preference v3 remains a separate file.
- Switch GlobalConfig, ProjectPreferencesDocument, Session projections, configuration snapshots, and
  basic ContextBuilder rendering to generic entries so the complete offline suite remains runnable.
- Wire the S56 legacy AgentRun decoder into journal load, backup verify, and every stored-snapshot parse
  before changing the live `Preferences` shape. A v1 snapshot must recover old semantics rather than
  silently validating to `entries=[]`.
- Keep the S60 freshness improvement separate: S57 may retain current load timing, but every new
  snapshot must use the new shape and record exact source revisions.
- Update tests and fixtures away from `.language`, `.response_detail`, and `.instructions`, except
  explicit migration/legacy-snapshot coverage.

## Primary files

- `src/morrow/application/preferences/writer.py`
- `src/morrow/application/preferences/recovery.py`
- `src/morrow/application/preferences/queries.py`
- `src/morrow/application/preferences/tool.py`
- `src/morrow/interfaces/preferences_cli.py`
- bounded `src/morrow/application/preferences/` services replacing Preference responsibilities in
  the existing 573-line configuration service
- `src/morrow/bootstrap.py` composition only
- thin legacy dispatch in existing Learning promotion/inbox modules

## Acceptance

- One valid multi-operation batch produces one YAML revision and all expected entry revisions.
- One invalid/stale operation produces zero YAML, decision, or event changes.
- Every crash point recovers exactly or stops at visible drift; no replay creates a second add ID.
- Direct CLI/REPL/tool operations share the same Writer and approval semantics.
- Profile configuration, Provider configuration, Task/tool recovery, and read-only/corrupt state
  behavior remain unchanged.
- Public tool/help output contains no fixed Preference domain fields.
- Old and newly emitted v12 Preference Candidates remain previewable/acceptable through the internal
  translator until S58 disables new emission.
- Global Preference and Provider/model writes each preserve the other half of the aggregate; v1 reads
  do not rewrite the file.
- Historical AgentRun and backup fixtures recover exact fixed-field semantics after the model cutover.

## Validation

```bash
uv run pytest -q tests/test_preference_writer.py tests/test_preference_recovery.py \
  tests/test_preference_cli.py tests/test_configuration_tool.py \
  tests/test_preferences_and_orchestration.py tests/test_context_runtime.py \
  tests/test_stage4_recovery_crash.py tests/test_state_and_workspace.py
uv run pytest -m 'not live'
uv run ruff format --check .
uv run ruff check .
uv run python -m compileall -q src tests
uv run morrow preferences --help
git diff --check
```

## Review and closeout

After the implementation checkpoint and gates pass, run exactly one `$grok-delegate` `/review` for
S57, wait, independently verify every finding, fix confirmed/valuable items once, and rerun the
affected and S57 gates. Do not re-review the fix. Commit closeout, fast-forward merge, retire the
clean branch, and activate S58.
