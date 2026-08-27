# Subplan 85 — S7P-06 Pi-Parity Long-Horizon Loop, Context Compaction and Retry

> Status: planned, pending activation
> Priority: P0; depends on the integrated S7P-01, S7P-02 and S7P-05 foundations
> Planning base: local `main` at `1d3ba3706b1ac82cf0b82b56ea207bd2b548090f`
> Behavioral reference: `@earendil-works/pi-coding-agent` 0.84.2, repository commit
> `209bc7b9a89b01c8fd05861cf5bbdda3e300037a`
> Source authority: current user decision (“Pi Agent 项目怎么干就怎么干”); pinned Pi source and
> documentation; current Morrow code. This decision supersedes the earlier frozen 30 → 60 budget,
> deterministic-only summary and no-progress-stop proposal.

## 1. Objective

Replace Morrow's demo-sized cumulative task ceilings with Pi Agent's long-horizon execution model:
the agent continues while the model produces tool calls, ends normally when the model produces no
more tool calls, compacts context when the model window requires it, and retries transient provider
failures using Pi's policy. There is no default product stop based on total tool rounds, total model
requests, total tool calls, whole-task wall time, repetition or inferred “no progress”.

This is behavioral parity, not a request to copy Pi's TypeScript architecture. Morrow keeps its
existing permission, tool-protocol, cancellation, durability, redaction and Session-owned
`ConversationLog` invariants. A Morrow-specific difference is allowed only when one of those
boundaries requires it; every difference must be documented with a focused test and must not
silently reintroduce a task-lifetime budget.

S7P-06 also replaces character-count context deletion with Pi-style token-window compaction and
brings Pi-style provider retry into this subplan, because compaction overflow recovery and retry are
one long-running-session contract. The retry portion is removed from S7P-07; S7P-07 remains the
later steering/follow-up/runtime-control subplan.

## 2. Reference contract

### 2.1 Pinned Pi authority

Implementation must first pin executable behavior from the reference revision, rather than rely on
memory or latest-branch drift:

- Agent loop:
  `packages/agent/src/agent-loop.ts` at commit
  `209bc7b9a89b01c8fd05861cf5bbdda3e300037a`.
- Session compaction, overflow recovery and retry:
  `packages/coding-agent/src/core/agent-session.ts` at the same commit.
- Compaction implementation and summary format: the corresponding pinned
  `packages/coding-agent/src/core/compaction/` sources.
- Tool-result truncation: the pinned coding-agent tool sources and truncation helpers.
- User-facing defaults: the version-matched settings and compaction documentation. Latest docs may
  be used only to locate behavior; pinned source wins if they differ.

Before production edits, publish a small checked-in parity table in the acceptance document with
the exact source symbol/line, observed Pi behavior and planned Morrow location. At minimum it must
prove:

1. Pi's loop has no default cumulative turn/model-request/tool-call/whole-task deadline.
2. Pi continues after a tool-using assistant turn and stops after a tool-free assistant turn,
   abort/error, or an optional host `shouldStopAfterTurn` decision.
3. Automatic compaction is triggered by model context-window pressure using token estimates,
   reserve tokens and a recent-context tail; repeated compaction chains from prior summaries.
4. Compaction uses an LLM-generated structured summary, persists a compaction entry, respects tool
   call/result boundaries and has one overflow-recovery path.
5. Retry defaults, exponential delays, provider `retry-after` handling/cap, reset-after-success and
   user-abort behavior match the pinned Pi revision.
6. Tool outputs use Pi's exact head/tail, byte/line and continuation behavior for the corresponding
   read/search/command tool family. Do not freeze guessed constants in this plan.

### 2.2 Adoption rule

Classify every reference behavior before implementation:

- **Adopt:** identical observable behavior is compatible with Morrow's invariants.
- **Adapt:** the behavior is preserved but implemented through Morrow ports, immutable run state,
  safe projections or durable records.
- **Harden:** only content safety, path confinement, permission, legal tool pairing, cancellation or
  crash recovery requires a stricter boundary. Hardening may redact or fail closed; it may not add
  an ordinary-task stop heuristic.
- **Defer:** Pi steering/follow-up/UI behavior belongs to S7P-07. Deferral must not weaken the loop,
  compaction or retry behavior delivered here.

Unexplained deviations fail acceptance. “Safer” is not sufficient justification for a new default
cumulative cap or no-progress kill.

## 3. Located Morrow facts and superseded assumptions

| Current fact | Consequence |
|---|---|
| Bundled defaults are `max_tool_rounds=30`, `max_model_attempts=40`, `max_tool_calls=128`, `max_run_seconds=1800`; code ceilings are 100/120/512/3600 | These are active task-lifetime limits and must not govern new Pi-parity runs. Raising one number does not solve the contract. |
| `AgentLoop` checks model-attempt exhaustion before tool-round exhaustion | The old 60-round proposal was internally unreachable with the 40-attempt default. Its planned comparison cannot establish long-horizon support. |
| `max_tool_calls_per_cycle=32` rejects a provider response before executing all calls | Pi parity requires processing the valid tool-call batch; this behavioral cap is retired for new runs. |
| `loop_detection_enabled=true` terminates repeated cycle signatures | Pi has no default repeated/no-progress kill. Existing loop telemetry may remain observational, but it cannot terminate a new default run. |
| Context sizing is character-based and unknown models fall back to 160,000 chars | Pi compaction is model-window/token based. New runs require an exact model context window and a Pi-equivalent estimator fallback, not model-name guessing or char ratios. |
| Compression clears old tool results and drops turns/cycles without a semantic summary | Replace the model-visible projection with Pi-style summary + recent tail; never erase the immutable durable conversation/tool history. |
| `model_retry_limit=1` is coupled to the attempt budget | Replace it with Pi's retry state machine and defaults, independent of any cumulative model-attempt budget. |
| Morrow already has cancellation, per-operation tool timeouts, permission checks, legal ToolCycle/history rules and durable run state | Preserve these execution boundaries. A per-operation timeout is not a whole-task deadline and must be modeled separately. |
| S7P-01 observability records request/tool trajectories and terminal outcomes | Retain/extend bounded metrics for diagnosis, but metrics do not become a no-progress judge. |

The earlier plan's following decisions are explicitly void: `max_tool_rounds=60` as the product
default; a fixed 30-vs-60 product-budget experiment; deterministic-only `WorkSummary`; a
three-cycle diagnosis/`NO_PROGRESS` stop; and keeping the other cumulative caps unchanged.

## 4. Frozen design decisions

### 4.1 Pi-parity loop and terminal conditions

- A new default run has no cumulative limit on total model requests, tool rounds, tool calls or
  whole-task elapsed time. Do not replace the current limits with larger sentinel values.
- Continue the inner loop whenever the completed assistant response contains valid tool calls or
  queued input requires another turn. A tool-free completed assistant response is the ordinary
  successful stop, matching Pi.
- Terminal paths are limited to: normal model stop; explicit user/host abort; unrecoverable provider
  failure after Pi-style retries; unrecoverable context overflow after the one Pi-style recovery;
  fatal tool-protocol/history/durability/internal failure; permission refusal where current Morrow
  policy requires termination; or an explicitly supplied host `shouldStopAfterTurn` equivalent.
- The host stop hook is absent by default, checked at the same turn boundary as Pi, externally
  attributable and covered by tests. It is not a bundled timeout or hidden scheduler budget.
- `LOOP_DETECTED`, `MODEL_CALL_LIMIT`, round/total `TOOL_CALL_LIMIT` and `RUN_TIMEOUT` remain
  readable for historical v1 runs and stored data. New default v2 runs do not emit them for
  cumulative/repetition conditions. Do not add `NO_PROGRESS`.
- Per-tool/provider payload validity, permission and local operation timeout boundaries remain.
  They report their actual failure and may be recoverable by the model; they are not counted into a
  synthetic whole-task cap.

### 4.2 Runtime policy v2 and compatibility

- Introduce an explicit v2 agent-run policy/snapshot contract rather than weakening validation of
  v1 fields or encoding “unlimited” as a huge integer. New runs use v2; persisted in-flight v1 runs
  resume with their frozen v1 limits and terminal semantics.
- Retire active v2 enforcement of `max_tool_rounds`, `max_model_attempts`, `max_tool_calls`, the
  behavioral `max_tool_calls_per_cycle`, `max_run_seconds`, loop-stop parameters and the old
  `model_retry_limit`. Retire char/ratio context fields after token-window migration.
- Keep bounded fields that serve a different purpose: per-operation timeouts, permission/effect
  policy, safe output truncation, validation-display bounds and persistence/event size bounds.
  Rename or separate ambiguous fields so they cannot be mistaken for a task deadline.
- Old snapshots/config data remain strict and readable. A user-owned config containing legacy
  task-lifetime overrides must receive an explicit migration error/instruction for new v2 runs; it
  must not be silently ignored or silently converted to a bundled default. Embedders that need a
  limit use the optional host stop hook, analogous to Pi.
- Record `policy_schema_version` and the presence/source of a host stop in run evidence. Do not
  persist executable callbacks or expose new raw payload fields.

### 4.3 Exact model window and token accounting

- Extend exact provider/model capability discovery with `context_window_tokens`. The value must come
  from a registered/verified capability, never model-name guessing. If unavailable, fail before a
  long tool run with a bounded configuration error instead of applying a small unknown-model char
  fallback.
- Use provider-reported usage when available and Pi's estimator behavior when it is not. Freeze in
  the parity table which message parts/tool schemas are counted and how images/non-text content are
  represented.
- Record the accounting basis (`provider_usage` or the named estimator), estimated/current tokens,
  context window, reserve, keep-recent target and compaction decision as bounded observability.
  Estimates are operational evidence, not billing truth.
- Automatic compaction threshold follows Pi exactly:
  `contextTokens > contextWindow - reserveTokens`, with Pi's version-matched default reserve and
  recent-tail token targets unless pinned source proves different values.

### 4.4 Pi-style compaction and immutable history

- The authoritative `ConversationLog` and durable tool-cycle records remain complete and immutable.
  Compaction changes only the model-visible context projection.
- Persist a separate immutable compaction/checkpoint entry containing at minimum: generated
  structured summary, first retained durable position, tokens before compaction, model/prompt
  identity, accounting basis, compaction request usage/cost when available, cumulative read/modified
  file lists, source ranges and integrity hashes. It is not a second chat-history writer.
- Build subsequent model context as stable system/tool prompt + latest compaction summary + retained
  recent tail. Repeated compaction summarizes from the prior summary plus newly compacted content,
  matching Pi's chained behavior.
- Never split a tool call from its result. Match Pi's turn-boundary and split-turn behavior for an
  oversized current turn. Resume/recovery must rebuild the exact same projection from durable
  records and must not repeat compacted-away work merely because it is absent from the recent tail.
- Use an LLM-generated structured summary with Pi's sections: Goal; Constraints & Preferences;
  Progress (Done/In Progress/Blocked); Key Decisions; Next Steps; Critical Context; Files Read; Files
  Modified. Preserve Pi's cumulative file tracking and optional user compaction instructions.
- The summary is a non-authoritative memory aid. It may contain only the safe projection already
  eligible for model context. It must not expose hidden reasoning, credentials, protected content,
  raw SDK objects, tracebacks or unrestricted full tool arguments/results. A summary provider
  failure leaves durable history intact and follows Pi's failure/recovery behavior.
- Support automatic threshold compaction, one automatic overflow recovery and an idle manual
  `/compact [instructions]` equivalent. The manual operation cannot race an active run.

### 4.5 Pi-style provider retry

- Implement the pinned Pi retry state machine and defaults exactly, including enabled default,
  maximum retry count, exponential base delay, provider-supplied delay handling/cap, retryable
  status/error classification, retry-state notifications and reset after a successful response.
- Ensure only one layer owns retries. Configure the provider SDK/adapter consistently with the
  pinned Pi behavior so hidden SDK retries cannot multiply Agent-level attempts.
- Abort/cancel interrupts backoff immediately. Fake clocks drive tests; no wall-clock sleeps.
- A context-length failure routes through the single compaction-overflow recovery before final
  failure; it is not repeatedly treated as an ordinary transient retry.
- Retrying a model request does not duplicate committed assistant messages, tool calls/results,
  user messages, usage or durable attempt state.

### 4.6 Pi-style tool-output truncation

- Match the pinned Pi tool family behavior and exact limits discovered in Phase A. Read/search-like
  output keeps the same end Pi keeps; command output keeps the same head/tail Pi keeps; limits are
  evaluated using Pi-equivalent bytes/lines, not the old request-char ratios.
- A truncated result includes Pi-equivalent continuation guidance and a safe Artifact reference so
  the agent can fetch omitted data without injecting it all into context. Artifact retention is
  bounded and subject to existing redaction/path policy.
- Truncation is model-context shaping, not deletion of durable execution evidence and not a reason
  to terminate the task.

### 4.7 Evaluation watchdog is outside the product loop

- Offline tests and later same-model Pi/Morrow evaluations need a finite harness watchdog. Apply the
  same declared watchdog to both systems through the evaluation runner/process boundary, not
  `AgentLoop` or runtime-policy defaults.
- A watchdog expiry is recorded as evaluator outcome `watchdog_timeout` with the manifest value; it
  is not an `AgentStopCode`, model stop or evidence that the product should use that timeout.
- The manifest also records model, provider, reference revision, retry/compaction settings and
  environment. Compare completion, cost/tokens, elapsed time, compactions, retries, invalid calls
  and evaluator watchdogs together.
- No live Provider/Pi run is authorized in S7P-06. Build scripted parity fixtures here; execute the
  same-model Pi/Morrow A/B only in S7P-09 or after separate explicit authorization and credentials.

## 5. Ordered execution

### Phase A — Freeze the executable Pi parity table

1. Create the topic branch from a verified clean latest `main`; activate this subplan in the
   execution-state files and record the exact base/reference commits.
2. Inspect the pinned Pi loop, session, compaction, retry and truncation sources. Publish the parity
   table with exact defaults, constants, state transitions, terminal cases and source permalinks.
3. Turn the table into scripted black-box Pi-shaped fixtures/traces for: more than the current round
   and call caps; repeated identical cycles followed by success; compaction chaining; overflow
   recovery; transient retries; provider retry delay; abort during backoff; and tool truncation.
4. Inventory every Morrow use of the v1 cumulative/char/loop fields, including snapshot,
   observability, migration, CLI/config and test factories. Record the v2 compatibility map before
   changing production behavior.

### Phase B — Runtime policy v2 and uncapped loop (test-first)

5. Add failing strict-model/config/snapshot tests for v2 new-run behavior, v1 resume behavior,
   explicit legacy override migration errors and historical stop-code reads.
6. Add failing AgentLoop parity tests proving a scripted run can exceed 120 model cycles, 512 total
   tool calls and 3,600 seconds of fake monotonic time, and can repeat identical cycles before a
   later successful tool-free model stop. No cumulative/loop stop code may appear.
7. Implement the tagged v2 policy and prepared-run/snapshot plumbing. Remove v2 cumulative checks
   and default loop termination; preserve v1 frozen execution for recovered old runs.
8. Add the absent-by-default host `shouldStopAfterTurn` equivalent and cancellation tests. Prove it
   is checked only at the reference boundary, is externally attributable and leaves legal history.
9. Separate per-operation timeout/output/persistence bounds from task lifetime. Preserve exact tool
   failure recovery, permissions and multi-call ordering without a per-cycle behavioral ceiling.

### Phase C — Token-window context and compaction (test-first)

10. Add exact-model capability and token-accounting tests for provider usage, estimator fallback,
    unknown context window, tool schemas, tool results and non-text content.
11. Add failing compaction fixtures matching pinned Pi: threshold boundary, reserve/recent targets,
    normal turn boundary, oversized split turn, tool call/result indivisibility, cumulative file
    lists, repeated compaction chaining and manual instructions.
12. Implement the structured compaction prompt/result schema and model request. Validate bounded
    structure, preserve safe model-visible facts and fail closed on unsafe/unparseable output
    without mutating the durable log.
13. Persist immutable compaction entries and rebuild the summary + recent-tail projection across
    restart. Prove recovery yields the same hashes/retained boundary and avoids repeating a prior
    compacted-away tool operation.
14. Implement automatic threshold compaction, one context-overflow recovery and the idle manual
    compact operation. Prove abort/error/race paths leave valid conversation/tool pairing.

### Phase D — Retry and tool-output parity (test-first)

15. Add a table-driven retry matrix from pinned Pi: each retryable/non-retryable class, attempt
    count, exponential delays, provider delay/cap, success reset, exhaustion, context overflow and
    abort during fake-clock backoff.
16. Implement one retry owner in the session/provider boundary and bounded retry observability.
    Prove no message/tool/usage duplication across attempts or restart.
17. Add exact truncation fixtures for each corresponding tool family, including UTF-8 byte/line
    boundaries, command head/tail behavior, continuation notice, Artifact access and redaction.
18. Replace the old request-char-ratio shaping with the pinned Pi-equivalent truncation path for v2;
    retain v1 read/resume compatibility.

### Phase E — Offline long-horizon and regression evidence

19. Run the scripted parity suite with external fake-clock/process watchdogs. Include successful
    tasks beyond every retired ceiling, repeated/no-progress-looking stretches that later recover,
    multiple compactions and transient provider failure.
20. Assert trajectory and terminal evidence: normal model stop remains normal; evaluator watchdog
    remains external; no hidden cumulative limit, loop kill or `NO_PROGRESS` emission appears.
21. Run recovery/cancellation/tool-protocol/history tests across each phase boundary, including
    crash after summary persistence, during retry backoff and between parallel tool results.
22. Run the full S7P-01…S7P-05 regression and repository offline/static/CLI gates. Do not run a live
    model, Pi, MCP, network or credential test.

### Phase F — Documentation, review and closeout

23. Publish `docs/acceptance/s7p-06-pi-parity-long-horizon.md` with the parity table, deviations,
    scripted evidence, v1/v2 migration, metrics and unresolved live A/B destination. Update
    `docs/ARCHITECTURE.md`, human runtime-policy/config documentation and the Stage 7 research
    checklist because the current user decision supersedes its 60-round/no-progress design.
24. Run the required read-only implementation review, fix every reproduced finding, rerun affected
    and full gates, make coherent commits and update execution state. Root performs integration and
    resource retirement under the repository Git rules; S7P-07 is not started automatically.

## 6. Required test matrices

- **Loop:** tool-free normal stop; tool continuation; queued-input continuation seam; user/host
  abort; host stop hook; fatal provider/protocol/durability paths; >120 model cycles; >512 calls;
  fake elapsed >3,600 seconds; repeated identical/varying sterile cycles followed by success;
  multiple calls in one assistant turn.
- **Policy/migration:** strict v2 new run; exact v1 resume; old snapshot/row/stop-code reads; explicit
  legacy user override error; no huge-number sentinel; no default host hook; per-operation timeout
  remains distinct and finite.
- **Token accounting:** exact context window; provider usage; estimator; threshold just below/at/
  above boundary; tool schema/results; Unicode/non-text inputs; missing capability failure.
- **Compaction:** structured sections; safe input projection; recent tail; split turn; tool-pair
  integrity; cumulative files; chained summary; manual instructions; summary failure; one overflow
  recovery; deterministic durable rebuild; restart no-repeat; bounded persistence.
- **Retry:** pinned defaults and attempts; every retryable/non-retryable class; exponential and
  provider-directed delay; cap; reset; exhaustion; abort; overflow routing; no double retry; no
  duplicated conversation/tool/usage state.
- **Truncation:** exact pinned constants and head/tail choices; byte and line boundary; UTF-8;
  continuation; safe Artifact reference; redaction; durable evidence remains available.
- **Evaluation:** identical external watchdog for Pi/Morrow arms; watchdog outcome is evaluator-only;
  manifest completeness; no product policy mutation; no live run in this subplan.
- **Safety/durability:** Session remains the only chat-history writer; no reasoning, secrets, full
  unsafe arguments/results, SDK objects or tracebacks in events/YAML/DB/terminal; cancellation and
  every failure retain legal tool pairing and continuable durable state.

## 7. Validation

The implementation task refines focused paths after Phase A, then runs at least:

```bash
uv run pytest -q tests/test_policy.py tests/test_agent_limits.py
uv run pytest -q tests/test_agent_tool_loop.py tests/test_context_runtime.py
uv run pytest -q tests/test_agent_run_observability.py tests/test_operational_store.py
uv run pytest -q tests/test_stage4_recovery.py tests/test_stage4_journal.py
uv run pytest -q tests/test_code_agent_mini_eval.py tests/test_headless_run.py
uv run pytest -m 'not live'
uv run ruff format --check .
uv run ruff check .
uv run python -m compileall -q src tests
uv run morrow --help
uv run morrow run --help
git diff --check
```

Use scripted Providers, fake SDK chunks and fake clocks. Do not assert duration with wall-clock
sleeps. If sandboxed `uv` cannot access its cache, use the synchronized worktree `.venv` commands
and record the exact fallback. No live Provider/model/Pi/MCP/network/credential test is allowed
without a new explicit authorization.

## 8. Boundaries and exclusions

- Do not implement “unlimited” by raising 30 to 60/100/1,000 or by using maximum integers.
- Do not add a default no-progress/repetition stop, loop killer, model-request cap, tool-call cap or
  task deadline under another name. Progress metrics may be observed only.
- Do not make deterministic extraction the primary compaction summary; Pi's LLM summary is the
  standard. Deterministic bounded metadata/hashes may support safety and recovery.
- Do not erase or rewrite durable chat/tool history during compaction, create another chat-history
  writer, change public event types, weaken permissions/confinement, or expose protected payloads.
- Do not add a dependency without asking first. Do not implement MCP, Skills, background work,
  Workflow concepts or S7P-07 steering/follow-up in this subplan.
- Do not change the evaluation protocol/taxonomy to hide watchdog, cost, retry or compaction
  failures. Historical 30-round evidence remains historical and is not presented as the new target.
- Do not run live/network tests or begin implementation merely because this revised plan exists.

## 9. Acceptance

- [ ] The pinned Pi parity table is complete; every adopted/adapted/hardened/deferred row has source
      evidence, a Morrow owner and a focused test.
- [ ] New default runs have no cumulative model-request/tool-round/tool-call/task-time or
      repetition/no-progress termination; scripted successful runs exceed all retired ceilings.
- [ ] Normal continuation/stop, abort, optional host stop and fatal terminal behavior match Pi while
      preserving Morrow permissions, durability and legal tool history.
- [ ] Exact model windows and Pi-equivalent token accounting drive automatic compaction; missing
      capability never falls back to a guessed small char window.
- [ ] Automatic, overflow-recovery and manual compaction match Pi's structure, tail/boundary and
      chaining behavior; restart reconstructs it without erasing or repeating durable work.
- [ ] Provider retry and tool-output truncation match the pinned Pi defaults/state transitions/
      limits, with no double retry or duplicated durable effects.
- [ ] Historical v1 runs/data remain readable and resumable under v1; new v2 config/snapshots are
      strict; legacy user overrides are never silently ignored.
- [ ] Evaluation watchdogs live only in the harness, apply equally to Pi and Morrow and never appear
      as product Agent stop codes.
- [ ] Full offline regression, Ruff, compileall, CLI help and `git diff --check` pass; acceptance and
      architecture/config documentation describe actual behavior and every justified deviation.

Every completion claim attaches the reference row, problem reproduction, code/contract change,
automated verification, scripted long-horizon evidence, safety/persistence result, metric impact and
unresolved live A/B work. S7P-06 does not claim same-model Pi equivalence until S7P-09 (or a separately
authorized run) executes that comparison.
