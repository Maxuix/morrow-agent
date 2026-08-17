# Stage 2 Agent Core Implementation Plan

> Status: complete; Stage 2 accepted after final code-review remediation.
> Active subplan: none

## Overall goal

Implement the approved Stage 2 Agent Core as one production path that can take a real user goal through multiple model/tool steps and return a final answer while preserving legal history, deterministic cancellation, Stage 1 product behavior, and strict Stage 3/4/5 boundaries.

The implementation must finish with a working terminal product, not only isolated protocol modules:

```text
User input
→ SessionOrchestrator
→ AgentLoop.run_task()
→ ContextBuilder
→ OpenAI-compatible Provider
→ ToolExecutor
→ ConversationLog
→ final Assistant answer / deterministic stop
```

## Authority and precedence

1. [Stage 2 roadmap](../docs/roadmap/stage-2-agent-core.md) owns the approved scope, protocol and safety invariants.
2. This plan owns implementation order, dependencies and stage gates.
3. The active subplan owns concrete work and validation for one vertical slice.
4. Tests and observed behavior take precedence over stale plan wording; any material conflict must update the roadmap/plan before implementation continues.
5. The proposal and its reviews remain decision history, not competing implementation authorities:
   - [approved proposal record](../docs/reviews/stage-2-agent-core-final-proposal.md)
   - [first review](../docs/reviews/stage-2-agent-core-final-proposal-review.md)
   - [executability review](../docs/reviews/stage-2-agent-core-revised-proposal-review.md)

## Entry baseline

- Stage 1 final-tree offline, Live and manual acceptance is complete.
- The current product has one OpenAI-compatible Adapter, a text-only `AgentRuntime.run_turn()`, mutable `Session.messages`, a deterministic ContextBuilder, structured completion/Handoff services and a terminal event renderer.
- Pydantic v2 and Python 3.12 `tomllib` are already available; Stage 2 requires no new third-party dependency.
- Production code has no Stage 2 tool execution, persistent conversation history, local project tools, MCP, Skills or summary pipeline.

## Execution strategy

Stage 2 is split into four ordered vertical slices. Each slice must leave the tree integrated and green. Module boundaries guide ownership, but no slice may postpone its first end-to-end integration until a later module is “complete.”

| Order | Subplan | Status | Depends on | Release value |
|---|---|---|---|---|
| 17 | [Walking Skeleton](subplans/17-stage-2-walking-skeleton.md) | complete | Stage 1 complete | One model → tool → model loop, one history writer, minimal safe Cycle closure |
| 18 | [History, Context, and Product Projections](subplans/18-stage-2-history-context-product-projections.md) | complete | 17 | Full ToolCycle legality, safe context reduction, Handoff/structured compatibility |
| 19 | [Guardrails, Policy, and Observability](subplans/19-stage-2-guardrails-policy-observability.md) | complete | 18 | Configured budgets, cancellation closure, retry/loop controls, terminal tool UX |
| 20 | [Acceptance and Delivery](subplans/20-stage-2-acceptance-and-delivery.md) | complete | 17–19 | Complete evidence, documentation reconciliation and Stage 2 completion decision |

Only one subplan is active at a time. A failed gate reopens the owning subplan; later slices must not paper over an earlier invariant failure.

## Cross-cutting implementation contracts

These contracts apply from the first slice that touches the relevant behavior:

1. **Single chat state machine:** all ordinary chat reaches `AgentLoop.run_task()`; retained `run_turn()` is only a thin no-tools delegate.
2. **Single history authority:** Session owns one ConversationLog. There is no second mutable message list and no transitional double write.
3. **Adapter ownership:** Core expresses one OpenAI-compatible function-calling subset. Vendor request serialization, stream fragment assembly and future native conversion stay in Provider Adapters.
4. **Atomic ToolCycle:** after an Assistant tool-call batch is accepted, every call receives exactly one ordered real or synthetic ToolMessage before another Assistant, User or terminal record can be appended; this minimum closure exists in Slice 1, not only in final hardening.
5. **Commit points:** a partial Provider stream is never history; accepted tool calls are history only after minimum closure space is guaranteed; a final Assistant is completed only after it is appended.
6. **No tool auto-retry:** ToolExecutor produces one bounded outcome. `retryable` is model guidance, not Runtime permission to replay a tool.
7. **Pure context projection:** ContextBuilder is synchronous and deterministic; it reads a snapshot, never writes Session/Log/Handoff, never calls a model and never summarizes.
8. **State-data boundary:** ToolMessage, Profile, Preferences and Handoff are model data, not commands or authorization. Tool data never enters command/config/permission routing.
9. **Developer-owned policy:** operational limits come from a bundled validated TOML plus exact-model safe-size declarations. They are injectable in tests and absent from user Preferences, Profile, Handoff and CLI.
10. **Public lifecycle:** one task emits exactly one `turn.started` and one `turn.completed`; fatal errors emit one `error` with the same AgentStopCode before completion; cancellation is not an error.
11. **No secret/raw payload leakage:** public events, logs and terminal output exclude credentials, reasoning, full arguments, full results, SDK objects and tracebacks.
12. **Stage boundary:** only the two approved in-memory tools are enabled. No file, Shell, Git, network, browser, MCP, Skill, persistence, LLM-summary or background-task capability enters Stage 2.

## Development method

For every logical task:

1. Mark the task in `.agent/TODO.md` as in progress and update `.agent/TRACKER.md`.
2. Add or tighten a failing focused test for the contract being implemented.
3. Implement the smallest integrated change that satisfies the contract.
4. Run the focused test and the affected Stage 1 regression set.
5. Run the subplan gate before marking the task/subplan complete.
6. Record only material decisions, failures and validation results in `.agent/LOG.md`.

Temporary compatibility is allowed only when it is read-only and has an explicit removal task in the same or next slice. The sole numeric bridges allowed before policy loading are the two existing Stage 1 values (retry limit 1 and context limit 24000): retry is explicit in Slice 1, Slice 2 must turn the ContextBuilder default into one explicit composition injection, and S2.19.1 removes both. Temporary duplicate writers, duplicate runtimes, new magic-number fallbacks and unbounded production tool enablement are prohibited.

## Quality gates

Every subplan must pass:

- focused unit/integration tests for its changed contracts;
- the complete offline suite under the repository NetworkGuard;
- `uv run ruff format --check .`;
- `uv run ruff check .`;
- `uv run python -m compileall -q src tests`;
- the capability-based Stage 2 boundary tests;
- `git diff --check`.

Additional rules:

- no unexpected skip/xfail may hide a Stage 2 contract;
- wall-clock timing assertions use injected monotonic time, events or bounded synchronization rather than sleeps;
- Provider tests use fake SDK chunks and scripted Providers by default;
- any attempted Live test is opt-in, secret-safe and recorded as observed evidence rather than inferred success;
- package-resource tests must verify the policy TOML is present in an installed/built artifact before Stage 2 completes.

## Principal risks and containment

| Risk | Containment and owning slice |
|---|---|
| OpenAI-compatible vendors emit irregular/interleaved fragments | Table-driven fake-SDK accumulator tests and request round trip in 17 |
| Session migration creates dual history or orphan messages | Atomic migration to Session-owned Log in 17; remaining readers only in 18 |
| Context reduction produces illegal tool history | Snapshot-derived semantic units, final validator and exhaustive boundary cases in 18 |
| Cancellation/exception lands between calls/results | Minimal unresolved-call closure in 17; exhaustive commit points, budget/status mappings in 19 |
| Large batches/results make the next request impossible | Pre-admission Cycle capacity check and equal per-call bounds in 19 |
| Retry replays observable model output | Adapter/runner `made_progress` contract and retry matrix in 19 |
| Policy values become scattered constants or user settings | One validated bundled TOML and frozen RunPolicy in 19 |
| Terminal repeats mixed-content output | Event-sequence renderer tests in 19 and manual acceptance in 20 |
| Tool execution crosses into Stage 3 | Default-registry and side-effect boundary tests from 17 through 20 |

## Stage 2 definition of done

Stage 2 is complete only when all four subplans are complete and executable evidence proves:

- a single user goal reaches a final answer after at least two dependent tool steps;
- plain no-tools chat uses the same AgentLoop and retains all Stage 1 behavior;
- accepted calls/results are one-to-one and no exit path leaves an open Cycle;
- malformed/partial Provider output never enters history;
- all Provider requests use an explicit field whitelist and legal tool history;
- tool validation, unknown tool, not-found, timeout, failure, cancellation and truncation produce bounded deterministic results;
- model/tool/time/context/output/loop limits stop safely with the approved public reason;
- ContextBuilder clears and trims only derived Views, never the facts or Handoff;
- Handoff and StructuredCompletion never consume ToolMessage envelopes or intermediate tool-call Assistant content;
- cancellation during model or tool work is followed by a healthy next user turn;
- terminal text/tool/final segments are ordered, non-duplicated and secret-safe;
- ConversationLog remains process-local and resettable;
- all mandatory offline, packaging and manual acceptance gates pass;
- Stage 1 remains green and no Stage 3/4/5 capability is present.

## Completion

Stage 2 completed 2026-08-17. Subplan 20 mapped every acceptance branch to direct evidence, closed the remaining fake-chunk/output-allocation/deadline/loop and real-terminal coverage gaps, then remediated review findings through S2.20.12: Provider deadlines, context rebuild, Cycle sizing, bounded synthetics, finish/message consistency, finite calculator output, NL config budget degradation, per-Cycle call-ID uniqueness, deadline-scoped Provider `anext`, and `GeneratorExit` turn cleanup. The final tree passed 308 offline tests with one Live test deselected, plus ruff, compileall, and capability/product sentinel gates. Final evidence is in `docs/acceptance/stage-2-evidence.md`. The optional Live smoke was not run because no explicit compatible credential was present; no Live success is claimed. Stage 3 has not been planned or started by this plan.
