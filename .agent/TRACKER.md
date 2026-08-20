# Progress Tracker

## Current status

The user authorized a complete executable Stage 5 plan. Subplan 52 is merged and Subplan 53 is now
active on `feat/stage5-memory-selection`.

## Last completed task

Subplan 52 is verified and merged at `7dfe5af`, including its one-time Grok review-fix pass. It adds
prepared configuration OCC, recoverable YAML promotion/undo, Preference/Profile policy, provenance,
recovery, Session projections, and CLI/REPL controls. Subplan 51 is verified and merged at `548f0bc`,
including its one-time Grok review-fix pass. It adds
the v11 Inbox decision boundary, SQLite Project Knowledge promotion/lifecycle, bounded CLI/REPL
surfaces, and lazy query expiry. Subplan 50 is merged at `4d47be8` underneath it.

## Active task

Implement S53.3: bounded deterministic Project Knowledge selection, ranking, diversity, reasons,
and digests.

## Next action

Build the selector as an independent bounded projection over current Knowledge and the v12 term
surface; keep AgentRun freeze, ContextBuilder integration, Reviewer, and history search out of this
task.

## Blockers

No Stage 5 implementation blocker. Remote publication remains outside the current commit-only
request.

Remote publication is outside the current commit-only request. After this plan is committed and
fast-forwarded, local `main` will be one planning commit ahead of `origin/main` until push is
explicitly authorized.

The two Stage 5 research discussions are untracked user files. They are preserved as input and are
not part of the implementation plan changes unless the user explicitly requests that they be
adopted into version control.

## Active boundary

- Stage 5 planning is authorized; Subplan 53 is active from verified local `main` after Subplan 52.
- First release exposes only `off` and `review_only`; `explicit_auto` remains closed.
- Automatic trigger is accepted TaskOutcome only; no `completed`/`corrected` Task status is added.
- No background worker, natural-language acceptance, embedding/vector dependency, Skill write, or
  Workflow activation is authorized.
- Profile/Preferences remain YAML authorities; Project Knowledge and learning audit records use the
  shared Operational Store.
- S51.1 is verified: v11 upgrade/rollback, decision/Knowledge/memory persistence, corruption and
  reserved Saga constraint tests passed on `feat/stage5-inbox-knowledge`.
- S51.2 is implemented and focused query/preview tests pass; the legacy API methods remain
  compatible while new typed view entry points live under `api.learning` and `api.memory`.
- S51.3–S51.6 are implemented: candidate decisions/rejection/suppression/expiry, Project Knowledge
  promotion and lifecycle, replay/OCC/workspace isolation, and preview-confirm CLI/REPL boundaries.
- S53.1 is implemented on the active branch: v12 MemoryQuery/Selection contracts, immutable
  selection/item and rebuildable-term tables, repository codecs/guards, v11 upgrade/future/
  corruption coverage, and legacy migration expectation updates. Focused validation passed with
  83 tests, Ruff format/check, compileall, and `git diff --check`.
- S53.2 is implemented: pure bounded mixed-language/code/path tokenizer, deterministic term
  generation, transactional rebuild/clear on Project Knowledge promotion and lifecycle changes,
  and bounded lexical candidate retrieval. Full offline validation passed with 760 tests, 2 skips,
  1 deselection, repository-wide Ruff format/check, compileall, and `git diff --check`.
- S52 is complete: prepared configuration revisions/digests, YAML promotion Saga, Preference/
  Profile whitelist, foreground recovery, activation provenance/undo, Session projection updates,
  recoverable after-state finalization, explicit REPL global scope, and activation memory events.
  Grok reported 15 findings; five real recovery/OCC defects and feasible presentation/recovery gaps
  were independently fixed once. Final offline gate: 752 passed, 2 skipped, 1 deselected; no second
  review was run.
- S51 Grok review found four confirmed issues; all four plus edited-payload duplicate/suppression
  revalidation and replay/presentation polish were independently fixed once. The final gate passed
  739 tests, Ruff, compileall, CLI help, and `git diff --check`; no second review was run.
- AgentLoop and ConversationLog ownership, public runtime events, bundled capability policy, and
  credentials remain unchanged.
