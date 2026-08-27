# Subplan 88 — S7P-07 Runtime Control: Steering and Follow-Up

> Status: active on `feat/s7p-07-runtime-control`; activated 2026-08-27 from local `main`
> at `7b52f5f` after Subplan 85 fast-forward integration.
> User decisions frozen 2026-08-27: scope unified (retry belongs to S7P-06); v1 steering waits
> for admitted-batch closure; `FinishReason.STEERED` additive enum approved.

## 1. Objective

Give a running Direct Agent bounded, durable runtime control: the user can steer an in-flight run
at a safe point and queue follow-up input for the next turn, without breaking
tool_call/tool_result pairing, cancellation, recovery, or the Session-owned `ConversationLog`
boundary. After Subplan 87 removed the runtime outcome gate, steering is the only mid-run
correction channel; without it a drifting long-horizon run can only be cancelled.

### Frozen scope decisions

1. **Scope split is final.** Provider retry (ModelErrorCode classification, bounded exponential
   backoff, Retry-After, budget coupling) is delivered by Subplan 85 (S7P-06). S7P-07 implements no
   retry logic. The original checklist's retry acceptance items
   (`docs/research/Stage 7 前 Direct Agent 可靠性修复清单.md` §S7P-07) are owned by the S7P-06
   acceptance gate; S7P-07 adds only retry×control interaction regressions.
2. **Steering waits for admitted-batch closure.** Verified as exact Pi behavior, not a deviation:
   Pi's `executeToolCalls` runs the whole admitted batch and polls steering only between turns
   (see §2). v1 never skips remaining calls inside an admitted batch and never synthesizes
   cancelled results for skipped calls.
3. **Steering delivery is a turn boundary.** `ConversationLog` forbids a user message inside an
   active turn (`runtime/conversation.py:192-194`), and a STOP terminal requires a final no-tools
   assistant message (`runtime/conversation.py:179-180`). Steering therefore closes the current
   turn with the new additive `FinishReason.STEERED` (user-approved) and is immediately resubmitted
   as the next durable user turn inside the same TaskRun. Model-visible effect is identical to Pi's
   in-loop user-message injection.
4. **S7P-06 ownership is separate.** This subplan must not modify S7P-06's retry, compaction,
   truncation, loop-default or policy-version code paths except at the documented consumption
   seams (§4). Any defect found there is reported, not silently fixed.

## 2. Reference contract (Phase A pinning completed during preparation)

Pinned Pi Agent 0.84.2 at commit `209bc7b9a89b01c8fd05861cf5bbdda3e300037a`
(repo `earendil-works/pi`). Sources verified during subplan preparation:

| Contract | Pinned Pi evidence | Observed behavior | Morrow owner / seam | Class |
|---|---|---|---|---|
| Steering drain points | [`runLoop`](https://raw.githubusercontent.com/earendil-works/pi/209bc7b9a89b01c8fd05861cf5bbdda3e300037a/packages/agent/src/agent-loop.ts) (`getSteeringMessages` poll at loop start, line ~166, and after each completed turn before the next LLM call) | Steering is polled at run start and after the full tool batch plus `shouldStopAfterTurn`; injected as `role:"user"` messages before the next assistant response. | `AgentLoop.run_task` loop top (`agent.py:1163`), post-batch point next to `_host_stop_requested` (`agent.py:1699`), pre-final-STOP check (`agent.py:1450`) | Adapt (turn boundary, §3) |
| Batch non-interruption | [`executeToolCalls` / `executeToolCallsSequential`](https://raw.githubusercontent.com/earendil-works/pi/209bc7b9a89b01c8fd05861cf5bbdda3e300037a/packages/agent/src/agent-loop.ts) (line ~411-487) | The admitted batch runs to completion; only `signal.aborted` breaks the sequential loop early. Steering never interrupts a batch. | `agent.py:1598-1677` sequential batch; per-call cancellation check at `agent.py:1599` stays cancel-only | Adopt |
| Follow-up drain point | [`runLoop` outer loop](https://raw.githubusercontent.com/earendil-works/pi/209bc7b9a89b01c8fd05861cf5bbdda3e300037a/packages/agent/src/agent-loop.ts) (lines ~263-270) | Follow-ups are drained only when the agent would stop (no tool calls, no steering pending); the same run continues with them as user messages. | Orchestrator drain after normal `FinishReason.STOP` completion (`orchestrator.py:136-154`), resubmit as next turn | Adapt (next durable turn, §3) |
| Queue modes | [`PendingMessageQueue`](https://raw.githubusercontent.com/earendil-works/pi/209bc7b9a89b01c8fd05861cf5bbdda3e300037a/packages/agent/src/agent.ts) (lines 125-157; defaults at 231-232) | FIFO; modes `all` or `one-at-a-time`; **default `one-at-a-time`** for both queues. | Runtime-control queue drains one entry per delivery point; no `all` mode in v1 | Adopt (default only) |
| Session queue API | [`steer` / `followUp`](https://raw.githubusercontent.com/earendil-works/pi/209bc7b9a89b01c8fd05861cf5bbdda3e300037a/packages/coding-agent/src/core/agent-session.ts) (lines 1350, 1370, 1387-1411) | `steer()` queues while running and errors on extension commands; `followUp()` queues until the agent has no more tool calls or steering. Queue display state is UI-only. | Orchestrator/API `steer()` / `follow_up()` entrypoints; headless surface from S7P-01 | Adapt |
| Steering survives abort | `runLoop` initial poll (line ~166); [`continue()`](https://raw.githubusercontent.com/earendil-works/pi/209bc7b9a89b01c8fd05861cf5bbdda3e300037a/packages/agent/src/agent.ts) (lines 372-384) | Messages queued before/during an aborted run are polled at the next run start; steering drains before follow-ups. | Pending queue entries survive cancel; loop-top check delivers them on the next run | Adopt |
| Queue persistence | Pi queues are in-memory (`PendingMessageQueue`, session display copies at agent-session.ts:321-323) | Pi loses pending queues on process crash. | Morrow queue is durable (operational store) so crash/recovery neither loses nor duplicates entries | Harden |

`Defer` rows (Pi TUI key bindings, images, extension-command expansion, `all` queue mode) are out
of scope; the terminal phase pins the exact Pi TUI input mapping before choosing keys (§5 step 7).

## 3. Located Morrow facts (verified on the S7P-06 implementation tree)

| Fact | Consequence |
|---|---|
| Main loop is `while True` at `runtime/agent.py:1163`; per iteration: cancellation check, v1 budget checks, prompt refresh, context build + compaction (`:1199`), model call | Steering safe-point check installs at loop top and pre-model-request, exactly like Pi's poll. |
| Final tool-free STOP commits at `agent.py:1445-1477` (`append_assistant`, `finish_turn(STOP)`, `turn.completed`) | The pre-commit steering check at `:1450` defers the final commit when steering is pending; the turn instead closes `STEERED`. |
| Sequential batch at `agent.py:1598-1677` with per-call cancellation at `:1599`; post-batch host hook at `:1699-1709` (`FinishReason.CANCELLED` + `status.changed` source marker) | The steering drain point sits next to the host hook; the `STEERED` close mirrors this established graceful-close pattern. |
| `_host_stop_requested` (`agent.py:546-572`) is injected via constructor (`agent.py:501`) | The runtime-control port follows the same injection pattern: an optional `runtime_control` collaborator on `AgentLoop`, composed by the orchestrator/bootstrap. |
| `ConversationLog` rejects a user message inside an active turn (`runtime/conversation.py:192-194`) and STOP without final assistant (`:179-180`); `FinishReason` = STOP/CANCELLED/ERROR (`core/models.py:467-470`) | Steering delivery must close the current turn first; `FinishReason.STEERED` is additive and needs read-compat handling in replay/backup/doctor/terminal display. |
| `turn_lifecycle.py:320-358` reopens a `READY_FOR_ACCEPTANCE` TaskRun as `ordinary_follow_up`; `tasks.py:269-288` performs the transition; `DurableTurn.client_message_id` exists (`turn_lifecycle.py:347`) | Steering and follow-up resubmission reuse this exact path: same TaskRun, new durable Turn, idempotent by `client_message_id`. No new turn-entry mechanism. |
| Orchestrator drives runs at `application/orchestrator.py:105-154` (probe → prepare → `runtime.run_turn`), recovery resume at `:168-195` | The follow-up drain loop and steering resubmission live here; `resume_recovery` naturally picks up surviving queue entries at loop top. |
| Terminal blocks during a run: `await dispatch_task` with Ctrl+C cancel only (`interfaces/terminal.py:213-221`) | Terminal phase adds non-blocking in-run input; Ctrl+C semantics unchanged. |
| Latest migration is `migrations_v21_retry_progress.py`; `observability_journal.py:120` pins v21 | The runtime-control queue uses the next additive migration (v22 at preparation time; use the next free number at activation). |
| Retry backoff sleeps through `self.retry_sleep` (`agent.py:513`, `:665`), with S7P-06 abort-on-cancel semantics | Steering during backoff is not delivered mid-call; it takes effect at the next safe point. Cancel during backoff stays immediate (S7P-06 contract; regression-tested here). |

## 4. Frozen design decisions

### Durable runtime-control queue

- One bounded table in the operational store: `(workspace_id, session_id, position, kind,
  client_message_id, text, status, created_at, consumed_at)` with `kind ∈ {steer, follow_up}` and
  `status ∈ {pending, consumed, superseded}`. Bounds: at most 32 pending entries per session, text
  ≤ 4096 characters; overflow rejects the newest entry with a surfaced reason, never a silent
  drop. User text here has the same secrecy class as the conversation journal; no command output,
  tool arguments, secrets, reasoning or tracebacks are stored.
- Every entry carries a fresh `cmsg` id at enqueue time; delivery submits with that id, so a
  crash between turn-close and resubmission, or a duplicated drain, cannot double-submit.
- Pending entries survive cancel, crash and recovery. Consumption is marked in the same
  transaction as the durable turn submission that delivers the entry.
- Explicit cancel supersedes nothing by default: pending steering is delivered at the next run
  start (Pi parity), pending follow-up remains queued until drained or the session is reset.

### Steering delivery

- `AgentLoop` gains one optional `runtime_control` port (constructor-injected, like
  `should_stop_after_turn`). At each safe point — loop top (`agent.py:1163`), after admitted-batch
  closure next to the host hook (`:1699`), and before the final STOP commit (`:1450`) — the loop
  asks the port for at most one pending steering entry (`one-at-a-time`).
- On delivery the loop closes the current turn with `FinishReason.STEERED` (no interrupted calls;
  pairing already closed), emits the existing `status.changed` + `turn.completed` lifecycle with
  the steered reason, marks the queue entry consumed, and returns. The orchestrator immediately
  resubmits the steering text as the next user turn in the same TaskRun through the normal
  probe → prepare → `run_turn` path. No second ConversationLog writer; no system side-projection.
- Steering while no run is active is rejected by the entrypoint (the caller submits a normal turn
  instead). Steering never interrupts an in-flight tool, never reorders results, never fabricates
  a result, and is not delivered in the middle of a model stream or retry backoff.
- A steering delivery with an empty v1/v2 policy distinction: safe points are policy-independent,
  so steering works for v1-resume and v2 runs alike; no v1 budget semantics change.

### Follow-up drain

- While a run is active, `follow_up()` appends a `kind=follow_up` entry. After a run completes
  normally (`FinishReason.STOP`, no pending steering), the orchestrator drains one entry
  (`one-at-a-time`), marks it consumed, and submits it as the next user turn through the existing
  `ordinary_follow_up` transition. This repeats until the queue is empty; each drained entry is a
  full durable turn with its own preparation and observability.
- Follow-up while the session is idle routes to an ordinary submission, not the queue.
- A run that ends in error, cancel or host stop does not auto-drain follow-ups; they remain
  pending for the next user-initiated run (surfaced in the terminal as queued).

### FinishReason.STEERED (user-approved additive enum)

- Add `STEERED = "steered"` to `FinishReason`. Rules mirror CANCELLED where the contract is
  shared: no final assistant required, no interrupted call IDs allowed, legal only after all
  admitted calls are closed.
- Read compatibility: old rows never contain it; new rows with `steered` must round-trip through
  snapshot/backup/restore/doctor without being rewritten, and the terminal renders it as a
  distinct bounded label (not "已取消"). Unknown-enum handling for out-of-tree readers is
  documented in the acceptance doc.
- No new event types and no new payload fields; `turn.completed` carries the existing finish
  reason field with the new value.

### Control × retry × cancel interaction

- Cancel during S7P-06 retry backoff remains immediate (existing contract); add an explicit
  regression that a `retry_sleep` in progress is aborted by cancellation.
- Steering submitted during backoff or mid-stream is delivered only at the next safe point; it
  neither aborts the backoff nor replays the failed attempt.
- Steering, follow-up drain, cancel, host stop and recovery resume each end with legal
  tool_call/tool_result pairing and a legal ConversationLog shape; extend the existing pairing
  test matrix rather than creating a parallel one.

### Delivery layers and boundaries

- Phase 1: orchestrator/headless `steer()` / `follow_up()` entrypoints (the S7P-01 headless
  surface consumes them) with scripted-Provider closure of the full acceptance matrix.
- Phase 2 (same subplan): terminal non-blocking in-run input wired to the same entrypoints.
  The exact key/prefix mapping is pinned from the Pi TUI sources at the same commit before
  implementation (no guessed bindings); Ctrl+C cancel semantics are unchanged.
- No new third-party dependency; bundled runtime-policy defaults unchanged; no live
  Provider/Pi/MCP/network/credential test. Bounded observability counters (steered turns,
  drained follow-ups) may be added to terminal AgentRun metrics inside the same additive
  migration; no message text in metrics.

## 5. Test-first implementation sequence

1. Restate the S7P-07 acceptance checklist with retry items explicitly marked as owned by the
   S7P-06 gate; copy the §2 parity table into
   `docs/acceptance/s7p-07-runtime-control.md` as the checked-in Phase A evidence.
2. Failing queue/migration tests: FIFO order, 32-entry/4 KiB bounds and reject-newest policy,
   `client_message_id` dedup, crash/restart survival, consume-in-submission-transaction, v22
   additive migration round-trip, backup/doctor compatibility (extend
   `tests/test_operational_store.py`, `tests/test_stage4_journal.py`,
   `tests/test_stage4_recovery.py`).
3. Failing `FinishReason.STEERED` contract tests: validator legality (closed batch required, no
   interrupted IDs, no final assistant required), snapshot/replay round-trip, terminal label,
   unknown-value read tolerance (`tests/test_agent_tool_loop.py` plus the conversation/durable-log
   suites).
4. Failing AgentLoop safe-point tests: steering delivered only at the three documented points;
   in-flight tool never interrupted; pre-STOP deferral produces `STEERED` then a new user turn;
   `one-at-a-time` drain; steering while idle rejected; steering queued before an aborted run is
   delivered at the next run start (scripted providers/fake clocks in
   `tests/test_agent_tool_loop.py` and a new `tests/test_runtime_control.py`).
5. Failing interaction tests: cancel during scripted retry backoff is immediate; steering during
   backoff waits for the safe point; cancel with pending follow-up keeps the queue; recovery
   resume neither loses nor repeats entries; every path keeps pairing legal.
6. Implement the v22 queue, the `runtime_control` port and AgentLoop safe-point consumption, the
   orchestrator `steer()`/`follow_up()` entrypoints and the follow-up drain loop through
   `ordinary_follow_up`.
7. Terminal phase: pin the Pi TUI in-run input mapping from the pinned commit, then implement
   non-blocking in-run input in `interfaces/terminal.py` wired to the same entrypoints.
8. Scripted acceptance cases (no live model): steer mid-run corrects the next model request;
   follow-ups drain in order as durable turns; cancel-then-resume preserves pairing and queue;
   backoff interruption; steering at run start. Publish the acceptance document, update
   architecture and terminal docs, update execution state, run focused plus repository-wide
   offline gates.

## 6. Validation

```bash
uv run pytest -q tests/test_runtime_control.py tests/test_agent_tool_loop.py
uv run pytest -q tests/test_context_runtime.py tests/test_conversation_log.py
uv run pytest -q tests/test_operational_store.py tests/test_stage4_recovery.py tests/test_stage4_journal.py
uv run pytest -q tests/test_stage3_product_acceptance.py tests/test_code_agent_mini_eval.py
uv run pytest -m 'not live'
uv run ruff format --check .
uv run ruff check .
uv run python -m compileall -q src tests
uv run morrow --help
uv run morrow run --help
git diff --check
```

Adjust test paths to the actual suite names at activation. Same sandbox fallback rule as
Subplan 85 (`.venv/bin/...` equivalents with the restriction recorded). No live
Provider/model/Pi/MCP/network/credential test is allowed.

## 7. Completion, review and integration

- Dedicated topic branch `feat/s7p-07-runtime-control` from the latest verified `main` after
  Subplan 85 integrates; small coherent commits; no S7P-06 ownership overlap (§1.4).
- One read-only `gpt-5.6-luna` / `max` review subagent over the complete base...HEAD diff,
  focused on: pairing legality under every control path, queue loss/duplication across crash
  windows, ConversationLog single-writer proof, `STEERED` read compatibility (backup/replay/
  doctor/terminal), steering reaching model context only through the normal audit chain,
  cancel/backoff races, and tests that only prove mocks.
- Reproduce and fix confirmed findings, rerun affected and full offline gates, then follow the
  standard root-task fast-forward integration and clean resource retirement. S7P-08 is not
  started automatically.
