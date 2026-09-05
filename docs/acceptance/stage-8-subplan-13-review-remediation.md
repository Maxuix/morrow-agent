# Stage 8 Subplan 13 — Review Remediation

Date: 2026-09-05. Base: verified local main `429d812`.
Branch: `fix/stage8-subplans-8-12-review`.
Status: implementation committed and validation passed; local integration pending.

## Delivered behavior

All seven findings from [the Subplans 8–12 review](../reviews/stage-8-subplans-8-12-code-review.md)
have corresponding code changes and regression coverage:

- Removed or retargeted input bindings require Replan approval even for generic TextResult
  evidence; additive bindings can remain low risk.
- Controlled cancellation after sibling failure closes admitted leaf Tasks/Turns in both
  foreground and Server modes. Actual driver loss still preserves recovery facts.
- Serial fallback honors pending Replan signals and preserves queued work through Pause and
  approved continuation.
- GraphPlanner reads current Profile constraints before classification and before saving,
  remerging classification against current explicit constraints without another model request.
- Planner and feedback/evaluation share existing canonical role node IDs. Non-role IDs retain
  the historical definition-origin fallback. No frozen Revision schema/hash migration is needed.
- Learning merges pagination across all three candidate lists, retaining its offset cursor
  convention. Orchestration-only history pages remain accessible after other lists are exhausted.
- Settings show actual promoted types and resolved, saved auto-run/Replan eligibility separately.
  Promotion alone does not imply authorization; explicit wildcard low-risk Replan remains visible.

Implementation commits: `072fd27`, `64cb4d8`, `1d5a4af`, `6a27dc2`.
No dependency, bundled runtime policy default, public event lifecycle or chat-history ownership
change was required.

## Validation

- Replan/run-control suites: 43 passed.
- Parallel/Pause/serial-scheduler suites: 67 passed, including foreground/Server cancellation
  and fallback signal-to-continuation cases.
- Final planner/feedback/context suites: 74 passed, including Profile changes across an Event-
  synchronized classifier await, fallback roles, pagination and promotion/authorization changes.
- GUI typecheck passed; 16 test files / 100 tests passed; production build and bundle budget passed.
  JS 513.9 KiB / 700 KiB budget; CSS 44.6 KiB / 120 KiB. Vite's advisory 500 kB chunk warning
  remains; the project budget gate passes.
- Ruff check and format passed (635 Python files); compileall, CLI help and whitespace passed.
- Full offline gate: 1761 passed, 2 existing host-level Seatbelt tests skipped, 2 Live tests
  deselected; exit 0 in 266.32 seconds.

Python commands use `UV_CACHE_DIR=/private/tmp/morrow-review-uv` and `uv run --offline` because
the default uv cache is not writable in this sandbox. Regressions use scripted Providers and
Events, without timing sleeps. No Live tests, real credentials or browser acceptance were used.

## Integration

Local fast-forward integration and topic branch retirement are next; validation is complete.
Remote push remains unauthorized; no remote fetch/push was performed.
