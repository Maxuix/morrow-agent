# Stage 8 Subplan 6 — Workflow Editor and Agent Module Inspector

Date: 2026-09-04. Branch `feat/stage8-editor`, based on local `main@df7ae4f`. No dependency,
Live Provider, MCP, external-network or credential test was added or run.

## Delivered

- Operational Store v27 adds a bounded durable `WorkflowDraft` with source/head baselines,
  diagnostics, timestamps, terminal Revision reference and OCC `row_version`. Create, update,
  revalidate, reject and freeze use the Core Host command path and idempotent receipts.
- Every edit invokes the existing pure Workflow Compiler. Edits and canvas movement create zero
  Revisions; freeze alone writes desired source and invokes the existing sole publication service.
  Publication-commit/API-receipt crash recovery converges from the lower-level publication receipt.
- Compiler diagnostics retain existing error-code compatibility and add optional `node_id` /
  `edge_id` locators. Removed outputs, broken bindings, edge direction and contract mismatches now
  have actionable messages and exact graph locations.
- The React Flow editor supports add/delete/replace, control edges, input bindings, output
  contracts, final output, node task/access/conversation/tool overrides and optional budget fields.
  Semantic edits debounce for 400ms through the Core API; node dragging is local canvas state.
  Invalid Drafts disable Freeze, stale head/Catalog facts are explicit, and durable Drafts reopen
  after refresh. Running/historical node statuses are locked by the editor contract.
- Agent Module Inspector edits only existing publishable Definition fields, separates desired-source
  save from immutable Version publication, offers Provider/Model/Skill/Tool Catalog pickers, and
  shows deterministic Definition diffs. Built-ins copy to a new user ID with verified parent
  source/hash and parent Version when available; credential and approval-boundary fields cannot be
  smuggled through the strict API schema.

## Deterministic contract evidence

`tests/test_stage8_workflow_editor.py` covers legal Draft creation with zero Revisions during edit,
located invalid deletion, blocked invalid freeze, OCC conflict, repair/freeze/replay, Agent copy and
provenance mismatch, strict security-field rejection, Agent disablement during edit, idempotent
revalidation, and recovery after publication committed before the API receipt. Existing Stage 7
compiler/AgentDefinition suites confirm backward-compatible hashes, diagnostics and publication.

Frontend Vitest covers source construction, clone isolation, Revision normalization, deterministic
path diffs, admitted-node locking and authenticated POST/PUT JSON calls. TypeScript remains strict.

## Browser acceptance

The loopback scripted server and dedicated throwaway Chrome profile passed 26/26 checks. The flow
ran the seeded legal Workflow to completion, opened the editor, cloned a published Workflow into a
durable valid Draft, froze an immutable Revision, reopened another Draft, deliberately deleted a
node without guessed reconnection, observed a located compiler error and disabled Freeze, then
verified offline text and Core-restart resync without lost or duplicated session state. Screenshots
were captured in the disposable smoke directory; no user browser profile or real provider was used.

The browser run also exposed and fixed one render loop: a default object identity caused React Flow
nodes to be reset on every render after Draft creation. The default is now stable, and the final
production bundle completed the full flow.

## Validation gates

| Gate | Result |
|---|---|
| Focused Draft/Agent matrix | 46 passed |
| Compiler/editor regression | 33 passed |
| Full offline pytest | 1613 passed, 2 deselected |
| Ruff format/check | 601 files formatted; all checks passed |
| Compileall / CLI help / git diff check | PASS |
| GUI | typecheck PASS; 37/37 Vitest; build and bundle budget PASS |
| Browser | 26/26 checks PASS |

## Scope closure

Run-control buttons remain Subplan 7. GraphPlanner Draft generation, global Replan, management GUI,
feedback/evaluation and read-only parallelism remain their later subplans. No second compiler,
publication path, credential surface or generalized command framework was introduced.
