# Subplan 67 — Skill Selection, Context and Resources

> Status: pending
> Branch: `codex/feat/stage6-skill-context`
> Prerequisite: Subplan 66 complete

## Objective

Select exact enabled Skill versions for each new AgentRun, persist reproducible selection/context
evidence, inject bounded low-authority Skill instructions, and expose frozen resource reads. Preserve
same-run freeze and historical recovery.

## Ownership

- `src/morrow/core/skills/selection.py`, `context.py`, `resources.py`
- `src/morrow/application/skills/selection.py`, `context.py`, `resources.py`
- focused v14 row mapping additions already reserved by Subplan 65
- thin integration in `application/agent_runs/`, existing ContextBuilder and doctor
- `tests/test_skill_selection.py`, `tests/test_skill_context.py`, `tests/test_skill_resources.py`

## Tasks

1. Resolve candidates from explicit user selection and Workspace defaults. Implement only the
   bounded conservative description fallback locked by Subplan 63, with an off switch and recorded
   activation reason; no learned router.
2. Apply scope, Binding, pin, availability, dependency, platform, capability, count and context
   budgets deterministically. Persist selected and omitted reasons.
3. Revalidate version/tree/file digests while loading `SKILL.md`; drift makes the Skill unavailable
   and cannot silently choose another version.
4. Persist one bounded `SkillSelection` and dedicated context row in the same AgentRun admission
   transaction. AgentRun main snapshot stores only IDs/digests/counts/source revisions.
5. Render a typed Skill context block below fixed safety/system/developer policy and separately from
   Preferences and MemorySelection. Permission prose cannot grant tools or alter approval.
6. Implement `SkillResourceService` for references/assets using frozen selection and relative paths;
   reject arbitrary host paths, revalidate content, enforce MIME/byte limits and return Artifact refs
   for binary/large content.
7. Make `rehydrate()` use persisted selection/context only. A disabled/updated Skill affects the next
   new AgentRun, never the current or recovered historical run.
8. Add bounded status/doctor projections without full Skill content.

## Validation

- Explicit/default/fallback ordering, scope isolation, pins, omissions, context ordering/budgets,
  hostile instructions, tree drift and unavailable dependencies.
- Same Session next-run refresh, same-run freeze, closed replay with zero Catalog reads, recovery
  after Binding/package drift, main snapshot size boundaries and context tamper detection.
- Resource traversal/symlink/MIME/size/Artifact cases.
- Focused tests, affected context/recovery tests, standard quality commands and full non-live suite.

## Exit criteria

- A handwritten enabled Skill appears only in the intended new AgentRun with exact version evidence.
- Historical/replay behavior is independent of current Binding/Catalog state.
- Skill content cannot expand authority or overflow AgentRun/model budgets.
