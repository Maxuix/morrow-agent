# S7P-06 Pi-Parity Long-Horizon Acceptance

Status: implemented; amended by Subplan 93 to make v2 the only runtime policy.

Subplan 93 verification: focused runtime/policy/preparation tests passed `139`; the complete offline
gate passed `1362 passed, 2 deselected` in 111.45 seconds. Ruff format/check, compileall, both CLI
help commands and `git diff --check` passed. No Live Provider request was run.

The behavioral reference is Pi Agent 0.84.2 at commit
`209bc7b9a89b01c8fd05861cf5bbdda3e300037a`. This document is the checked-in
parity table and final offline evidence for the completed subplan. It does not
claim a live same-model comparison.

## Pinned parity table

| Contract | Pinned Pi evidence | Observed behavior | Morrow owner / test seam | Classification |
|---|---|---|---|---|
| Long-horizon loop | [`runLoop`](https://raw.githubusercontent.com/earendil-works/pi/209bc7b9a89b01c8fd05861cf5bbdda3e300037a/packages/agent/src/agent-loop.ts#L144-L256) | Continue while a completed assistant response has tool calls; a tool-free response, abort or error ends the loop. No cumulative counters or task deadline are in the loop. | `morrow.runtime.agent.AgentLoop`; `test_v2_loop_can_repeat_and_exceed_retired_caps_before_normal_stop` | Adapt |
| Host stop boundary | [`shouldStopAfterTurn`](https://raw.githubusercontent.com/earendil-works/pi/209bc7b9a89b01c8fd05861cf5bbdda3e300037a/packages/agent/src/agent-loop.ts#L229-L238) | An optional host decision is checked after a completed turn. It is absent by default and is not a product timeout. | `AgentLoop` host hook; `test_v2_host_stop_hook_is_optional_and_keeps_cancelled_history_legal` | Adapt |
| Compaction defaults | [`DEFAULTS`](https://raw.githubusercontent.com/earendil-works/pi/209bc7b9a89b01c8fd05861cf5bbdda3e300037a/packages/coding-agent/src/core/compaction/compaction.ts#L125-L135) | Compaction is enabled with `reserveTokens=16384` and `keepRecentTokens=20000`. | `morrow.runtime.compaction`; token-window tests | Adopt |
| Compaction threshold and estimator | [`shouldCompact` / estimator](https://raw.githubusercontent.com/earendil-works/pi/209bc7b9a89b01c8fd05861cf5bbdda3e300037a/packages/coding-agent/src/core/compaction/compaction.ts#L193-L294) | Trigger when `contextTokens > contextWindow - reserveTokens`; use provider usage when available and the Pi estimator otherwise. | Exact capabilities, `ContextBuilder`, compaction tests | Adapt |
| Compaction boundaries and chain | [`prepareCompaction`](https://raw.githubusercontent.com/earendil-works/pi/209bc7b9a89b01c8fd05861cf5bbdda3e300037a/packages/coding-agent/src/core/compaction/compaction.ts#L689-L889) | Keep complete tool call/result units, retain the recent tail, and include a prior summary when compacting again. | Immutable `pi_compaction` checkpoint entries plus model-visible projection; restart codec test | Adapt / Harden |
| Structured summary | [`SUMMARY_GUIDANCE`](https://raw.githubusercontent.com/earendil-works/pi/209bc7b9a89b01c8fd05861cf5bbdda3e300037a/packages/coding-agent/src/core/compaction/compaction.ts#L444-L509) | Summary sections are Goal, Constraints & Preferences, Progress, Key Decisions, Next Steps, Critical Context, Files Read and Files Modified. | Bounded `CompactionSummary`; summary-provider tests | Adopt / Harden |
| Overflow recovery | [`_checkCompaction`](https://raw.githubusercontent.com/earendil-works/pi/209bc7b9a89b01c8fd05861cf5bbdda3e300037a/packages/coding-agent/src/core/agent-session.ts#L1843-L1955) | One context-overflow compaction recovery is attempted; a second overflow is terminal. | AgentLoop overflow seam; one-recovery test | Adapt |
| Provider retry | [`_retryAfterError`](https://raw.githubusercontent.com/earendil-works/pi/209bc7b9a89b01c8fd05861cf5bbdda3e300037a/packages/coding-agent/src/core/agent-session.ts#L2533-L2625) and [`settings`](https://raw.githubusercontent.com/earendil-works/pi/209bc7b9a89b01c8fd05861cf5bbdda3e300037a/packages/coding-agent/src/core/settings-manager.ts#L10-L30) | Retry transient failures up to 3 times with 2s, 4s and 8s exponential delays, cap provider-directed delay at 60s, abort backoff immediately, and reset retry state after success. | Single AgentLoop retry owner; `test_v2_retry_uses_provider_delay_cap_and_does_not_duplicate_history` | Adapt |
| Tool truncation constants | [`truncate.ts`](https://raw.githubusercontent.com/earendil-works/pi/209bc7b9a89b01c8fd05861cf5bbdda3e300037a/packages/coding-agent/src/core/tools/truncate.ts#L0-L258) | Shared defaults are 2000 lines and 50 KiB; head truncation is for reads/search, tail truncation is for command output, and UTF-8 byte boundaries are preserved. Grep lines use 500 characters. | `morrow.runtime.truncation`; service adapters and fixture tests | Adapt / Harden |

## Retired v1 surface

Subplan 93 removed the former v1 resolver and its cumulative rounds, attempts, calls, task deadline,
Cycle character budget and strict repeated-cycle detector. The packaged policy and user overlay no
longer accept those fields. `RunPolicy` accepts schema version 2 only; a v1 snapshot fails validation
instead of selecting a compatibility loop. Per-tool timeout, context/result bounds, compaction,
Provider retry, cancellation, steering and durable observations remain active.

## Explicit Morrow adaptations and safety boundaries

| Area | Morrow decision | Reason |
|---|---|---|
| Runtime mode selection | Configured, injected and restored runs accept v2 only. Exact `context_window_tokens` enables token thresholds; otherwise the 256-KiB conservative character boundary triggers compaction and token-window telemetry remains absent. Known maximum output expands the reserve. | Missing metadata must not disable long-horizon compaction, but Morrow must not invent or report a token window. |
| Summary ingestion | Provider summary text may include a code fence, surrounding prose, trailing container commas, `null` list fields, or unknown fields. Provider-only parsing normalizes those forms before validation; durable `CompactionSummary` and checkpoint decoding still reject unknown fields and enforce size/secret/type boundaries. | Presentation noise should not terminate an otherwise recoverable compaction, while stored state remains strict. |
| Durable history | Compaction uses the existing immutable `ContextCheckpoint` repository with a `pi_compaction` codec; `ConversationLog` and durable ToolCycles are never rewritten. | Preserve the single chat-history authority and restart/recovery invariants. |
| Host stop result | A host stop at the completed-tool-cycle boundary is represented by Morrow's existing `cancelled` finish reason and status payload. | Reuse the frozen public event lifecycle instead of adding a new event type. |
| Tool output artifacts | Model-visible results remain bounded and carry a safe Artifact reference. The Artifact stores only bounded captured bytes after secret redaction; older fake adapters fall back to their bounded tail capture. | Omitted output must be fetchable without persisting raw streams or exposing credentials. |
| Evaluation watchdog | No product loop watchdog was added. Equal external watchdogs for Pi/Morrow remain a future S7P-09 harness concern. | A harness timeout must not become an Agent stop code or hidden runtime policy. |

## Phase A scripted trace catalog

The offline fixture catalog uses deterministic providers and fake clocks. The
covered traces are: 513 tool cycles beyond the retired call caps;
repeated cycles followed by a tool-free success; strict threshold accounting and
split-turn/tool-pair compaction; structured summary and bounded summary retries;
one overflow recovery; transient retry with provider delay/cap; head/tail UTF-8
truncation; and model-visible, redacted, chunk-readable Artifact output. Live
Provider/Pi A/B remains intentionally deferred to S7P-09 or separate authorization.

## Evidence log

Phase A reference evidence was captured before production edits. The dedicated
S7P-06 suite passes `12`; the expanded parity/tool-contract/artifact/permission
matrix passes `73`; the process/sandbox/capability/parity matrix passes `38`;
the cancellation, migration and compatibility regressions pass after the final
收尾修复; and the repository offline gate passes `1272 passed, 2 deselected`.
`uv sync`, Ruff format/check, compileall, both CLI-help entrypoints and
`git diff --check` pass. The formal read-only review and all reproduced repairs
are recorded below. No live Provider/model/Pi/MCP/network/credential run is
included.

## Post-review closeout

Kuhn (`01a042d6-4ef4-77e1-807c-3163f3875d63`, `gpt-5.6-luna`, reasoning `max`)
reviewed the activation-base-to-`04333c9` implementation range and returned
`REQUEST CHANGES` with no P0. The four confirmed findings were independently reproduced and
closed in `d230126`:

- P1: automatic compaction now re-checks the rebuilt token-window requirement and rejects a
  non-advancing boundary instead of admitting an oversized retained context.
- P2: oversized UTF-8 file lines and byte-window continuations always make progress.
- P2: Artifact reads align requested offsets to UTF-8 character boundaries and never expose an
  incomplete trailing code point.
- P2: retry transitions use explicit bounded durable counters, so resume does not count unrelated
  terminal failures as transient retries or lose summary-retry counts.

Final verification is `uv run pytest -m 'not live' -q --tb=short` → `1272 passed, 2 deselected`
in `121.09s`; `uv run ruff format --check .`, `uv run ruff check .`,
`uv run python -m compileall -q src tests`, `uv run morrow --help`,
`uv run morrow run --help`, and `git diff --check` all pass. No live test was run.
