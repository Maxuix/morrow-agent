# Subplan 68 — Generated Skill Drafts and Usage

> Status: pending
> Branch: `codex/feat/stage6-skill-drafts`
> Prerequisite: Subplan 67 complete

## Objective

Turn accepted Stage 5 SkillCandidates into reviewable generated Drafts and record bounded SkillUsage.
A Draft can create an immutable approved version only after user review; approval does not silently
enable or repin it.

## Ownership

- `src/morrow/core/skills/drafts.py`, `usage.py`, validation report contracts
- `src/morrow/application/skills/drafts.py`, `validation.py`, `usage.py`
- `src/morrow/adapters/state/migrations_v15_skill_learning.py` and additions to `skill_journal.py`
- Draft/usage CLI additions in `interfaces/skills_cli.py`
- `tests/test_skill_drafts.py`, `tests/test_skill_usage.py`, `tests/test_skill_migration_v15.py`

## Tasks

1. Define Draft state, source Candidate/Evidence refs, parent version, package tree, validation report,
   approval decision and immutable outcome IDs with strict budgets.
2. Add v15 tables for Drafts, validation runs/reports and Usage. Keep raw user text, model reasoning,
   Provider output and full script results out of rows.
3. Implement Candidate→Draft only for accepted, same-workspace SkillCandidates. Allocate a generated
   package root, preserve provenance and make replay return the same Draft.
4. Add deterministic validation: manifest/path/tree, secret/privacy scan, prompt-injection warning,
   structured permission/tool/MCP dependencies, platform compatibility, scripts and sample fixtures.
   Model-generated semantics, if later used, remain a separate no-write helper and are not required
   for this subplan.
5. Provide show/diff/edit/revalidate/accept/reject. Edits create a new Draft revision/tree; accept
   publishes an immutable version through the existing lifecycle saga.
6. Require a separate explicit Binding update after accept. Existing pins never move automatically.
7. Record SkillUsage from terminal AgentRun/Task facts: exact version, activation reason, status,
   user correction flag, bounded token/time/tool metrics and Artifact refs. Usage never triggers an
   update or changes Trust.
8. Add version comparison queries with clear “insufficient data” results instead of automatic
   superiority claims.

## Validation

- Candidate authority/scope, duplicate command replay, every Draft state transition, diff integrity,
  stale parent, validation failure, approval without activation and pinned Binding preservation.
- v14→v15 migration/future schema/doctor refs; bounded Usage and no sensitive payload persistence.
- Focused Draft/Usage/Learning compatibility tests, standard quality commands and full non-live suite
  at the migration gate.

## Exit criteria

- No Candidate or Draft can become selected without both version approval and an enabled Binding.
- User/builtin/imported packages remain immutable to the Draft path.
- v15 is fixed and Usage remains observational only.
