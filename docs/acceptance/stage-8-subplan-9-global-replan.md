# Stage 8 Subplan 9 — Global Future-Only Replan

Date: 2026-09-05. Activation base: verified local `main` at `a799982`.
Implementation branch: `feat/stage8-global-replan`, using the existing checkout.

## Delivered behavior

- A bounded typed `ReplanRequest` travels on the internal `submit_node_result` mechanism.
  Generic TextResult leaves may submit a signal without structured output slots. The leaf's
  terminal transaction durably records its own immutable `ReplanSignal`; it never edits a Revision.
- Both authoritative admission and queued-to-running persistence check unconsumed signals.
  A crash after leaf closure but before NodeRun projection cannot admit the next node. The
  Coordinator consumes evidence only after its NodeRun settles, then requests Pause/Drain.
  User cancellation retains priority and cannot start a continuation child.
- ReplanCoordinator is the sole automatic proposer. It converts bounded future task corrections
  and dependency additions into an exact-source `FutureGraphPatch`. Invalid/conflicting signals
  become auditable invalid proposals. Compilation/publication remain with the existing owners.
- The only handoff is `PatchApplicationService` with Compiler and OCC/CAS, on a fully paused
  parent with no Active nodes. Parent supersession, child creation, root handoff and the proposal
  decision share one transaction; API command receipts share that transaction as well.
- The deterministic classifier derives risk from actual source/compiled differences. Guardrail,
  role, contract, report/control dependency, Writer order, required output, permission, scope,
  model/data-boundary and unclassifiable changes cannot silently become low risk.
- `approval_only` queues proposals. An explicit user wildcard `allow_low_risk` policy can apply
  only low-risk changes, rechecking policy and Compiler at application. Policy resolution reuses
  deterministic task classification from the immutable root TaskContract; task-specific promotion
  remains approval-only until paired Direct/Multi evidence exists.
- Blocked/outcome-unknown parents produce proposals only. Stale bases produce conflict history
  and are never replayed on a new Revision. Compile failure records a failed proposal and starts
  no child. Rejection leaves the parent paused.
- API, CLI and GUI use the same Coordinator. They expose exact before/after source, structural
  diff, risk reasons, policy revision, actor, decision and child history. GUI disables approval
  for stale or ineligible parents. Existing server Supervisor drives applied children; CLI
  foreground execution follows automatic children through the existing Scheduler.
- Store schema v28 owns signal/proposal persistence. Doctor checks identity, consumption and
  child lineage. Recovery reconstructs current or exact legacy frozen mechanism tools before
  checking their digest; pre-Replan runs remain recoverable.

## Deterministic acceptance

The focused Stage 7/8 scheduler, Pause/Drain, continuation, run control, Core API and new Replan
matrix passed **110 tests in 20.61 seconds** on the final application code.

| Case | Evidence in `tests/test_stage8_global_replan.py` |
|---|---|
| Same low-risk patch under both modes | Pending under approval-only; automatic application and durable audit under explicit low-risk policy |
| Durable signal/admission boundary | Real leaf submission, injected crash before NodeRun projection, admission refusal and restart settlement |
| Cancel during signal settlement | Parent cancels and no automatic child is created |
| C8 escalation | Parameterized guardrail/permission/contract/edge/role/scope/model/output differences remain non-low |
| Past and compile constraints | Past edits rejected; invalid candidate cannot publish or start a child |
| Atomic decision/handoff | Injected transaction failure rolls back decision and child together |
| Stale proposal | Conflicting base produces conflict history; no replay or second child |
| Blocked outcome | Proposal persists, application refuses until Recovery resolves the parent |
| Policy promotion | Task-specific policy overrides wildcard resolution but stays approval-only without evidence |
| Automatic supervision | Real server composition starts and completes the child and emits existing run events |
| API receipt parity | Authenticated decision replay returns the same child without duplicate application |
| CLI and integrity | CLI projects stored history; Doctor detects tampered proposal scope |
| Frozen recovery | Pre-Replan generic leaf recovers with its original ToolSet digest |

An additional exact comparison against `a799982` confirmed identical legacy tool definitions
for all four historical structured result contracts.

## GUI, static and package verification

- `pnpm --dir gui typecheck`: passed.
- `pnpm --dir gui test`: **88 passed**, including three Replan panel tests.
- `pnpm --dir gui build`: passed; JS **474.4 KiB / 700 KiB**, CSS **41.3 KiB / 120 KiB**;
  all assets **915.6 KiB**.
- Ruff format/check, Python compileall, `morrow --help`, `morrow workflow replan --help`, and
  `git diff --check`: passed.
- `uv build --offline`: built sdist and wheel with existing cached build requirements;
  wheel inspection confirmed all **18 GUI asset entries**, including index and JavaScript.

Browser acceptance used the built GUI with real local Core and scripted Providers from
`scripts/gui_replan_smoke_server.py`, isolated under `/tmp/morrow-replan-smoke-2` on loopback.

1. Inspected pending low-risk and elevated proposals, including exact cap change `10 → 999`.
2. Approved the low-risk proposal; parent superseded, child completed and history recorded user.
3. Confirmed the remaining proposal became stale and its approval button was disabled.
4. Rejected another proposal and confirmed its parent remained paused.
5. Inspected an automatic application, policy revision, Coordinator actor and completed child.
6. Reloaded/reopened and confirmed durable history and child linkage remained visible.
7. Inspected screenshots at 1280×720 and browser warning/error logs (empty).

The browser tab and fixture server were closed. No real Provider, MCP service, credentials or
external-network test was used.

## Full regression and local integration

Final full offline gate: **1679 passed, 2 skipped, 2 Live deselected in 183.56 seconds**, exit 0.
Command: `UV_CACHE_DIR=/tmp/morrow-uv-cache uv run --offline pytest -m 'not live' -q`.
Output: `/tmp/morrow-subplan9-complete-gate.log`. Both skips are existing real Seatbelt host-level
tests that intentionally skip inside the nested Codex sandbox. No failing tests remain.

Earlier complete attempts exposed only migration-name expectations and an old hard-coded future
schema version of 28. Expectations now include v28 and the future-version rejection uses the
supported version plus one. A subsequent full gate passed 1677 tests; the final gate above also
includes the cancellation and task-policy precedence regressions added afterwards.

Verified work is ready for fast-forward local integration; the final Git closure is recorded
in the execution log.
Remote push and Subplan 10 activation are outside this delivery's authorization.
