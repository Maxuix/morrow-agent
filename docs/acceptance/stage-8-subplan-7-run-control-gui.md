# Stage 8 Subplan 7 — Run Control GUI

- Date: 2026-09-04/05
- Branch: `feat/stage8-run-control-gui` (base `aadcfc1`, latest verified `main`)
- No dependency, Live Provider, MCP or external-network test was added or run; the browser
  evidence uses the loopback smoke server with scripted providers and a throwaway state root.

## Delivered

- **Run-control actions in GUI and CLI parity**: Start (existing editor freeze + run), Pause,
  Resume, Cancel (`morrow workflow cancel` added; GUI two-step confirm), Resolve Approval (GUI
  dialog + `morrow approval list/resolve` added), Retry failed node (explicit root resume +
  `run_relation=rerun` child), Full rerun (new accounting root, labelled in both surfaces), Edit
  pending graph (Pause → PatchEditor → preview → confirm → continuation child), Accept/Correct
  TaskOutcome (`task accept` / `task resume` on both surfaces).
- **§8.5 approval surface**: the approval projection carries requesting Task/Workflow/Node/Agent,
  operation type (tool + effect class), deterministic risk level, affected objects (redacted
  arguments, file-mutation and config-mutation evidence), requested/granted scope and the bounded
  redacted preview. Decisions are `allow_once` / `deny` / `allow_session`; the session choice is
  refused for high-risk operations and records a durable `session:<effect>:<tool>` granted scope;
  later same-scope executions in the same session get an auditable pre-resolved approval row at
  creation and never park. An `approval.resolved` event lets clients drop entries immediately.
- **Patch preview**: `POST /v1/patches/validate` returns a structural diff (added/removed/changed
  nodes and edges, required-output and budget changes) plus the C8 risk classification
  (`application/workflows/patch_preview.py`, shared by GUI and CLI): elevated on node removal,
  review/test-gate removal, contract relaxation, report-dependency removal, control-edge removal
  (compared against base bindings), writer reorder, required-output retarget, conversation-scope
  change, provider/model boundary change, cap/deadline relaxation, permission widening or role
  replacement.
- **Cost feedback (§14.1–14.2)**: `pre_run_summary` (node count, providers/models, explicit limits
  or none, parallelism, writer nodes) on every run view and pre-start at
  `GET /v1/catalog/workflow-revisions/{id}/run-preview`; per-node durable request counts on node
  views against the effective cap; run and lineage request counts as before.
- **GUI workflow panel upgrades**: run lineage labels with parent links, superseded badge, the
  control row (per-status enablement matrix mirroring the server), pre-run summary line, per-node
  usage in NodeDetail, inherited Artifact provenance list.

## Deterministic contract evidence

- `tests/test_stage8_run_control.py` (15 tests):
  - C8 risk matrix: low-risk text-only change; elevated node removal, cap raise/removal (and the
    tightening low-risk adjacent), required-output retarget, provider/model boundary, control-edge
    removal, writer reorder, report-dependency removal, contract relaxation; diff correctness.
  - Approval surface: enriched wire fields with the redaction boundary asserted; `allow_session`
    records the scope and the second same-session/same-scope call is approved durably at creation
    without parking; decision/flag mismatch is a 400; session scope is refused for high-risk
    operations (unit boundary plus server path).
  - Usage/cost projections: run-preview endpoint (404 on unknown revision), per-node counts summing
    to the run count, pre-run summary on the run view.
  - Patch validate through the API returns diff + risk (low and elevated).
  - GUI–CLI OCC conflict on the TaskOutcome accept surface in both directions: first writer
    commits, loser gets `stale`/409, the task closes exactly once.
- Existing matrices stay green: `test_stage8_core_api.py` (20), `test_stage8_pause_drain.py` +
  `test_stage8_patch_continuation.py` (21), `test_stage8_workflow_editor.py`, CLI suites.

## Browser acceptance

Scripted loopback server (`scripts/gui_smoke_server.py`, fixed smoke token, throwaway Chrome via
the browser skill; smoke budget raised to 7200s so manual sessions do not hit the seeded deadline):

1. Run panel shows the pre-run summary, budget and the correct per-status control enablement.
2. Pause during an in-flight approval → `draining` ("正在暂停"), never blocked; approval dialog
   shows the full §8.5 surface (请求方/工作流/节点/任务/操作类型/风险/范围/受影响对象/脱敏预览);
   允许一次 resolves it live and the run settles `paused` with gamma completed, alpha queued.
3. 编辑待定节点 → Past node locked, alpha edited → 预览补丁 shows 校验通过/低风险/diff/执行节点/
   继承 Past 节点 → 确认应用补丁 → continuation child starts `running`; the parent shows
   terminal `superseded` with the 已被取代 badge; the child completes with the inherited
   `gamma.result` Artifact and lineage accounting (谱系累计 3/10).
4. 完整重跑 on the completed child → explicit root resume + new run labelled
   "新预算根，不继承先前节点输出" (fresh 2/10 budget, `重跑 ← parent` lineage).
5. Denial semantics: 拒绝 records the denial and the run continues honestly to completion.
6. Core restart: server restarted serve-only over the same state root; the GUI reconnects
   (已连接) and shows the survived run state unchanged.
7. The expired-deadline patch apply was refused with `deadline_exceeded` (first seed session) —
   the explicit-limit guard surfaces as an actionable GUI error.

Real issues found and fixed during the browser run: the React Flow pane covered the PatchEditor
action bar (layout containment + stacking fix); the run panel only read the root task from the
event store so retry/rerun stayed disabled (fetch fallback added); the root-resume pre-step missed
`ready_for_acceptance` (runtime requires an OPEN root for rerun children).

## Validation gates

| Gate | Result |
|---|---|
| Focused run-control matrix | 15 passed |
| Full offline `uv run pytest -m 'not live'` | 1631 passed / 2 Live deselected |
| Ruff format --check / ruff check | PASS |
| compileall + `morrow --help` smoke | PASS |
| GUI `pnpm typecheck` / `pnpm test` | PASS / 81 tests (11 files) |
| GUI `pnpm build` (bundle budget) | PASS |
| Browser flows | 7 scenarios above, all PASS |
| `git diff --check` | PASS |

## Scope closure

Remaining per the subplan's out-of-scope list: automatic Draft generation (Subplan 8),
Agent-proposed Replan (Subplan 9), feedback capture UI (Subplan 11). Session-scoped approvals
apply within one execution session; cross-session or run-wide exemption policies are not part of
Stage 8.
