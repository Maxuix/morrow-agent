# Subplan 24 — Handoff Removal Acceptance and Delivery

> Status: completed
> Depends on: Subplans 21–23

## Goal

Prove on the final integrated tree that Handoff is absent from every current source,
package, product, command, configuration, and documentation surface; legacy files are
untouched; ordinary Stage 2 behavior has not regressed; and no Stage 3/4/5 capability has
entered as a replacement.

## Executable tasks

### HR.24.1 — Complete the requirement-to-evidence matrix

- Complete `docs/acceptance/handoff-removal-evidence.md` with exact tests, commands,
  artifacts, and observed results for every master-plan definition-of-done branch.
- Link the final reference-classification artifact and resolve every current-to-remove row.
- Distinguish mandatory offline/package/product evidence from optional Live checks.
- Record the accepted Stage 2 baseline and final tree identity.

### HR.24.2 — Accept the real offline product lifecycle

- Exercise production `run_repl` and composition with an offline Scripted Provider, using
  the established Stage 2 product-acceptance pattern rather than a mocked command handler.
- Cover one integrated scenario containing:
  - ordinary chat, a deterministic tool failure, model recovery, and a healthy follow-up;
  - `/handoff` and `/continue` returning the ordinary unknown-command response;
  - dirty `/new` confirmed discard with no `complete()` call;
  - dirty `/exit` confirmed discard with no workspace write;
  - cancellation and EOF-during-confirmation outcomes with exact exit/reset behavior;
  - valid/corrupt/future legacy primary/backup sentinels remaining unchanged.
- Prove no supported Handoff help, prompt, status, action, model-context field, or state
  publication appears.
- Prove no credential, raw tool argument/result, call ID, traceback, reasoning, SDK object,
  or unrelated project-file content leaks.

### HR.24.3 — Accept degraded state and legacy-data boundaries

- Create valid, corrupt, and future-version legacy primary/backup sentinels before startup.
- Prove every byte remains unchanged after startup, chat, supported config writes, Profile
  writes, `/new`, `/exit`, and package-installed CLI startup.
- Pair byte sentinels with source scans and a fail-on-Handoff-access store spy so “ignored”
  proves no read attempt, not merely no mutation.
- Prove legacy files never trigger read-only mode or block Profile/Preferences writes.
- Separately prove corrupt/future Profile causes workspace read-only and corrupt Preferences
  isolates only the affected Preferences layer.
- Re-run Profile/Preferences revision, tombstone, backup, recovery/failure, concurrency,
  relink, and workspace-lock acceptance.

### HR.24.4 — Accept Agent core regression surface

- Re-run ConversationLog grammar, context projection/reduction, Provider wire,
  ToolExecutor, AgentLoop budgets/deadlines/cancellation/retry/loop, event lifecycle, and
  terminal segmentation suites.
- Prove structured/config calls exclude ToolMessage envelopes and intermediate tool-call
  Assistant messages.
- Prove Session reset preserves the legal process-local history contract and
  ConversationLog remains the only chat-history writer.

### HR.24.5 — Accept source, package, documentation, and capability boundaries

- Repeat Subplan 22 precise source/test scans; review the explicit allowlist for dedicated
  legacy sentinel assertions rather than assuming a raw word-count zero.
- Reconcile every remaining docs/`.agent/` occurrence against
  `handoff-reference-classification.md`.
- Build a fresh wheel, inspect inventory, install it into a fresh environment, import
  Morrow, load bundled policy, and run `morrow --help`.
- Prove no Handoff module, public model, command, config schema/route, state API, or package
  resource is installed.
- Re-run capability scans proving no persistent Session, summary pipeline, local project
  tool, approval system, MCP, Skill, browser/network tool, or background task entered.

### HR.24.6 — Run final quality gates and close the plan

- Run the full offline suite and strict collection, and report the newly observed counts.
  Do not use the previous 308-pass count as a minimum or inferred regression floor because
  Handoff-specific tests are intentionally deleted or retargeted.
- Run Ruff format/lint, compileall, CLI smoke, product/boundary tests, package checks,
  precise source scans, Markdown/reference audit, and `git diff --check`.
- Record optional Live status truthfully; credential absence is not a pass or a blocker.
- Reconcile README, ARCHITECTURE, ROADMAP, both acceptance artifacts, PLAN, TODO, TRACKER,
  and LOG.
- Mark the plan complete only when no mandatory branch or unclassified occurrence remains.

## Mandatory final gates

```bash
uv run pytest -m 'not live' -q
uv run pytest --collect-only -q
uv run ruff format --check .
uv run ruff check .
uv run python -m compileall -q src tests
uv run morrow --help
git diff --check
```

Package acceptance uses a newly built artifact and fresh environment, and verifies bundled
`agent-policy.toml` discovery. The evidence records the new final counts as observed data.

## Completion criteria

- Every master-plan definition-of-done clause maps to direct green evidence.
- No mandatory test, product, state, source, package, documentation, or boundary gate is
  missing, unexpectedly skipped, or inferred.
- Optional Live status is accurate.
- Current code/package/product surfaces are Handoff-free; remaining textual occurrences are
  classified historical, legacy-data sentinel, or unrelated uses.
- Legacy primary/backup bytes are unchanged and ignored.
- Stage 3 remains unstarted and Stage 4 remains unimplemented.
- Execution-state documents are reconciled and the active plan is complete.
