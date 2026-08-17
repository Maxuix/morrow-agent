# Subplan 17 — Stage 2 Walking Skeleton

> Stage: 2
> Slice: 1 of 4
> Status: completed 2026-08-16 (all gates green; E2E and single-authority checks pass)
> Parent: [Stage 2 implementation plan](../PLAN.md)
> Depends on: completed Stage 1 final-tree acceptance

## Objective

Create the first integrated model → tool → model vertical slice while atomically migrating ordinary chat to one AgentLoop and one Session-owned ConversationLog. The slice is complete only when a scripted Provider can perform at least two dependent tool steps, return a final answer, preserve a legal history, and ordinary no-tools chat passes through the same runtime.

This slice establishes protocol shape, the sole write path and minimum safe ToolCycle closure. Full context reduction, operational budgets/deadlines, the exhaustive cancellation commit-point matrix, loop detection and terminal tool UX belong to later slices.

## Required design decisions

1. The old directory-name Stage 1 guard is replaced before Stage 2 modules are added. The replacement tests behavior: the default Stage 2 registry contains only approved in-memory tools, does not mutate a temporary workspace or use network, does not persist ConversationLog, and exposes no file/Shell/Git/MCP/Skill capability.
2. Message protocol objects use explicit discriminated variants. Existing calls to `Message(role=...)` migrate to `SystemMessage`, `UserMessage`, `AssistantMessage` or `ToolMessage`; protocol variants are frozen, reject extras and use tuples for ordered collections.
3. Adapter stream assembly is provider-specific. The Core/Runtime never reads SDK fragment indexes or raw SDK objects.
4. Session owns one ConversationLog from this slice onward. A retained `Session.messages` is a read-only derived tuple; `accept_user()` and `accept_assistant()` are removed or made private to the Log migration and cannot remain public writers.
5. `AgentLoop.run_task()` owns user/assistant/tool/terminal history and public task lifecycle. `AgentRuntime.run_turn()`, if retained for compatibility, delegates to `run_task(..., tools=empty)` and has no independent lifecycle, retry or Session mutation.
6. Tool support is injectable for the E2E but remains disabled in the default production bootstrap until Subplan 19 installs policy/guardrails. This avoids exposing an unbounded production tool loop while keeping the vertical slice executable.
7. The minimal ToolExecutor already uses Pydantic strict argument models and deterministic compact JSON envelopes. Slice 1 must close unresolved calls with `cancelled` after task cancellation and `internal` after an unexpected post-admission exception; Subplan 19 adds timeout/budget closure, public tool statuses and the exhaustive commit-point matrix without changing the wire.

## Intended module ownership

Exact file splits may stay thin, but responsibilities must land in these existing layers:

| Responsibility | Primary location | Notes |
|---|---|---|
| Message/tool wire and finish enums | `morrow.core` | no SDK, Session or terminal dependencies |
| ModelProvider port and internal model events | `morrow.core.ports` / Core DTOs | tools are optional; complete() stays text-only |
| OpenAI request serializer and fragment accumulator | `morrow.adapters.models.openai_compatible` | explicit field whitelist |
| ConversationLog and records | `morrow.runtime` | process-local controlled append |
| Registry, executor and demo tools | `morrow.runtime` or a thin application-facing runtime module | no UI/history writes |
| ModelCallRunner and AgentLoop | `morrow.runtime.agent` or adjacent runtime module | one task state machine |
| Session/orchestrator/bootstrap migration | existing runtime/application/bootstrap modules | no second chat path |
| scripted model/tool test doubles | `morrow.testing` and tests | deterministic and offline |

Do not create empty packages. Split a responsibility into a new file only when the same change provides behavior and tests.

## Executable tasks

### S2.17.1 — Freeze the baseline

1. Run and record the complete non-Live suite, Ruff format/check and compileall before changing production code.
2. Capture the current direct-history and runtime call sites with `rg`; use the inventory as a migration checklist.
3. Confirm no unrelated worktree changes overlap the Stage 2 files.

### S2.17.2 — Replace the stage-boundary guard first

1. Remove the test that rejects directories by name.
2. In the same first change, add a baseline-green capability guard that inspects constructed application behavior/state rather than future directory names:
   - no file, Shell, Git, network, browser, MCP or Skill tool is registered or exposed;
   - no persistent ConversationLog/summary field or state document exists;
   - ordinary application construction remains Stage 1-compatible.
3. Keep that replacement green before any new runtime/tool module is introduced.
4. Extend the same guard, without weakening its original assertions, when later Slice 1 tasks add behavior:
   - the enabled Stage 2 registry names are exactly `lookup_record` and `calculate`;
   - handlers operate only on injected memory data and deterministic arithmetic;
   - executing them under NetworkGuard leaves a temporary workspace tree byte-for-byte unchanged;
   - Session construction/restart does not read or restore ConversationLog.

### S2.17.3 — Introduce the Core protocol

1. Add `ToolDefinition`, nested function definition/call objects and `FunctionToolCall`.
2. Replace the monolithic Message model with the four explicit role variants and a discriminated `Message` union.
3. Enforce:
   - function tool names match `[A-Za-z0-9_-]{1,64}`;
   - descriptions and required IDs/names are non-empty;
   - Assistant has non-empty content or at least one tool call;
   - call IDs are unique within one Assistant;
   - arguments stay an untouched string;
   - extras are rejected and ordered collections are immutable tuples.
4. Separate `ModelFinishReason` from public `FinishReason`.
5. Extend internal Provider completion events to carry a fully assembled AssistantMessage and normalized model finish reason without exposing SDK objects/reasoning.
6. Migrate all Stage 1 explicit message construction and tests; do not use type inference from an existing message.

### S2.17.4 — Extend the Provider port and OpenAI-compatible Adapter

1. Extend streaming requests with optional ordered tools; text-only/structured `complete()` sends none.
2. When tools are present, the Adapter sends `tool_choice="auto"`; when absent it omits both tools and tool_choice.
3. Implement one canonical explicit serializer for messages and tools:
   - System/User: role + content;
   - Assistant: role + content + tool_calls, with `content:null` for pure calls;
   - Tool: role + tool_call_id + content;
   - no internal, SDK, event, reasoning or unknown fields.
4. Implement the OpenAI-compatible accumulator:
   - ignore usage-only chunks;
   - accept one logical choice;
   - accumulate text in order;
   - assemble interleaved tool-call fragments by vendor index;
   - retain the first non-empty ID and reject a conflicting ID;
   - concatenate name/arguments in arrival order;
   - require type=function and sort completed calls by index;
   - normalize stop-with-calls to tool_calls;
   - reject duplicate/missing IDs, empty names, invalid types, non-string arguments and missing/conflicting finish.
5. Track whether any text or tool fragment was observed so later retry policy can distinguish progress without exposing fragments to Runtime.
6. Add table-driven fake-SDK tests for text, pure calls, mixed content, multiple interleaved calls, malformed streams, `length`/`content_filter`/unknown finish, argument-string fidelity and serialize-after-assemble round trip.

### S2.17.5 — Atomically establish ConversationLog, Session authority, and the no-tools AgentLoop

1. Add immutable `MessageRecord`, `TurnTerminalRecord` and `ConversationSnapshot` DTOs with Log sequence independent of AgentEvent sequence.
2. Implement the minimal controlled API: `begin_turn`, `append_assistant`, `append_tool_result`, `finish_turn`, `snapshot`, `messages_view`, `reset`.
3. Enforce one active turn, one opening User, ordered results and no terminal while a ToolCycle is open.
4. Make Session own the Log and derive `messages: tuple[Message, ...]` from it.
5. In the same production change, introduce ModelCallRunner plus a minimal no-tools `AgentLoop.run_task()` that owns begin-turn, final Assistant/terminal append and the one-start/one-completion lifecycle.
6. Immediately make `AgentRuntime.run_turn()` a thin `run_task(..., tools=empty)` delegate (or remove it), and route SessionOrchestrator ordinary chat through that same loop.
7. Preserve Stage 1 no-tools cancellation and zero-progress transient retry behavior through the explicitly injected compatibility retry limit; no independent Runtime lifecycle or write path may survive this task.
8. Migrate dirty semantics: accepting the real User marks dirty; only Handoff publication, reset or explicit discard clears it.
9. Update reset/new paths to reset the Log and session-level preferences without touching persisted Handoff.
10. Remove every production writer other than AgentLoop/ConversationLog. Existing ContextBuilder/Handoff readers may consume the read-only messages projection until Subplan 18.
11. Add tests proving snapshots/messages are deeply read-only, sequences are monotonic, direct mutation is impossible, plain chat uses AgentLoop and Session has no transitional double write.

### S2.17.6 — Add the minimal Registry, Executor and demo tools

1. Define frozen `RegisteredTool` and a task-frozen registry with exact unique lookup and name-sorted definitions.
2. Generate ToolDefinition parameters from each Pydantic arguments model.
3. Parse with `model_validate_json(..., strict=True)`, reject extras/coercion and call handlers only after validation.
4. Define the stable compact success/error envelope and basic ToolExecutionOutcome boundary; do not leak tracebacks or raw exceptions.
5. Define at least the Slice 1 ToolErrorCodes needed for deterministic outcomes, including `cancelled` and `internal`; `asyncio.CancelledError` is re-raised to AgentLoop.
6. Implement:
   - `lookup_record(dataset: plans|regions, key: non-empty str)` over an injected immutable mapping;
   - `calculate(operation, values[2..32])` with ordered arithmetic, no `eval`, finite numbers only and deterministic formatting.
7. Cover valid results, malformed JSON, type/range/extra-field errors, unknown tool, not-found, divide-by-zero and handler failure. Runtime must not auto-retry.

### S2.17.7 — Extend the single AgentLoop through tools and minimum safe closure

1. Extend the no-tools AgentLoop from S2.17.5 to accept a task-frozen Registry/tools collection without creating another runtime path.
2. Extend `run_task` handling:
   - handle final text by appending Assistant then terminal(completed);
   - handle tool calls by appending one Assistant batch, executing calls in original order, appending one result per call, then invoking the model again;
   - on pre-admission invalid model output, append no partial Assistant and finish failed;
   - emit exactly one `turn.completed`.
3. Add minimum post-admission closure before any tool-capable E2E is accepted:
   - on `asyncio.CancelledError`, preserve completed results, append `cancelled` envelopes for every unresolved call in original order, record those IDs, then terminal(cancelled);
   - on an unexpected `Exception` after batch admission, preserve completed results, append bounded `internal` envelopes for every unresolved call, record those IDs, emit one fatal error, then terminal(failed);
   - never append terminal or admit the next User while any call remains unresolved.
4. Test cancellation before the first result, cancellation after a partial multi-call result set, and an unexpected post-admission exception; each must leave a closed Log and allow a healthy next turn.
5. Keep remaining Slice 1 containment deliberately small. Do not add deadlines, `budget_exhausted`, `tool.status` or the full commit-point matrix here.
6. Do not enable the production default tool registry until Subplan 19 supplies all budgets and observability.
7. Commands, config extraction, Handoff and Provider tests remain outside the chat state machine and never write ConversationLog.

### S2.17.8 — Prove the vertical slice

1. Upgrade `ScriptedModelProvider` (or add a focused scripted double) to record messages/tools and emit completed Assistant objects.
2. Add an offline E2E story:
   - lookup plan price;
   - lookup region tax;
   - calculate the three-month tax-inclusive total;
   - return final text.
3. Assert at least two tool rounds, ordered Provider requests, exact call/result pairing, final answer, one start/completion pair, no terminal records in Provider payload and one Session history source.
4. Add a no-tools ordinary-chat E2E through AgentLoop and SessionOrchestrator.
5. Add mixed-content persistence coverage: intermediate Assistant text remains history, but only the final no-tools Assistant completes the turn.
6. Reassert the minimum cancellation/internal-closure cases through the integrated scripted path and verify the next user turn succeeds without Session reset.

### S2.17.9 — Close the slice

1. Migrate affected Stage 1 tests/fixtures to explicit messages and the Log without weakening assertions.
2. Run focused Core/Adapter/Conversation/Tool/Loop/orchestrator tests.
3. Run all parent-plan quality gates.
4. Record implementation decisions and exact validation results.
5. Mark this subplan complete only when the E2E and single-authority checks pass; then activate Subplan 18 and replace TODO with its tasks.

## Required validation

- Core rejects every malformed wire variant without mutating history.
- OpenAI serializer/accumulator round trip is deterministic and does not leak unknown fields.
- Session has one write path from the first integrated commit.
- Plain chat and tool chat both use AgentLoop.
- The scripted multi-step story completes with legal history.
- Tool-stage cancellation or unexpected post-admission failure closes every unresolved call before terminal and permits the next turn.
- Default production bootstrap does not yet expose an unbounded tool loop.
- Capability boundary tests replace the old directory-name assertion and remain green.
- Complete Stage 1 offline regression remains green.

## Completion criteria

- The complete model → tool → model → final-text E2E passes offline.
- One response may contain multiple ordered calls and all results are paired.
- Cancellation and unexpected exception paths cannot strand the Session in an open Cycle.
- No mutable `Session.messages` or independent `run_turn()` history writer remains.
- Partial/malformed Provider responses do not enter the Log.
- All protocol data crosses the Adapter via explicit whitelists.
- The approved demo tools are deterministic and have no local/network side effects.
- No Stage 3/4/5 capability is introduced.

## Deliverables

- Core OpenAI-compatible function-call message/request types.
- OpenAI-compatible tool request serializer and stream accumulator.
- Session-owned process-local ConversationLog.
- Minimal Registry/Executor and two in-memory tools.
- Single AgentLoop with plain-chat compatibility.
- Minimum deterministic `cancelled`/`internal` unresolved-call closure.
- First Stage 2 vertical E2E and capability-boundary regression suite.
