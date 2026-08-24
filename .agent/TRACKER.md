# Progress Tracker

## Current status

Subplans 63 (spike), 64 (per-AgentRun preparation) and 65 (Skill package/catalog foundation + v14)
are complete and merged to local `main`; the Subplan 65 review repairs are also complete locally.
Subplans 67 and 68 are complete locally. Skills remain discoverable and truthfully projectable;
Subplan 68 adds only bounded generated Draft review and observational Usage on top of accepted Stage
5 Candidates. The verified checkpoint is ready for a local Git commit; remote publication is not in
scope.

## Last completed work (Subplan 66)

- Added independent global/workspace Extension YAML authorities with bounded SkillBindings, exact
  selection modes, reserved MCP state, OCC, last-valid backups, future-schema/corrupt failure
  classification and scope-safe atomic publication.
- Added bounded local Skill validation and immutable imported/generated package publication with
  captured bytes, provenance/digest/permissions/scripts/dependency previews, collision checks and
  no automatic enablement.
- Added enable/disable/pin/rollback/remove application services with exact source/version
  resolution, dependency/reference protections, YAML-authoritative Binding changes and bounded
  list/show/status projections.
- Added command-id receipts, global/workspace replay, package/YAML/SQLite saga phase records and
  recovery for package-published, YAML-published and package-removal boundaries; no AgentLoop
  lifecycle tools or selection/context injection were added.
- Validation: focused Skill suite `41 passed`; full non-live suite `1003 passed, 3 skipped,
  2 deselected`; CLI help, Ruff format/check, compileall and `git diff --check` passed.

## Previous completed work (Subplan 65)

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

Subplan 69 — Skill Script execution is next; it has not been activated or implemented yet.

## Next action

Create the verified local Git checkpoint for Subplan 68, then activate Subplan 69 and inspect the
existing ProcessAdapter, permission and Artifact seams before implementing script execution.

## Dependency gate

No dependency added through Subplans 63–65. Before Subplan 72, ask the user to approve the exact
change: `mcp >= 2.0.0, < 3` and `jsonschema >= 4.20, < 5` (rationale and versions in the ADR). If
denied, complete Subplans 66–71 normally and mark 72–73 blocked.

## Blockers

None for Subplans 66–71. MCP implementation Subplans 72–73 are conditional on the recorded
dependency decision and explicit approval if new packages are recommended.

## Notes

- `main` is three local commits ahead of `origin/main` (Subplans 63/64/65); review repairs remain
  as working-tree changes because `.git` refs are read-only in this workspace. Remote publication
  was not placed in scope, so no push was made.
- The spike test file skips cleanly in the default dev env (`importorskip("mcp")`).

## Last completed work (Subplan 68)

- Added bounded Draft, validation-report and observational Usage contracts; v15 tables, migration,
  journal ports and digest-checked mappings keep raw user text, model reasoning and full results out
  of SQLite.
- Added accepted same-workspace SkillCandidate→Draft generation with replay-safe roots, deterministic
  manifest/tree/privacy/secret/prompt-injection validation, revision diff/edit/revalidate/reject and
  lifecycle publication through immutable Skill versions.
- Draft acceptance records approval evidence but does not enable or repin a Binding; Usage records
  exact selection/version facts and only provides descriptive or insufficient-data comparisons.
- Validation: focused Draft/Usage/migration suite `6 passed`; full non-live suite `1017 passed,
  3 skipped, 2 deselected`; Ruff format/check, compileall, CLI help and `git diff --check` passed.

## Previous completed work (Subplan 67)

- Added deterministic explicit/default/conservative description Skill selection with bounded
  omission reasons, frozen version/tree/context evidence and same-admission persistence.
- Added low-authority bounded Skill context injection, journal-only historical rehydration and
  frozen relative resource reads with drift/traversal/MIME/Artifact limits.
- Validation: full non-live suite `1011 passed, 3 skipped, 2 deselected`; Ruff format/check,
  compileall, CLI help and `git diff --check` passed.

## Preserved history

Subplan 63 evidence: ADR + `tests/spikes/`; Subplan 64 evidence: `tests/test_agent_run_preparation.py`;
Subplan 65 evidence: `tests/test_skill_catalog.py`, `tests/test_skill_migration_v14.py`. Detailed
history stays in Git; it is not duplicated in this active tracker.
