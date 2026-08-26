# Subplan 80 — S7P-01 AgentRun Observability and Headless Run

> Status: active
> Branch: `codex/feat/s7p-01-observability-headless`
> Base: `main@d8d2752752cf7d0d0b057af9db56b3029fd05120`
> Dependency: S7P-00 completed and integrated

## Goal

Establish the safe diagnostic chain required by every later Stage 7 preflight repair. Persist
bounded Provider usage, context pressure, request progress and terminal stop facts against the exact
AgentRun, retain recoverable invalid-argument diagnostics, and add a script-friendly one-shot JSONL
entrypoint without introducing a second chat state machine or changing public event lifecycle.

## Ownership

- normalized Provider usage contracts in `src/morrow/core/models.py` and the OpenAI-compatible
  adapter/runtime interpretation path;
- context-observation counters in `src/morrow/application/context.py`;
- new bounded AgentRun observation contracts/ports and schema-v17 migration/journal code;
- `SessionPersistence`/AgentLoop plumbing for request admission, settlement and terminal metrics;
- durable invalid-argument diagnostics in the existing tool result envelope path;
- a one-shot CLI command and read-only AgentRun inspection through existing bootstrap/application
  seams;
- focused tests, `docs/acceptance/s7p-01-observability-headless.md`, and Subplan 80 execution state.

The subplan does not own tool schemas, Direct Coding prompts/project instructions, delete/move/
rename, validation/completion truthfulness, round-budget defaults, context summaries, retry/backoff,
steering/follow-up, Stage 7 Workflow code or the three user-owned research documents.

## Reproduced defects

### Usage loss

`OpenAICompatibleProvider.stream()` skips empty-choice chunks and exits immediately when it sees a
logical finish. `tests/test_provider.py::test_adapter_ignores_usage_only_chunks` currently codifies
the wrong behavior. OpenAI-compatible streams can deliver usage in an empty-choice chunk after the
finish chunk, so no Runtime or AgentRun layer can recover the missing usage.

### Context and stop facts are ephemeral

`ContextBuilder._chat()` calculates estimated request chars, cleared cycles and dropped records.
AgentLoop validates and sends the pack but retains only messages. Stop code exists transiently while
building public terminal events. The process-local `RunMetricsSnapshot` is keyed to a turn ID, counts
any successful command as validation and is deliberately not persisted. It cannot support S7P-01.

### AgentRun has no observation projection

Operational schema v16 stores `agent_runs.snapshot_json` as immutable admission evidence and has no
request or terminal metric tables. Adding post-run facts to the snapshot would break its immutability
and preparation meaning, so observations require linked rows and explicit state transitions.

### Durable validation diagnostics are discarded

`PydanticArgumentsValidator` produces capped, value-free field/type details. ToolExecutor includes
them in its bounded error envelope, but `application.tool_persistence._envelope_from_outcome()`
stores only envelope character count and error code. Restart/query evidence cannot identify the bad
field even though the model saw it.

### No one-shot Agent entrypoint

The root command launches prompt-toolkit and can prompt for Provider setup, workspace onboarding and
approval. `SessionOrchestrator.stream()` already exposes the shared Agent path, so a one-shot command
can reuse it and serialize events without changing the public lifecycle.

## Detailed implementation contract

### 1. Normalized usage

Add frozen, extra-forbid models for token/cost availability. Token fields are optional non-negative
integers; numeric zero is valid only when explicitly reported. Total must be consistent with known
input/output values. Cost is a separate optional decimal/minor-unit fact with an explicit currency
and source if a trusted adapter supplies it; otherwise it is unavailable.

The OpenAI-compatible adapter must:

- request/accept stream usage using only supported adapter request fields;
- retain at most one logically consistent usage payload from empty-choice or choice-bearing chunks;
- continue consuming after the semantic finish to accept a trailing usage chunk;
- emit text deltas exactly as before and exactly one logical completion;
- reject malformed/conflicting usage or semantic chunks after finish as `invalid_response`;
- exclude raw SDK objects, vendor fragments, reasoning and unknown usage fields.

Runtime attaches normalized usage to the settled model attempt. If no usage arrives, it records
`unavailable`; it never fabricates token or cost zeros.

### 2. Context observation

Extend `ContextPack` compatibly with `dropped_turn_count` and `dropped_cycle_count`. Preserve
`cleared_cycle_count` and `dropped_record_count`. Counts must derive from the exact source ranges
removed for the request and remain deterministic across identical snapshots.

Before each Provider call, persist a model-request row containing only:

- AgentRun ID and one-based attempt ordinal;
- admitted state/timestamp;
- estimated request chars and configured char budget;
- cleared-cycle and dropped-turn/cycle/record counts;
- tool rounds and total tool calls already consumed.

Settle the row exactly once with completion/failure/cancellation, normalized finish/error code,
usage/cost availability and terminal timestamp. Admission without settlement is a visible incomplete
request after a crash, not success.

### 3. Schema v17 and queries

Create a dedicated migration module and update the supported/reserved schema constants. Use linked
tables rather than mutable AgentRun snapshot fields:

- one bounded terminal-metrics row per AgentRun;
- ordered model-request observation rows with a uniqueness constraint per AgentRun/ordinal;
- foreign keys and workspace/subject guards consistent with existing journals;
- enum/check constraints for observation state, finish/stop and availability markers;
- canonical JSON only for bounded typed substructures where scalar columns are unsuitable.

Add core port methods and a narrow SQLite journal. Writes validate the run/workspace relationship,
legal request state transition and idempotent terminal finalization. Read models expose no messages,
arguments or results. Add application queries for a complete safe AgentRun inspection: base run,
terminal metrics, ordered requests and tool execution terminal counts.

Migration tests must cover fresh v17 initialization, v16→v17 upgrade, future-version refusal,
checksums, backup/restore/doctor compatibility and tamper/fail-closed reads.

### 4. AgentLoop/SessionPersistence integration

Expose a small durable observation contract through `DurableRunCoordinator`; do not make AgentLoop
reach into SQLite. Resolve the true durable AgentRun ID after turn submission/resume instead of using
the turn ID as the observation key.

Every terminal path—normal stop, Provider failure, invalid response, context/model/tool budget,
loop detection, deadline, cancellation, startup/recovery error and unexpected internal error—must
finalize the aggregate once when an AgentRun exists. Record:

- finish reason and explicit stop code when present;
- model attempts, retries, tool rounds and total tool calls;
- context reduction totals/max pressure;
- input/output/total tokens and cost availability aggregated from settled requests;
- terminal tool dispositions mechanically derived from durable ToolExecutions.

Do not alter public `AgentEvent` types/payloads. Do not weaken the existing close-unresolved logic.
Tests must show each accepted tool call has one closed durable terminal disposition across success,
failure, cancellation and budget exhaustion.

### 5. Durable invalid-argument diagnostics

Add a bounded typed diagnostic entry (`path`, `type`) to the existing handler-result envelope or a
semantically equivalent bounded field inside its current JSON column. Populate it only from parsed,
validated tool error-envelope details for `invalid_arguments`; ignore arbitrary handler result data.
Cap entries and bytes, validate allowed strings, run secret-material checks and store no values.

### 6. One-shot JSONL and inspection CLI

Add a top-level one-shot command, with final naming documented in `--help`, that accepts an explicit
workspace, required prompt, optional Session resume ID, permission preset and state root. It must:

- fail non-interactively if Provider/config/credential/workspace/recovery prerequisites are absent;
- validate native sandbox availability for `auto-sandboxed` exactly as interactive mode does;
- acquire the same workspace writer lock;
- build the same Session application and call `SessionOrchestrator.stream()`;
- use a non-interactive approval port that fails closed and never reads stdin;
- print only versioned JSON objects on stdout, one per line;
- wrap each existing AgentEvent without changing it, then print one terminal run record with
  Session/TaskRun/AgentRun IDs and the safe metrics projection;
- return zero only for normal completion and a stable nonzero code for cancelled/error/preflight
  outcomes.

Add a read-only AgentRun inspection command/API that emits the same safe observation projection for
post-run evaluation tooling. Interactive and headless scripted runs with identical inputs must have
equivalent ConversationLog records, Task/Agent terminal state and ToolExecution outcomes.

## Test-first sequence

1. Replace the usage-ignore regression with adapter tests for leading/trailing usage, unavailable
   usage, explicit zero, malformed/conflicting usage and post-finish semantic rejection.
2. Add ContextPack counter tests for dropped full turns and current-turn cycles.
3. Add schema/journal tests for observation admission, settlement, terminal aggregation,
   idempotency, invalid transitions, workspace guards, migration and tamper handling.
4. Add runtime tests for all finish/stop classes and exact AgentRun correlation across resume.
5. Add durable tool diagnostic tests proving field/type recovery and absence of raw values/secrets.
6. Add CLI/application tests for JSONL validity, no stdin use, exact event order, run IDs, exit codes,
   approval denial, preflight failures and interactive/headless equivalence.
7. Add safety scans over YAML, emitted JSONL, public events and SQLite projections for credential,
   reasoning, complete arguments/results and traceback sentinels.

## Acceptance evidence

Publish `docs/acceptance/s7p-01-observability-headless.md` containing:

- before/after defect reproduction;
- schema and lifecycle diagrams in prose/table form;
- checklist-to-test mapping;
- scripted end-to-end headless output summary with queryable run ID;
- cancellation/failure/budget/normal tool terminal accounting;
- usage unavailable-vs-zero evidence;
- secret/redaction checks and public-event compatibility result;
- exact validation commands/results, residual risks and rollback point.

## Validation

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

If implementation chooses a different final subcommand name, update the help smoke command and
acceptance record. Do not run live Provider/model/Pi/MCP/network/credential tests.

## Review, repair and handoff

After the first coherent verified implementation commit, the implementation task must spawn a
Luna Max review subagent. The reviewer is read-only and examines the complete
`d8d2752752cf7d0d0b057af9db56b3029fd05120...HEAD` diff for:

- lost or double-counted usage, especially trailing chunks and retries;
- crash/idempotency and illegal observation transitions;
- mismatch between turn IDs and AgentRun IDs;
- unfinalized terminal paths or nonterminal ToolExecutions;
- raw prompt/argument/result/reasoning/credential/traceback leaks;
- JSONL contamination, stdin prompts or interactive/headless divergence;
- migration/backup/doctor compatibility and public-event drift;
- test gaps that could create false acceptance.

The implementation task repairs every confirmed finding, reruns focused and full gates, updates
acceptance/execution state and commits the final verified state. It must not merge into `main`,
delete the topic branch/worktree, touch the three user-owned research documents, push a remote or
start S7P-02. The root task owns inspection, fast-forward integration and retirement.
