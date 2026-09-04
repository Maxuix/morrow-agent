# Stage 8 Subplan 8 — Task-specialized GraphPlanner Draft

Date: 2026-09-05. Activation base: verified local `main` at `e320f51`.
Implementation branch: `feat/stage8-graph-planner`.

## Delivered behavior

- Local English/Chinese task features, preserved user roles/scope/constraints, one optional
  schema-constrained no-tool Provider classification, and one optional bounded read-only Scout.
- Bounded Catalog projections reuse enabled, unrevoked Agent heads, exact available models and
  enabled Skills, existing Artifact contracts and actual tool authority. No second registry,
  arbitrary executable graph language, new dependency or runtime-policy default change.
- Direct-first composition creates ordinary editable Workflow Drafts. Larger implementations add
  review/planning; independent research scopes become read-only evidence branches with a synthesis
  join. The current Scheduler still executes serially. Node outputs use `TextResult@1/result`.
- Explicit Workflow/node request caps and admission timeouts are inherited or narrowed. Omitted
  limits remain absent. One compile regeneration is allowed, followed by a compiled Direct
  fallback or concrete `needs_input` diagnostics. Nothing uncompiled is saved or started.
- Global/workspace `OrchestrationPolicy` shares Extension YAML ownership, OCC and backup paths.
  Workspace policies fully override global matches. Empty orchestration preserves legacy digests;
  Skill/MCP edits are not overwritten. Model preferences select existing authorized Agent versions.
- API, CLI and GUI share the same services. Read-only classification awaits outside the mutation
  bus; save rechecks current Catalogs/policy. Stable Draft/command identities permit durable retry.
- Structured explanations survive reopen and show when the original graph has since been edited.
  GUI supports task entry, existing Draft editing/freezing and orchestration preference settings.
- `auto_run_eligible` remains false, including when a policy requests `allow_promoted` or supplies
  arbitrary evidence IDs. Users can freeze and run valid generated Drafts normally. Replan,
  task-class promotion and parallel scheduling remain later subplans.

## Deterministic acceptance

`tests/test_stage8_graph_planner.py`: **23 passed** on the final application code (120.18 seconds).

| Case | Evidence |
|---|---|
| Small task remains Direct | One node; no implicit cap, publication or run; durable reopen/edit |
| Different tasks produce different graphs | Three-area implementation has 3 nodes; five-area implementation has 4; research has scoped branches and a synthesis join |
| Explicit guardrail and roles | Cap forces Direct or a concrete conflict; excluded Reviewer stays absent |
| Policy precedence and OCC | Workspace override, stale document rejection, shared Skill/MCP preservation |
| Model classification | One scripted no-tool request; durable replay skips Provider; invalid/extra fields do not leak |
| Authority checks | Unauthorized/unavailable model, disabled Agent/Skill and missing Catalog reject selection; enabled exact Skill version works |
| Explicit task facts | Read-only constraints override write classification; a model cannot enlarge an explicit small scope |
| Scout | Exactly one bounded directory request; only known project markers returned |
| Compile failure | Exactly two multi-node compile attempts, then one validated Direct fallback |
| Core responsiveness | Event-controlled stalled classifier does not prevent policy mutation; save sees the new policy |
| Command integrity | Conflicting command identity rejected before classification; sensitive input rejected |
| Internal error boundary | Unexpected Catalog exceptions become the existing generic API error, never a planning explanation or saved Draft |
| CLI parity | Policy show/set uses the same Extension revision/OCC contract |
| Manual run remains available | Generated Draft freezes and completes through the existing real Scheduler using scripted leaves |

## GUI and static verification

- `pnpm --dir gui typecheck`: passed.
- `pnpm --dir gui test`: **85 passed** across 12 files.
- `pnpm --dir gui build`: passed, including bundle budget (JS 468.8 KiB / 700 KiB;
  CSS 41.0 KiB / 120 KiB; all assets 909.6 KiB).
- Ruff format/check, Python compileall, `morrow --help`, and whitespace diff checks: passed.

Browser acceptance used the built GUI and the real local Core with scripted Providers from
`scripts/gui_planner_smoke_server.py`, isolated under `/tmp/morrow-planner-smoke-9` on loopback.
No real Provider, MCP service, credentials or external network were used.

1. Entered a one-file typo task and generated a one-node Direct Draft with no implicit cap.
2. Edited its name, saved and froze it using the existing editor; no automatic run was started.
3. Saved workspace `allow_promoted` preference; the UI still explained manual confirmation.
4. Generated a broad refactor with Explorer, Planner, Coder and Reviewer; inspected bindings and
   the single writing node in the ordinary graph editor.
5. Reloaded and reopened the Draft; task features, original explanation and policy revision survived.
6. Inspected screenshots for the Direct and multi-node layouts; checked browser error logs (empty).
   Closed the browser tab and stopped the fixture afterwards.

## Backend regression verification

Final full offline gate: **1653 passed, 2 skipped, 2 Live deselected** in **290.61 seconds**.
Command: `UV_CACHE_DIR=/tmp/morrow-uv-cache uv run pytest -m 'not live' -q -x --tb=short
--junitxml=/tmp/morrow-stage8-verified-junit.xml`. Exit status: **0**. No failing tests remain.

Both skips are existing real Seatbelt host-level tests, which intentionally skip inside the nested
Codex sandbox. No Live Provider/MCP/network test ran.

Related integration matrix: **100 passed** (332.32 seconds), covering `test_stage8_core_api.py`,
`test_stage8_core_api_security.py`, `test_stage8_workflow_editor.py`, `test_stage8_run_control.py`,
`test_stage7_agent_definitions.py` and `test_skill_bindings.py`.

Recovery matrix after the fixture adjustment: **13 passed** (99.10 seconds), including all four
formerly timed-out subprocess cases.
Workspace matrix: **35 passed** (109.55 seconds), including all four workspace subprocess cases.
Ledger acceptance matrix: **3 passed** (67.89 seconds) after the collection-timeout adjustment.
Operational Store matrix: **33 passed** (144.07 seconds), including every subprocess lock,
migration and concurrent-backup test after unifying its setup waits and cleanup. An earlier full
run exposed two of these old 10-second startup windows; both failures were reproduced in isolation
before the fixture fixes. Holders now await explicit parent release, preserving the intended race.

The first full run completed with **1639 passed, 8 failed, 2 skipped, 2 Live deselected** in
1779.14 seconds. All eight failures were existing subprocess-fixture setup timeouts: four Stage 4
crash fixtures had not exited within 10 seconds (`exitcode is None`, reproduced inside and outside
the sandbox), and four workspace tests timed out waiting for child-ready barriers. The fixtures
now allow 60 seconds for spawn/setup and clean up failed children. Workspace mutations require the
start event; exact crash exit codes, recovery classifications, writer exclusion and one-winner OCC
assertions are unchanged. No wall-clock sleep was added.

A subsequent run was stopped after 70 passes when the existing
ledger selector-collection subprocess exceeded its 30-second import window; standalone reproduction
confirmed TimeoutExpired. That setup-only timeout is now 120 seconds, with selector validity
assertions unchanged.

That full run completed with **1651 passed, 2 failed, 2 skipped, 2 Live deselected** (1384.03 seconds).
Its only failures were the two Operational Store setup timeouts described above, both covered by
the passing 33-test module rerun. The successful final full gate above ran after every fixture fix.

## Publication boundary

Verified implementation and acceptance were fast-forwarded into local `main` through `d98933b`.
The topic had zero commits absent from main and was deleted; the existing workspace is retained.
Remote push is not authorized by the active master plan. Completion uses verified local `main`;
remote divergence is recorded explicitly. Subplan 9 is not activated by this delivery.
