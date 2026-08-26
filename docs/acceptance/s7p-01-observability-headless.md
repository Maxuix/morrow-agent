# S7P-01 AgentRun Observability and Headless Run Acceptance

日期：2026-08-26

状态：实现、focused/full offline 验证完成；独立只读 review 待本工作树内完成。本记录不
表示已执行真实 Provider、model、Pi、MCP、network 或 credential 评测。

## 范围与硬边界

S7P-01 adds safe AgentRun request/terminal observability and a non-interactive `morrow run`
entrypoint. The implementation keeps the existing public `AgentEvent` type, payload contract,
and lifecycle unchanged. Headless execution composes the existing
`build_session_application`, `SessionOrchestrator.stream`, and `AgentLoop`; it does not create a
second chat-history writer. `ConversationLog` remains the only chat-history writer.

The change does not alter runtime-policy defaults, tool schemas, the Direct Coding prompt,
completion判定, retry/steering behavior, or later S7P scope. It adds no dependency and does not
touch the three user-owned untracked research documents.

## Defect-to-evidence matrix

| Acceptance contract | Implementation evidence | Offline evidence |
|---|---|---|
| Provider usage is normalized from leading/trailing usage-only chunks; explicit zero is available and missing usage is unavailable | `openai_compatible.py` usage accumulator and `ModelUsage` | `tests/test_provider.py` usage-only, missing/zero, malformed/conflicting/post-finish cases |
| Usage and cost never silently become zero | `ModelUsage.unavailable()` and `ModelCost.unavailable()` | provider and AgentRun observation tests assert explicit availability |
| Context projection reports dropped turns/cycles without changing public events | `ContextPack` counters in `application/context.py` | `tests/test_context_projections.py` |
| Typed diagnostics retain only bounded `path` and `type` | `ValidationDiagnostic` and tool-persistence extraction | `tests/test_agent_run_observability.py` diagnostic tests |
| Model request admission/settlement is durable, bounded, idempotent, and correlated to one AgentRun | v17 observation journal and `SessionPersistence` delegation | `tests/test_agent_run_observability.py` admission, transition, idempotency, resume, retry, cancellation, deadline, and budget tests |
| Terminal metrics use persisted tool terminal dispositions and are safe to inspect | v17 terminal metrics projection | durable tool count and budget tests; tamper/cross-workspace tests |
| Interrupted/crashed work remains recovery-visible; historical pre-v17 AgentRuns remain compatible | open-request state plus Doctor compatibility check | reopen and Doctor tests |
| Headless run requires explicit workspace/prompt, emits versioned safe JSONL, and fails closed on approval | `morrow run` and `HeadlessApprovalPort` | `tests/test_headless_run.py` |
| Read-only AgentRun inspection has no prompt/message/full tool payloads | API/CLI safe projection | safe projection and CLI smoke tests |

## Durable lifecycle

```text
AgentRun correlation
  -> model request admitted
  -> exactly one terminal settlement (completed / failed / cancelled)
  -> terminal metrics finalized when the run reaches a normal terminal boundary

ToolExecution rows remain the source for terminal tool counts.
An admitted request or open execution is retained as recovery-visible evidence after a crash.
```

The observation tables contain identifiers, bounded counters, timestamps, normalized finish/error
codes, availability-aware usage/cost, and exact tool-terminal counts. They do not contain prompt or
message text, reasoning, credentials, SDK objects, traceback text, full tool arguments/results, or
raw provider responses. Existing public AgentEvents continue to carry only their pre-existing
contract; the new observation/terminal projections do not extend that contract.

## Scripted headless evidence

`tests/test_headless_run.py::test_run_emits_only_versioned_jsonl_and_terminal_safe_record` drives a
fake orchestrator through the production CLI composition boundary and verifies the JSONL kinds:

```text
agent_event -> agent_event -> run.completed
```

Every record has `schema_version: 1`. The terminal record exposes the AgentRun identifier and safe
metrics, including `usage.availability: unavailable` when the provider supplies no usage. The
headless path does not read stdin, accepts only an explicitly registered workspace, uses a shared
workspace lock, and returns a stable nonzero result when the run cannot complete.

## Terminal accounting cases

The focused suite covers normal completion, model retry attempts, cancellation before provider
completion, deadline expiry after text and before provider admission, invalid-argument diagnostics,
successful and cancelled durable tool executions, and the total-tool-budget path. The budget path
closes unresolved executions before normal finalization; cancellation/deadline paths settle an
already-admitted request before exposing the terminal failure. A crash/fault path intentionally
leaves the admitted request visible for recovery rather than fabricating successful metrics.

## Safety and compatibility checks

- No public `AgentEvent` declaration, payload field, or lifecycle transition was changed.
- No default runtime-policy value, tool schema, Direct Coding prompt, completion判定, retry, or
  steering behavior was changed.
- `ConversationLog` remains the sole writer of chat history.
- Validation diagnostics are bounded and redact values; observation and headless projections are
  safe-by-construction and fail closed on malformed typed JSON.
- No missing usage/cost is represented as numeric zero.
- The v17 migration is additive to immutable AgentRun snapshots and preserves older AgentRuns that
  predate observation rows.

## Verification record

Focused S7P-01 checks and the full offline gate completed before the required review:

| Check | Result |
|---|---|
| provider/context/observability/headless/persistence focused suite | 124 passed, 1 skipped |
| AgentRun and agent-limit regression suite | 35 passed |
| `uv sync` | Resolved 65 packages; checked 59 packages |
| full offline pytest (`uv run pytest -m 'not live'`) | 1134 passed, 2 deselected in 48.98s |
| `uv run ruff format --check .` | 457 files already formatted |
| `uv run ruff check .` | All checks passed |
| `uv run python -m compileall -q src tests` | Passed |
| `uv run morrow --help`, `run --help`, `agent-run --help` | Passed; command surfaces present |
| `git diff --check` | Passed; no output |

No live tests are part of this acceptance. The same focused and full gates will be repeated after
the required read-only reviewer has returned findings.

## Review and residual risk

The required collaboration reviewer and exact model/reasoning settings will be recorded after the
full diff review. The residual risk at this stage is limited to unexercised real Provider/Pi/MCP or
network integrations and provider-specific cost metadata; unavailable cost remains explicit until
such metadata is supplied. No branch merge, push, or worktree deletion is part of S7P-01 execution.
