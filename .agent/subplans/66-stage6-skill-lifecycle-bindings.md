# Subplan 66 — Skill Lifecycle and Binding Control

> Status: pending
> Branch: `codex/feat/stage6-skill-lifecycle`
> Prerequisite: Subplan 65 complete

## Objective

Implement user-facing validate/install/enable/disable/pin/rollback/remove with versioned Extension
YAML as Binding authority and recoverable filesystem/YAML/SQLite operations. Installation remains
disabled by default; this subplan still does not inject Skills into AgentRun context.

## Ownership

- `src/morrow/core/skills/bindings.py`, lifecycle commands/results
- `src/morrow/adapters/state/extension_yaml.py` for global/workspace desired state
- `src/morrow/application/skills/lifecycle.py`, `bindings.py`, `queries.py`, recovery helper
- `src/morrow/interfaces/skills_cli.py` and thin root CLI registration
- `tests/test_skill_lifecycle.py`, `tests/test_skill_bindings.py`, CLI/recovery tests

## Tasks

1. Define independent global and workspace Extension documents with schema, revision, updated time,
   SkillBindings (`scope + scope_id`) and reserved MCP section. Binding selection mode is exactly
   explicit, workspace-default or description-match. Do not add Skills to existing
   Preference/Profile files.
2. Implement OCC reads/writes, last-valid backup and future/corrupt schema failure behavior using
   current YAML primitives.
3. Implement validate and local-path install. Show provenance, digest, permissions, scripts and
   conflicts before confirmation; publish a managed version but no enabled Binding.
4. Implement enable/disable/pin/rollback against exact IDs/source. Ambiguous names and identity
   conflicts fail closed; missing versions/dependencies report unavailable.
5. Implement remove semantics: scope-only remove unbinds; package removal is limited to
   generated/imported, requires explicit confirmation and refuses versions referenced by runs,
   Drafts, Usage, rollback points or backup manifests.
6. Use command IDs and a prepare→package apply→YAML apply→SQLite finalize saga where more than one
   authority changes. Replays return the recorded result; unrelated drift becomes needs-resolution.
7. Prevent Agent-facing lifecycle tools in this slice. CLI/REPL calls application services; no
   interface writes YAML, packages or SQLite directly.
8. Expose bounded list/show/status with effective Trust, requested Trust, conflicts, binding scope,
   pin and availability; never dump full scripts by default.

## Validation

- Install disabled, exact replay, crash at each saga boundary, YAML drift, package collision,
  cross-workspace access, all source mutation protections and referenced-version deletion refusal.
- CLI help and command projections, future/corrupt YAML, backup read, no project-directory writes.
- Focused lifecycle/configuration tests, standard quality commands and full non-live suite at the
  cross-store recovery gate.

## Exit criteria

- Every lifecycle action has one visible authority and deterministic replay/recovery.
- Binding, Version and Catalog states cannot contradict by design.
- No AgentRun behavior changes until Subplan 67.
