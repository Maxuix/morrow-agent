# Subplan 10 — Context, Learning and Skill Management GUI

> Status: completed 2026-09-05; verified and integrated into local main
> Branch: `feat/stage8-context-learning-skill-gui` (merged and retired)
> Activation base: verified `main` at `672ba90` (Subplans 1–9 integrated)
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

## Implementation calibration

- The current generic Preference model has no dedicated language/verbosity slots. The bar links to
  the actual frozen rules instead of inferring values from free text.
- Knowledge creation/replacement keeps the existing candidate/evidence promotion authority; GUI
  exposes candidate edit-and-accept plus conflict resolution, with no unsupported direct writer.
- Skill Update selects an already installed immutable version. Existing `skill install` imports
  packages; generated packages require Draft review, publication and a separate Enable action.
- Management queries are read-only, including expired candidates. Source/decision history is
  retained; candidate, Knowledge and Draft pages are bounded and navigable.

## Completion evidence

- Full offline: 1709 passed, 2 existing host-only skips, 2 Live deselected; all 30 management tests passed.
- GUI: typecheck, 93 tests, budgeted build and loopback browser acceptance passed.
- Ruff format/check, compileall, CLI/manage help, whitespace and 18 packaged GUI assets verified.
- Implementation `5cd8ed3`, final refresh/acceptance `1e7c215`; fast-forwarded to local main.
  Topic ancestry difference was zero before deletion; no extra worktree was created.
- Acceptance: `docs/acceptance/stage-8-subplan-10-context-learning-skill-gui.md`.
- Remote push/Live tests remain unauthorized. Subplan 11 remains pending activation.
