# Progress Tracker

## Current status

The user authorized a complete executable Stage 5 plan. The master plan and Subplans 49–54 are
defined; Subplan 51 is complete and Subplan 52 is ready to activate from verified local `main`.

## Last completed task

Subplan 51 is verified and merged at `548f0bc`, including its one-time Grok review-fix pass. It adds
the v11 Inbox decision boundary, SQLite Project Knowledge promotion/lifecycle, bounded CLI/REPL
surfaces, and lazy query expiry. Subplan 50 is merged at `4d47be8` underneath it.

## Active task

Activate Subplan 52: implement the Profile/Preferences Promotion Saga without changing YAML
authority or opening `explicit_auto`.

## Next action

Create `feat/stage5-configuration-promotion` from verified local `main`, read the active Subplan 52,
then inspect ConfigPatchService and its revision/digest write boundary before changing code.

## Blockers

No Stage 5 implementation blocker.

Remote publication is outside the current commit-only request. After this plan is committed and
fast-forwarded, local `main` will be one planning commit ahead of `origin/main` until push is
explicitly authorized.

The two Stage 5 research discussions are untracked user files. They are preserved as input and are
not part of the implementation plan changes unless the user explicitly requests that they be
adopted into version control.

## Active boundary

- Stage 5 planning is authorized; Subplan 52 is next from verified local `main` after Subplan 51.
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
- S51 Grok review found four confirmed issues; all four plus edited-payload duplicate/suppression
  revalidation and replay/presentation polish were independently fixed once. The final gate passed
  739 tests, Ruff, compileall, CLI help, and `git diff --check`; no second review was run.
- AgentLoop and ConversationLog ownership, public runtime events, bundled capability policy, and
  credentials remain unchanged.
