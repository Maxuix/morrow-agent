# Subplan 60 — Fresh Preference Context and Observability

> Status: final gates passed; closeout pending
> Branch: `fix/stage5-preference-context-refresh`
> Prerequisite: Subplan 59 merged
> Owns: next-AgentRun reload/freeze, bounded rendering, typed diagnostics

## Goal

Guarantee that accepted/changed Preferences affect the next AgentRun of both new and restored
Sessions while preserving same-Run reproducibility and keeping Preference injection distinct from
Project Knowledge MemorySelection.

## Tasks

### S60.1 Admission-time reload

- Before each new foreground AgentRun admission, load the latest valid global and workspace
  Preference documents and their revisions; do not reuse a restored Session's cached persistent
  values.
- Build the AgentRun snapshot from that exact projection plus process-local session entries, then
  record source revisions/digest with the run.
- Define the cross-store race boundary honestly: a YAML change after projection load belongs to the
  following AgentRun. Do not hold YAML locks across the SQLite admission transaction.
- Preserve fail-closed behavior for corrupt/future/read-only Preference state and expose the reason.

### S60.2 Frozen run projection

- The same AgentRun and its recovery path reuse the exact frozen generic Preference projection even
  if YAML changes mid-run.
- A new Turn always creates a new AgentRun and sees the latest projection, including in an already
  restored Session.
- Consume the legacy-decoded projection already activated in S57; do not postpone any old-snapshot
  parsing change to this subplan or substitute current state during recovery.
- Validate snapshot digest/revision mismatch as recovery/repair state, not a silent fallback.

### S60.3 Deterministic context rendering

- Inject only `active` statements in a typed lower-authority block with Preference ID and scope.
- Keep the v13 Review snapshot (all Active durable entries up to 256/192 KiB) separate from the
  AgentRun injection projection (64 entries/8 KiB).
- Exact-normalized duplicates retain `session > workspace > global`. Select for budget by scope
  priority, `updated_at` descending, then ID ascending; render selected entries global→workspace→
  session, `updated_at` ascending, then ID ascending. Non-equal conflicts at different scopes both
  render. Report omitted count instead of silently claiming all rules fit.
- Keep safety/capability instructions above Preferences. Add adversarial tests proving a stored rule
  cannot grant a tool, bypass approval, change sandbox scope, or override system/developer policy.
- Exclude disabled/deleted entries immediately from the next AgentRun while keeping them inspectable.

### S60.4 Typed status and doctor projection

- Report Preference document revisions, active/disabled/deleted counts, injected count/digest,
  omitted count, and source scopes separately from Project Knowledge MemorySelection ID/revision/
  item count.
- Ensure user-facing status cannot mislabel Active Preference injection as Knowledge retrieval, or an
  empty Knowledge selection as missing Preferences.
- Add bounded event/query metadata for refresh and omission without statements, Evidence excerpts,
  credentials, or raw context.

## Primary files

- `src/morrow/application/turn_lifecycle.py`
- `src/morrow/application/context.py`
- `src/morrow/application/learning/memory_run_projection.py`
- `src/morrow/core/context.py`
- `src/morrow/runtime/session.py`
- focused status/doctor/CLI projection modules

## Acceptance

- Add, replace, disable, enable, and remove become visible on the next AgentRun in the same restored
  Session without starting a new Session.
- A Preference change during an AgentRun is invisible to that Run and visible to the next.
- Old snapshots recover their exact old semantics; missing/mismatched frozen state fails closed.
- Budget ordering/deduplication is deterministic across restart and reports omissions.
- MemorySelection remains unchanged and independently observable.
- Capability/approval/tool tests prove stored natural-language rules have no authority escalation.

## Validation

```bash
uv run pytest -q tests/test_preference_context.py tests/test_stage5_memory_context.py \
  tests/test_stage5_memory_agent_run.py tests/test_context_runtime.py \
  tests/test_stage4_recovery_crash.py tests/test_stage4_permissions.py \
  tests/test_stage5_memory_selection.py tests/test_stage5_learning_cli.py
uv run pytest -m 'not live'
uv run ruff format --check .
uv run ruff check .
uv run python -m compileall -q src tests
uv run morrow --help
git diff --check
```

## Review and closeout

After the implementation checkpoint and gates pass, invoke exactly one `$grok-delegate` `/review`
for S60 and wait. Codex verifies each finding, fixes the confirmed/valuable subset once, reruns
affected/S60 gates, and does not request a second review. Commit, fast-forward merge, retire the clean
branch, and activate S61.
