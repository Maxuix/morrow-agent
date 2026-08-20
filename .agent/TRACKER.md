# Progress Tracker

## Current status

The user authorized a complete executable Stage 5 plan. The master plan and Subplans 49–54 are
defined; Subplan 51 is active on `feat/stage5-inbox-knowledge` from verified local `main`.

## Last completed task

Subplan 50 is verified and merged at `4d47be8`, including its one-time Grok review-fix pass.
Subplan 49's bounded Core Learning/v10 foundation and shared repositories remain integrated
underneath the active Inbox/Project Knowledge slice.

## Active task

Implement S51.3: complete immutable candidate rejection, exact/key suppression, lazy/batch expiry,
receipts/events, replay, and concurrency behavior on top of the verified v11 boundary.

## Next action

Finish the focused Learning decision child service, validate stale/replay/expiry paths, then proceed
to Project Knowledge promotion; keep Preference/Profile YAML promotion closed until Subplan 52.

## Blockers

No Stage 5 implementation blocker.

Remote publication is outside the current commit-only request. After this plan is committed and
fast-forwarded, local `main` will be one planning commit ahead of `origin/main` until push is
explicitly authorized.

The two Stage 5 research discussions are untracked user files. They are preserved as input and are
not part of the implementation plan changes unless the user explicitly requests that they be
adopted into version control.

## Active boundary

- Stage 5 planning is authorized; Subplan 51 is active on its dedicated branch from verified local
  `main` at `4d47be8`.
- First release exposes only `off` and `review_only`; `explicit_auto` remains closed.
- Automatic trigger is accepted TaskOutcome only; no `completed`/`corrected` Task status is added.
- No background worker, natural-language acceptance, embedding/vector dependency, Skill write, or
  Workflow activation is authorized.
- Profile/Preferences remain YAML authorities; Project Knowledge and learning audit records use the
  shared Operational Store.
- S51.1 is verified: v11 upgrade/rollback, decision/Knowledge/memory persistence, corruption and
  reserved Saga constraint tests pass on `feat/stage5-inbox-knowledge`.
- S51.2 is implemented and focused query/preview tests pass; the legacy API methods remain
  compatible while new typed view entry points live under `api.learning` and `api.memory`.
- AgentLoop and ConversationLog ownership, public runtime events, bundled capability policy, and
  credentials remain unchanged.
