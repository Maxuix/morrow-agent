# Subplan 22 — Handoff Domain, State, and Configuration Excision

> Status: completed
> Depends on: Subplan 21 green with no production Handoff caller

## Goal

Delete the uncalled Handoff domain, patch-schema, port, YAML, fixture, and package
definitions that remain after Subplan 21. This slice must not be responsible for closing
any user/runtime/configuration write path; those paths are required to be closed already.
At completion, `src/` contains no Handoff implementation symbol or compatibility stub.
Active-test occurrences are limited to explicit unknown-command/negative-boundary and
legacy non-destruction assertions, while generic Profile/Preferences and state-safety
coverage remains.

## Preconditions inherited from Subplan 21

- no startup, inspection, onboarding, context, command, terminal, bootstrap, or
  configuration caller can access Handoff;
- `ALLOWED_PATHS` has no Handoff route, the intent gate has no Handoff scope keyword,
  direct patches are rejected, and patch dispatch is explicit;
- `HandoffService` and its module are absent;
- legacy files are ignored and do not affect read-only state.

If any precondition is false, reopen Subplan 21 instead of hiding the defect here.

## Executable tasks

### HR.22.1 — Lock negative and surviving-state boundaries

- Add precise boundary tests that reject Handoff class/service/store/config/command
  definitions in production source and the built package.
- Change the exact workspace-state document contract from
  `preferences/profile/handoff` to `preferences/profile`.
- Preserve the separate Stage 4 boundary: no conversation/session persistence appears.
- Record the remaining definition/call-site inventory and prove every candidate is uncalled
  before deletion.

### HR.22.2 — Remove dormant domain and patch-schema contracts

- Delete `Decision`, `Handoff`, and `HandoffDocument` from `core/models.py`.
- Remove Handoff validators, field normalizers, and domain tests.
- Remove `handoff` from `ConfigPatch.target`.
- Delete the dormant `Decision`-specific list matching branch and remove Handoff-only
  field names such as progress/next_steps/decisions/blockers from generic patch validation.
- Remove imports, annotations, test doubles, and fixtures tied to these contracts.

### HR.22.3 — Remove dormant state port and YAML definitions

- Delete `load_handoff`, `load_handoff_backup`, `write_handoff`, and
  `clear_handoff` from `ProjectStateStore`.
- Delete Handoff imports and document methods from `ProjectStateYamlStore`.
- Keep workspace envelopes, tombstones, backups, revisions, locking, fsync, and atomic
  replacement for Profile and Preferences.
- Delete only helpers proven Handoff-exclusive; retain reusable state infrastructure.

### HR.22.4 — Retarget generic state and isolation tests

- Remove Handoff rows from parameter matrices while retaining Profile/Preferences rows.
- Retarget `test_missing_workspace_backup_is_distinct_from_missing_primary` to Profile or
  Preferences instead of deleting its generic backup distinction.
- Retarget `test_two_workspaces_have_isolated_profile_and_handoff` to Profile and, where
  useful, workspace Preferences.
- Keep equivalent coverage for invalid/future documents, upgrade, recovery, conflicts,
  tombstones, relink, locking, concurrency, and atomic publication.
- Remove Handoff stubs from terminal/orchestration fixtures without weakening their generic
  contracts.

### HR.22.5 — Prove complete source, test, and package excision

- Scan `src/` and tests for precise patterns. Source matches must be zero. Test matches must
  be reviewed against an explicit allowlist containing only unknown-command rejection,
  negative-boundary, and legacy byte/non-read assertions. Use `class Decision` and
  `Decision(`, not bare `Decision`, because `GateDecision` is unrelated.
- Prove active tests import none of `Handoff`, `HandoffDocument`, `Decision`, or
  `HandoffService`; textual command/file sentinels do not require those definitions.
- Restrict command scans such as `/continue` to `src/` and tests; documentation and
  `.agent/` contain intentionally classified historical text until Subplan 23.
- Build and inspect the wheel; prove no `morrow/services/handoff.py`, public model, schema,
  store method, or package resource remains.
- Prove legacy primary/backup sentinels remain ignored and byte-identical during supported
  Profile/Preferences operations.
- Run the complete offline and quality gate.

## Completion criteria

- `src/` has no Handoff type, service, port, YAML method, context purpose, config target,
  action, command, field, or compatibility stub.
- Active tests have no Handoff implementation fixture or expected supported behavior;
  only explicit unknown-command, negative-boundary, and legacy non-destruction assertions
  remain.
- Workspace state supports exactly Profile and workspace Preferences documents.
- Generic structured completion, state safety, Profile/Preferences patching, workspace
  isolation, and Stage 2 AgentLoop behavior remain green.
- The installed package contains no Handoff module or public symbol.

## Validation

```bash
uv run pytest -m 'not live'
uv run ruff format --check .
uv run ruff check .
uv run python -m compileall -q src tests
uv run morrow --help
git diff --check
```

Negative scans cover at least these precise patterns. They require zero production-source
matches and an explicitly reviewed test allowlist as described above:

```text
class Handoff
Handoff(
HandoffDocument
class Decision
Decision(
handoff.yaml
/handoff
/continue
loaded_handoff
handoff_source_revision
is_continuation
handoff_fallback
switch_continue
update_handoff
clear_handoff
```
