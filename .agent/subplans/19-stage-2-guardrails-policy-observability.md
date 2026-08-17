# Subplan 19 — Stage 2 Guardrails, Policy, and Observability

> Stage: 2
> Slice: 3 of 4
> Status: complete (2026-08-17)
> Parent: [Stage 2 implementation plan](../PLAN.md)
> Depends on: Subplan 18

## Objective

Make the integrated AgentLoop safe to enable in the production bootstrap. Add validated developer policy, exact-model request sizing, all model/tool/time/output limits, complete the Slice 1 cancellation/closure semantics, add retry/loop controls, precise public stop reasons and non-duplicating terminal tool-step rendering.

## Required design decisions

1. Policy is one bundled TOML parsed by `tomllib` into strict frozen Pydantic models. Missing/invalid packaged policy fails bootstrap clearly; Runtime contains no fallback magic numbers.
2. The bundled policy is developer-owned. No value is added to Preferences, Profile, Handoff, config-extraction Schema, user CLI or natural-language configuration.
3. `safe_request_chars` is resolved only by exact `provider_id/model_id` table lookup. Unknown models use the configured unknown-model fallback; no prefix/token-window guess is allowed.
4. Slice 1 already guarantees minimum `cancelled`/`internal` closure for every accepted batch. This slice adds the exhaustive commit-point, deadline and budget variants. “Skipped” is only a public tool status: cancellation writes `cancelled` envelopes, and budget/deadline skips write `budget_exhausted` envelopes.
5. Model retries require a transient error, no visible/tool fragment progress, retry budget and overall run budget. A progressed request is never silently replayed.
6. The default registry is enabled in bootstrap only after policy, limits, cancellation closure and event tests pass together.

## Executable tasks

### S2.19.1 — Add validated developer policy

1. Add a packaged `morrow/resources/agent-policy.toml` and load it with `importlib.resources` + `tomllib`.
2. Define strict frozen `AgentPolicy`, `RunPolicy` and `ProviderToolSupport` models.
3. Include and validate:
   - max_tool_rounds = 30
   - max_model_attempts = 40
   - max_tool_calls = 128
   - max_tool_calls_per_cycle = 32
   - max_run_seconds = 1800
   - tool_timeout_seconds = 120
   - model_retry_limit = 1
   - requested_context_chars = 800000
   - unknown_model_fallback_chars = 160000
   - max_tool_result_chars = 64000
   - max_tool_result_request_ratio = 0.10
   - max_tool_cycle_chars = 256000
   - max_tool_cycle_request_ratio = 0.35
   - max_validation_errors = 3
   - loop_detection_enabled = true
   - loop_repeat_limit = 3
   - loop_max_pattern_cycles = 4
4. Reject non-positive counts/durations/sizes, ratios outside (0, 1], repeat limits below 2 and internally impossible combinations; require `loop_repeat_limit × loop_max_pattern_cycles <= max_tool_rounds` so the longest configured repeated pattern can trigger before the hard round cap.
5. Resolve effective request/result/Cycle limits exactly:

   ```text
   request = min(requested, exact_model_safe or unknown_fallback)
   result = min(max_result, floor(request × result_ratio))
   cycle = min(max_cycle, floor(request × cycle_ratio))
   ```

6. Extend Adapter registration metadata only with `tool_protocol` and `multiple_tool_calls`; keep exact-model safe sizes in the TOML model table.
7. Ship the initial production model table empty. All real models therefore use the configured 160000 unknown-model fallback until exact ModelRef entries are added from verified Live evidence; exact-hit tests use an injected test policy and must not seed guessed production entries.
8. Add injected-policy, exact-hit, unknown fallback, malformed/missing resource, combination-validation and package-resource tests.
9. Remove both approved compatibility numeric sources: the Slice 1 retry limit and Slice 2’s explicit Stage 1 context limit. Runtime/ContextBuilder must have no built-in numeric limit, including no `max_retries=1` or `max_chars=24000` default; all final values come from the resolved RunPolicy.

### S2.19.2 — Install counters and deadlines

1. Freeze RunPolicy, Registry and monotonic deadline at `run_task` start.
2. Count every physical Provider request, including retries, as one model attempt.
3. Count every accepted FunctionToolCall and every closed tool batch/round.
4. Before each request enforce the fixed order cancellation → run deadline → model attempts → tool rounds → context budget; check total/per-cycle tool calls only when a new batch is being admitted.
5. Before accepting a batch enforce per-cycle call count and minimum Cycle closure capacity.
6. If a legal batch exceeds remaining total tool calls:
   - accept the Assistant;
   - execute none;
   - write `budget_exhausted` for all calls in order;
   - publish skipped statuses;
   - close the Cycle and finish with `tool_call_limit`.
7. Use an injected monotonic clock for deterministic boundary tests.

### S2.19.3 — Bound ToolExecutor and each Cycle

1. Compute serialized Assistant tool-call size and reject pre-admission when the remaining Cycle budget cannot hold one minimal envelope per call. Finish with public `model_output_limit` and internal `stop_detail=tool_cycle_too_large`.
2. Allocate an equal per-call result limit from remaining Cycle space and the effective single-result cap; do not add rolling unused-quota accounting.
3. Truncate successful output before Log append, preserve a valid UTF-8/JSON envelope and include `original_chars`.
4. Bound/sanitize error messages and validation details; return at most `max_validation_errors` stable field-path errors.
5. Before each call:
   - check cancellation and run deadline;
   - compute `min(tool_timeout_seconds, remaining_run_seconds)`;
   - publish running;
   - execute with that timeout;
   - append outcome and publish succeeded/failed/cancelled;
   - check deadline again before the next call.
6. Convert timeout, known failure, unknown failure and output failure to the approved ToolErrorCode. Re-raise task cancellation to AgentLoop.
7. Prove a many-call batch cannot legally run beyond the total deadline.

### S2.19.4 — Complete cancellation and synthetic closure

1. Preserve Slice 1’s minimum unresolved-call closure as the common primitive; do not introduce a second cancellation path.
2. Define and test commit points for:
   - before User/log start;
   - model stream before any fragment;
   - model stream after text/tool progress;
   - after complete Assistant but before acceptance;
   - before first tool;
   - during a tool;
   - between tools;
   - after the last result;
   - before and after final Assistant append.
3. On model-stream cancellation, discard the incomplete Assistant and finish cancelled.
4. After a tool-call Assistant is accepted:
   - preserve completed results;
   - write `cancelled` for the running call;
   - write `cancelled` for unstarted calls;
   - publish running-call `status=cancelled` and unstarted `status=skipped,error_code=cancelled`;
   - finish only after the Cycle closes.
5. On deadline/tool-call budget:
   - write `budget_exhausted` for unstarted calls;
   - publish `status=skipped,error_code=budget_exhausted`.
6. Ignore a cancellation that arrives after final Assistant append; completed is already committed.
7. Ensure all paths append exactly one terminal record and emit exactly one public completion.
8. Add a post-cancel next-turn E2E for every cancellation phase that can leave accepted history.

### S2.19.5 — Add progress-aware model retry

1. Make Adapter/Provider errors carry normalized code plus `made_progress`; tool-call progress remains an opaque boolean, never raw fragments.
2. Retry only network/rate-limit/timeout (and explicitly classified invalid-response cases if the approved Adapter contract allows) when `made_progress=false`, retry budget and run deadline permit.
3. Emit `status.changed=retrying` for each retry but no new public turn start.
4. Do not write partial Assistant content for a failed attempt.
5. Map exhausted/fatal cases to one AgentStopCode and one public error.
6. Test text progress, tool-fragment progress, zero-progress transient failure, exhausted retry, auth failure and late cancellation.

### S2.19.6 — Add repeated-Cycle early stop

1. Compare only closed Cycles from the current real user turn.
2. Canonicalize valid JSON arguments; use trimmed raw text for invalid JSON.
3. Ignore call IDs and intermediate Assistant text.
4. Compare success state, truncation metadata and bounded content; compare failure code/retryable but not variable messages.
5. Detect repeated suffix patterns of length 1..`loop_max_pattern_cycles` and stop at `loop_repeat_limit`; changing arguments/results breaks equality.
6. Reset detector on the next real User.
7. Keep max_tool_rounds as the independent hard stop.
8. Cover A A A, A B A B A B, near-matches and changed-result cases.

### S2.19.7 — Finalize public stop/event contracts

1. Add `tool.status` to `PUBLIC_EVENT_TYPES`, then define its payload with call_id, name, running|succeeded|failed|cancelled|skipped, ordinal, total, optional error_code and truncated.
2. Add AgentStopCode:
   - provider_auth
   - provider_network
   - provider_rate_limit
   - provider_timeout
   - invalid_response
   - model_output_limit
   - content_filtered
   - context_budget
   - model_call_limit
   - tool_call_limit
   - run_timeout
   - loop_detected
   - internal
3. Define the fatal `error` payload as exactly a bounded `message` plus `stop_code: AgentStopCode`; remove the public Stage 1 `code: ModelErrorCode` field rather than carrying both. ModelErrorCode remains internal Adapter/Runner classification.
4. Require the fatal `error.stop_code` and following `turn.completed.stop_code` to be identical; migrate all Stage 1 tests/consumers that assert `payload["code"]`.
5. Keep `stop_detail` internal. Distinguish `provider_length` from `tool_cycle_too_large` in diagnostics/tests without adding public codes.
6. Extend `completion_payload` and `turn.completed` to carry finish_reason, visible text, text_length and required stop_code for error; stop/cancelled carry no stop_code. Update exact Stage 1 completion-payload assertions as an intentional contract migration.
7. Ensure public payloads never include complete arguments/results, tracebacks, reasoning or secrets.
8. Expand lifecycle validation for tool statuses, one fatal error maximum and exact event sequencing.

### S2.19.8 — Implement terminal segmentation

1. Render `text.delta` once and never re-render `turn.completed.text`.
2. Before the first tool status after mixed text, force a newline.
3. Render a bounded step marker such as `↳ 工具步骤 1/2：lookup_record`; do not show call ID by default.
4. Start final Assistant text on a new line after tool activity.
5. Keep error/cancel completion from replaying visible partial text.
6. Add the required offline `Terminal.show_event` sequence test:

   ```text
   text.delta → tool.status → text.delta → turn.completed
   ```

   Assert exact line boundaries, one final rendering and no payload leak.

### S2.19.9 — Enable the guarded production path

1. Load policy and Adapter capabilities in bootstrap, resolve RunPolicy for the active ModelRef and inject it into ContextBuilder/AgentLoop/ToolExecutor.
2. Construct the two approved demo tools with immutable in-memory data.
3. Pass the default registry only when the Adapter declares OpenAI function-tool support; otherwise preserve plain chat without pretending tools are available.
4. Ensure StructuredCompletion, Handoff, config extraction and Provider probing always pass no tools.
5. Test source-tree and built-package bootstrap, unsupported capability behavior and unknown-model fallback.

### S2.19.10 — Close the slice

1. Run focused policy/budget/deadline/cancellation/retry/loop/event/terminal/bootstrap tests.
2. Run every parent-plan quality gate and the Stage 1 product regression suite.
3. Inspect captured events, terminal output and Log envelopes for secret/raw-payload leakage.
4. Record exact validation evidence and any default-value changes justified by tests.
5. Mark complete and activate Subplan 20 only when the production tool path is bounded and recoverable.

## Required validation

- Every accepted batch closes under success, error, budget and cancellation.
- Every serial call uses remaining global deadline, not a fresh independent total.
- Retry never replays a progressed model request.
- Result/Cycle/request limits use the frozen effective policy.
- Unknown models never receive the requested 800k limit without an exact safe-size declaration.
- Loop detection stops true repeats but not changed work.
- Public events are ordered, bounded and secret-safe.
- Terminal output is segmented and never duplicated.
- A healthy next turn succeeds after every recoverable stop/cancel path.

## Completion criteria

- Bundled policy and exact-model resolution are validated and packaged.
- All budgets, output bounds and deadlines are enforced before unsafe work.
- Cancellation and failure cannot leave an open ToolCycle or duplicate completion.
- Progress-aware retry and repeated-Cycle stopping match the approved semantics.
- AgentStopCode/tool.status/terminal contracts have direct offline tests.
- Production bootstrap safely enables only the approved tools.
- Full Stage 1 and Stage 2 offline suites pass.

## Deliverables

- Bundled developer policy and frozen RunPolicy.
- Provider tool capability metadata.
- Complete budgets, deadlines and Cycle/result bounds.
- Exhaustive cancellation/synthetic closure matrix and retry control built on the Slice 1 primitive.
- Loop early-stop detector.
- Public tool/stop events and terminal segmentation.
- Guarded production bootstrap integration.
