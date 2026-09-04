# Subplan 10 — Context, Learning and Skill Management GUI

> Status: pending activation
> Branch: `feat/stage8-context-learning-skill-gui`
> Activation base: latest verified `main` with Subplan 4 integrated (re-sequenced per plan §4)
> Prerequisite: Subplan 4 verified; technically independent of Subplans 5–9
> Roadmap authority: stage-8 §8.2, §9, §10, §8F

## Objective

Give users full GUI management of Active Context, Preferences/Knowledge/Learning candidates and
the Skill lifecycle — with GUI and CLI producing identical revisions, events and runtime
resolution.

## Deliverables

- Active Context Bar per §8.2 (compact summary: language, verbosity, workspace, convention count,
  pending-learning count) and Context Drawer: Resolved Preferences, Profile, selected Knowledge,
  sources and scopes, add/edit/disable/delete, pending Learning candidates.
- Preference/Knowledge/Candidate management per §9: Active/Proposed/History views; edit display
  shows scope, current value, source/evidence, affected Resolved Context and supersession; "live
  display" means the current task's actually-resolved `ResolvedPreferences`, not the raw record
  list.
- Skill management per §10: source, version, scope, status, SKILL.md summary, requested
  tools/capabilities, scripts and trust level, test/eval results, Draft diff,
  Enable/Disable/Pin/Update/Rollback. Auto-generated Skills appear as Draft cards only — never
  mixed into the Active list.
- All mutations go through the unified Command Service; the GUI adds no side channel.

## Key semantics

- GUI and CLI mutations produce byte-identical revision/event/resolution outcomes (the slice
  gate).
- Deferral of this product surface never blocks the Workflow core chain.

## Validation

- GUI–CLI equivalence tests for each mutation class; resolved-context display correctness;
  Draft-versus-Active Skill separation.
- Standard offline/static gates.

## Out of scope

Workflow editing/run control (Subplans 6–7), OrchestrationPolicy editing beyond Preferences
(Subplan 11 surfaces candidates).
