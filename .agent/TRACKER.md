# Progress Tracker

## Current status

Subplans 63 (spike), 64 (per-AgentRun preparation) and 65 (Skill package/catalog foundation + v14)
are complete and merged to local `main`. Skills are discoverable and truthfully projectable but
nothing is enabled, selected or injected yet. `main` is 3 commits ahead of `origin/main` (no push;
remote publication not in scope).

## Last completed work (Subplan 65)

- core/skills/: identity rules (skill_id/skv_/display version, reserved names, collision checks),
  manifest contracts, TrustLevel + effective_trust from local provenance, four-concept catalog
  contracts, composite (scope, source_kind, skill_id).
- adapters/skills/: strict manifest parser (SKILL.md frontmatter + morrow.yaml, unknown keys rejected,
  declarations non-authoritative), canonical tree builder (regular files only; fstat+bytes from one
  open; exec mode; rejects symlinks/hardlinks/devices/FIFOs/sockets, escape, normalization
  collisions, reserved names, oversize, non-UTF-8), envelope writer/reader/verifier with digest
  checks and drift detection, bounded root discovery.
- application/skills/catalog.py: deterministic projection with exact conflict rules (fold on same
  digest; identity_conflict; name_conflict), requested vs effective Trust exposed, bounded failures.
- Operational Store v14 (definitions, versions, catalog operations, reserved AgentRun
  selections/contexts with scope+scope_id) + SqliteSkillJournal + thin aggregate delegation;
  version bump 13->14 with fixed checksum.
- Validation: `tests/test_skill_catalog.py` 17 tests + `tests/test_skill_migration_v14.py` 5 tests;
  full offline suite `983 passed, 1 skipped (mcp spike), 2 deselected`; ruff/compileall/
  `git diff --check` green; `pyproject.toml`/`uv.lock` unchanged; pre-existing migration tests'
  hardcoded version expectations bumped to 14.

## Active task

None in progress. Next: activate Subplan 66 (Skill lifecycle and Binding control) from latest `main`.

## Next action

Create `codex/feat/stage6-skill-lifecycle` for Subplan 66 when the user asks to continue.

## Dependency gate

No dependency added through Subplans 63–65. Before Subplan 72, ask the user to approve the exact
change: `mcp >= 2.0.0, < 3` and `jsonschema >= 4.20, < 5` (rationale and versions in the ADR). If
denied, complete Subplans 66–71 normally and mark 72–73 blocked.

## Blockers

None for Subplans 66–71. MCP implementation Subplans 72–73 are conditional on the recorded
dependency decision and explicit approval if new packages are recommended.

## Notes

- `main` is three local commits ahead of `origin/main` (Subplans 63/64/65); remote publication was
  not placed in scope, so no push was made.
- The spike test file skips cleanly in the default dev env (`importorskip("mcp")`).

## Preserved history

Subplan 63 evidence: ADR + `tests/spikes/`; Subplan 64 evidence: `tests/test_agent_run_preparation.py`;
Subplan 65 evidence: `tests/test_skill_catalog.py`, `tests/test_skill_migration_v14.py`. Detailed
history stays in Git; it is not duplicated in this active tracker.