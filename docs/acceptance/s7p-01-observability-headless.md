# S7P-01 AgentRun Observability and Headless Run Acceptance

日期：2026-08-26

状态：实现、focused/full offline 验证、独立只读 review 及 review 修复均已完成。本记录不
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
small fake orchestrator only to verify the headless wrapper's JSONL ordering and serialization:

```text
agent_event -> agent_event -> run.completed
```

Every record has `schema_version: 1`. The terminal record exposes the AgentRun identifier and safe
metrics, including `usage.availability: unavailable` when the provider supplies no usage. The
production-composition path is covered separately by
`tests/test_headless_run.py::test_run_uses_the_real_session_builder_with_a_scripted_provider`:
it constructs the real application, replaces only the Provider seam with a scripted offline
Provider, and exercises `build_session_application`, `SessionOrchestrator.stream`, and
`AgentLoop`. A direct dispatch regression also verifies that a slash/preflight/stream-failure path
cannot reuse an older AgentRun identity. The headless path does not read stdin, accepts only an
explicitly registered workspace, uses a shared workspace lock, and returns a stable nonzero result
when the run cannot complete.

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

The initial implementation gates completed before review. After the review repairs, the final
affected and repository-wide offline gates were rerun:

| Check | Result |
|---|---|
| review-fix focused regression suite (`test_agent_run_observability`, `test_headless_run`, operational store, v15 migration) | 69 passed in 6.24s |
| Subplan validation: provider/context | 55 passed, 1 skipped in 1.07s |
| Subplan validation: observability/headless | 33 passed in 1.61s |
| Subplan validation: durable tool persistence/operational store | 41 passed in 5.79s |
| expanded affected S7P-01 suite | 224 passed, 1 skipped in 8.14s |
| `uv sync` | Resolved 65 packages; checked 59 packages |
| full offline pytest (`uv run pytest -m 'not live'`) | 1142 passed, 2 deselected in 49.17s |
| `uv run ruff format --check .` | 457 files already formatted |
| `uv run ruff check .` | All checks passed |
| `uv run python -m compileall -q src tests` | Passed |
| `uv run morrow --help`, `run --help`, `agent-run --help` | Passed; command surfaces present |
| `git diff --check` | Passed; no output |

No live tests are part of this acceptance. The final gate results above include the review repairs.

## Review and residual risk

The required collaboration reviewer was Euler (`01a03e1f-77c8-72d2-ad24-9cdf1d25edde`), using
`gpt-5.6-luna` with `max` reasoning. The reviewer was read-only and inspected the complete
`d204a6518d1c7f64193ddb42c33384c8fb320e3d..50fa808741dd1b1e4748f06a57525d8dd951fe3e` diff;
the reviewer changed no files and ran no tests. The review found seven confirmed findings (one P1,
five P2, and one P3), no P0 findings, and no uncertain findings. They were all independently
verified and repaired:

- Empty Provider streams now settle as `invalid_response`, with a regression test.
- Model-request and terminal-metrics observations now enforce compatible terminal facts in both
  Pydantic validation and schema-v17 checks.
- Cost sources are bounded safe provider labels and reject credential-like values.
- Headless failure/preflight paths cannot reuse an older AgentRun identity.
- Clean resume retry accounting includes the latest settled failed request.
- A real-builder scripted-Provider test now covers the production headless composition path; the
  fake test is limited to wrapper serialization evidence.
- Durable validation diagnostics require a matching `ok: false` invalid-arguments envelope and
  inner error code before any `{path,type}` details are retained.

Residual risk is limited to unexercised real Provider/Pi/MCP or network integrations and
provider-specific cost metadata; unavailable cost remains explicit until such metadata is supplied.
No branch merge, push, or worktree deletion is part of S7P-01 execution.
