# Stage 7 Preflight Reliability Repairs — S7P-01 Observability and Headless Run

> Status: active
> Active subplan: 80 — safe AgentRun observability and a headless execution entrypoint
> Branch: `codex/feat/s7p-01-observability-headless`
> Base: verified local `main@d204a6518d1c7f64193ddb42c33384c8fb320e3d`
> Source authority: the user-requested S7P-01 checklist, current code, and deterministic checks

## 1. Objective

Make every later Stage 7 preflight repair diagnosable without weakening Morrow's safety boundaries.
Persist bounded request/usage/context/stop observations against the exact AgentRun, preserve safe
invalid-argument diagnostics, and add a non-interactive JSONL execution path that reuses the same
Session application, AgentLoop and ConversationLog writer as the interactive CLI.

S7P-01 does not change tool schemas, Direct Coding prompts, workspace mutation semantics, completion
truthfulness, round-budget defaults, retry policy, steering/follow-up behavior or public event types.

## 2. Located defects

1. `OpenAICompatibleProvider.stream()` skips every `choices=[]` chunk and returns immediately on the
   finish-bearing choice. Standard trailing usage-only chunks therefore never reach Runtime.
2. `ModelEvent` and `ModelCallOutcome` have no normalized usage contract. Missing usage cannot be
   distinguished from a numeric zero.
3. `ContextPack` calculates request characters, cleared cycles and dropped records, but AgentLoop
   discards those facts after each request; it does not count dropped turns/cycles explicitly.
4. `DurableAgentRun` stores only an immutable preparation snapshot. There is no request ledger or
   terminal aggregate for usage availability, input/output tokens, context pressure, tool/model
   rounds, or stop code.
5. Pydantic validation already produces bounded `{path,type}` details and the model receives them in
   the tool envelope, but `_envelope_from_outcome()` persists only `error_code` and envelope length.
6. The root CLI always enters a prompt-toolkit REPL. Existing state commands are headless, but there
   is no one-shot Agent execution command or JSONL projection with Session/TaskRun/AgentRun IDs.
7. The public `AgentEvent` lifecycle is fixed and validated. S7P-01 can meet the machine-readable
   requirement by wrapping existing events at the interface boundary; no new public event type or
   payload field is needed.

## 3. Frozen implementation decisions

- Add internal normalized usage models with explicit `available` / `unavailable` state. Token and
  cost values are optional; absent Provider data remains unavailable, never zero.
- Consume a complete Provider stream after its logical finish so a trailing usage-only chunk is
  retained. Emit no text for usage chunks and accept exactly one bounded usage record per attempt.
- Add Operational Store schema v17 with an AgentRun terminal-metrics row and ordered model-request
  rows keyed to the existing AgentRun. Do not mutate the immutable AgentRun preparation snapshot.
- Record request admission before the Provider call and settle it exactly once as completed, failed
  or cancelled. A crash-visible open row remains explicit rather than being fabricated as success.
- Persist only scalar counts, enum/status values, bounded hashes and already-sanitized validation
  `{path,type}` diagnostics. Never persist prompts, messages, reasoning, tool arguments/results,
  SDK objects, credentials, URLs containing credentials or tracebacks.
- Extend `ContextPack` with deterministic dropped-turn/dropped-cycle counts while retaining existing
  compatibility counters.
- Keep `PUBLIC_EVENT_TYPES`, `AgentEvent` payload contracts and lifecycle ordering unchanged. The
  headless command serializes existing events inside a versioned JSONL interface envelope and emits
  one terminal run record containing correlation IDs and the persisted metric projection.
- The one-shot command uses `build_session_application()` and `SessionOrchestrator.stream()`. It does
  not write ConversationLog directly or create a second state machine. Non-interactive approvals
  fail closed; permission and sandbox policy are not widened.
- Add read-only application/CLI inspection for one AgentRun, its request metrics and terminal tool
  states. Full tool arguments/results and unredacted messages remain unavailable.
- No third-party dependency, runtime-policy default, live Provider call or public-event change is in
  scope.

## 4. Data and lifecycle contract

### Provider usage

- Normalize prompt/input, completion/output and total tokens when valid non-negative integers exist.
- Reject conflicting or malformed duplicate usage as an invalid Provider response.
- Preserve explicit unavailable status when no usable usage record arrives.
- Preserve cost availability separately. OpenAI-compatible token usage does not imply a monetary
  cost when no trusted price/cost fact exists.

### Model-request observation

Each ordered request records AgentRun ID, attempt ordinal, state, estimated request characters,
configured request budget, cleared-cycle count, dropped-turn/cycle/record counts, tool round/call
counts at admission, normalized finish/error code, usage/cost availability and timestamps.

### AgentRun terminal metrics

The terminal projection records finish reason, explicit stop code when present, model attempts, tool
rounds/calls, terminal tool-disposition counts, context reduction totals and normalized aggregate
usage. Cancellation, failure, budget exhaustion and normal stop all finalize through one bounded
path; unavailable values stay unavailable.

### Durable invalid-argument diagnostics

For `invalid_arguments` only, persist at most the configured bounded list of field paths and error
types already generated by the validator. Never persist the offending values or raw argument JSON.

### Headless JSONL

- A required prompt and explicit workspace directory drive one ordinary turn.
- The command never prompts for onboarding, credentials, approval or recovery decisions.
- Every stdout line is valid JSON. Diagnostics go to stderr.
- Existing AgentEvents retain their original schema and order inside interface envelopes.
- The terminal record includes `session_id`, `task_run_id`, `agent_run_id`, finish/stop state and the
  safe metrics view; process exit is zero only for a normally completed turn.

## 5. Implementation sequence

1. Add failing adapter/runtime tests for trailing usage, unavailable usage and malformed/conflicting
   usage without extra text.
2. Add normalized usage and AgentRun observation domain contracts plus schema-v17 migration, journal
   ports, SQLite implementation, migration/doctor/backup coverage and query views.
3. Thread request admission/settlement and terminal finalization through `SessionPersistence` and
   AgentLoop; extend ContextPack counters without changing ConversationLog ownership.
4. Retain bounded invalid-argument diagnostics in the existing durable tool-result envelope and add
   redaction/budget tests.
5. Add the one-shot JSONL CLI and read-only AgentRun inspection, reusing bootstrap/orchestrator and a
   fail-closed non-interactive approval adapter.
6. Add equivalence tests proving interactive dispatch and headless dispatch produce the same
   ConversationLog and durable terminal state for the same scripted Provider.
7. Publish `docs/acceptance/s7p-01-observability-headless.md`, update execution state and run all
   focused/full offline quality gates.

## 6. Validation

```bash
uv run pytest -q tests/test_provider.py tests/test_context_projections.py
uv run pytest -q tests/test_agent_run_observability.py tests/test_headless_run.py
uv run pytest -q tests/test_stage4_tool_persist.py tests/test_operational_store.py
uv run pytest -m 'not live'
uv run ruff format --check .
uv run ruff check .
uv run python -m compileall -q src tests
uv run morrow --help
uv run morrow run --help
git diff --check
```

No live Provider, credential, network, Pi or MCP test is part of S7P-01. Scripted Providers and
temporary Operational Stores provide deterministic end-to-end evidence.

## 7. Completion and integration

- The focused matrix covers usage-only chunks, explicit unavailable metrics, context counters,
  request/terminal durability, safe validation diagnostics, cancellation/failure/budget terminal
  states, headless/interactivity equivalence and JSONL/exit semantics.
- Acceptance evidence maps every S7P-01 checklist item to code, tests and a queryable artifact.
- The Luna Max implementation session spawns a separate Luna Max review subagent for the full
  `base...topic` diff. It repairs every confirmed finding and reruns affected plus full gates.
- Verified changes are committed on the topic branch. This root session then fast-forward merges the
  branch into local `main`, verifies ancestry/cleanliness and retires the clean worktree/branch.
- The three user-owned untracked research documents remain untouched. No remote push is performed
  unless separately requested.
