# Progress Tracker

## Current status

Subplans 63 (spike), 64 (per-AgentRun preparation), 65 (Skill package/catalog foundation + v14),
66, 67, 68, 69, 70 and 71 are complete locally; the Subplan 65 review repairs are also complete.
Subplan 69 adds constrained Skill script execution, Subplan 70 adds the Provider/Model control
plane, and Subplan 71 adds generic dynamic-tool contracts without widening the existing
AgentLoop/Session/Task boundaries. The verified Subplan 71 checkpoint is committed locally;
remote publication is not in scope.

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

## Last completed work (Subplan 71)

- Added one bounded `ToolArgumentsValidator` seam, a Pydantic compatibility adapter and a
  dependency-free explicit Draft 2020-12 JSON Schema subset with input/schema/depth/property/
  array/string/number budgets, unsupported keyword rejection and local-reference-only behavior.
- `RegisteredTool` now has one validator and one recovery declaration; all local/configuration/
  preference/Git/Skill/fixture factories pass explicit declarations. Legacy `arguments_model`
  callers are constructor-adapted without retaining a second execution authority.
- Prepared intents freeze the declaration into durable evidence before handler entry; recovery uses
  that evidence after the current registry changes. Dynamic-tool tests cover validation, approval,
  execution and crash-classification stability without name-prefix branches.
- Validation: focused compatibility/dynamic/recovery suites `76 passed`; full non-live gate
  `1036 passed, 3 skipped, 2 deselected`; Ruff, compileall and `git diff --check` passed.

## Next action

Ask the user to approve the exact Subplan 72 dependency change (`mcp >= 2.0.0, < 3` and
`jsonschema >= 4.20, < 5`) before activating MCP control-plane work.

## Dependency gate

No dependency added through Subplans 63–65. Before Subplan 72, ask the user to approve the exact
change: `mcp >= 2.0.0, < 3` and `jsonschema >= 4.20, < 5` (rationale and versions in the ADR). If
denied, complete Subplans 66–71 normally and mark 72–73 blocked.

## Blockers

None for Subplans 66–71. MCP implementation Subplans 72–73 are conditional on the recorded
dependency decision and explicit approval if new packages are recommended.

## Notes

- `main` is ahead of `origin/main` through the verified Subplan 71 fast-forward. Remote publication
  was not placed in scope, so no push was made.
- The spike test file skips cleanly in the default dev env (`importorskip("mcp")`).

## Last completed work (Subplan 69)

- Added strict Skill script request/result contracts with bounded argv, environment names, input
  Artifact IDs, output paths and timeout; shell strings and overlapping output paths are rejected.
- Added verified whole-package capture and immediate pre-launch envelope/tree/script digest checks;
  execution uses an isolated temporary root, read-only Skill copy, declared input Artifacts and
  declared output files only. Root/output/input symlink and mutation checks fail closed.
- Registered `run_skill_script` through the existing ToolExecutor/CapabilityPolicy path with a
  sandbox-required OperationIntent, manifest permission risk flags, approval metadata and an
  outcome-unknown recovery declaration. Stdout/stderr and output files are redacted/bounded before
  the output Artifact refs enter the result envelope.
- Validation: focused suite `82 passed, 2 skipped`; full non-live suite `1023 passed, 3 skipped,
  2 deselected`; Ruff, compileall and `git diff --check` passed. Commit `73f99db`; no push.

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
