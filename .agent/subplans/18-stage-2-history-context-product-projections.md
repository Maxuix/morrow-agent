# Subplan 18 — Stage 2 History, Context, and Product Projections

> Stage: 2
> Slice: 2 of 4
> Status: complete (2026-08-17)
> Parent: [Stage 2 implementation plan](../PLAN.md)
> Depends on: Subplan 17

## Objective

Complete ConversationLog/ToolCycle legality and make every model-facing consumer use an explicit, purpose-safe projection. Add deterministic old-result clearing and legal history trimming without mutating the Log, Session or Handoff, then prove Stage 1 Handoff, configuration and session flows remain correct in the presence of tool history.

## Required design decisions

1. ConversationLog remains the only mutable history; ToolCycle and public-turn units are derived from immutable snapshots, not stored twice.
2. Log validation is incremental while appending an open Cycle and strict for a completed View/Provider request.
3. Chat, Structured and Handoff fallback projections are separate named paths. A generic “messages” list must not silently serve all three purposes.
4. Current User is already in the Log. ContextBuilder no longer accepts `current_user` or conditionally appends a duplicate.
5. Request size is computed from the Adapter’s canonical serializer over messages plus tools. Context code does not import SDK objects or maintain a second approximation/default; until RunPolicy lands, the composition root explicitly injects the existing Stage 1 limit as a named compatibility bridge.
6. Reduction order is deterministic: clear every result in the oldest closed Cycle as one unit, oldest first; only then hard-trim the oldest legal history.
7. No reduction changes the snapshot or persisted Handoff, and Stage 2 never invokes an LLM summary.

## Executable tasks

### S2.18.1 — Complete ConversationLog and ToolCycle validation

1. Expand the Log state machine to enforce the full public-turn grammar:

   ```text
   User
   → 0..N ClosedToolCycles
   → optional final no-tools Assistant
   → terminal(completed|cancelled|failed)
   ```

2. Derive immutable ToolCycle/public-turn views from records, including open/closed status and unresolved call IDs.
3. Reject unknown, duplicate, orphan, missing and out-of-order results; reject duplicate call IDs inside one Assistant; reject User/Assistant/terminal crossing an open Cycle.
4. Require completed turns to contain a final no-tools Assistant. Allow cancelled/failed turns to end without one only after every accepted call is closed.
5. Ensure `finish_turn` stores the exact interrupted call IDs supplied by Runtime and that terminal records never appear in `messages_view`.
6. Add exhaustive single-call, multi-call and malformed-order tests plus snapshot immutability tests.

### S2.18.2 — Define explicit projection contracts

1. Introduce immutable `ContextRequest`/`ContextPack` (or equivalently explicit typed method arguments/results) carrying purpose, snapshot, user state, tools, effective request limit and projection diagnostics.
2. Implement three explicit projections:
   - **Chat View:** fixed System Boundary, dynamic user-state System message, legal user/assistant/tool history and request tools;
   - **Structured View:** real Users plus only completed turns’ final no-tools Assistant text; no ToolMessage, tool-call Assistant, cancelled/failed partial output or tools;
   - **Handoff Fallback View:** most recent real User and most recent completed final Assistant only.
3. Update the fixed System Boundary to allow only request-provided tools, classify tool/state data as untrusted context and continue denying file/Shell/Git/network claims.
4. Keep fixed boundary and dynamic user state as separate SystemMessage objects.
5. Add tests proving tool envelope text cannot enter command/config routing, structured prompts or Handoff fallback.

### S2.18.3 — Share canonical request sizing

1. Expose a pure canonical OpenAI-compatible request serializer/estimator from the Adapter layer without making SDK calls.
2. Inject `estimate_request_chars(messages, tools)` into ContextBuilder from composition root; do not create a premature multi-Adapter Protocol.
3. Remove `ContextBuilder(max_chars=24000)` as an implicit constructor default. Require an explicit request-character limit at the Context/Bootstrap boundary.
4. Until S2.19.1 resolves RunPolicy, have the composition root pass the existing Stage 1 value (24000) through one clearly named compatibility injection; no ContextBuilder fallback or second numeric source is allowed.
5. Count every serialized field: nullable content, IDs, names, raw arguments, ToolMessage content and Pydantic-generated schemas.
6. Re-run the same estimator immediately before Provider dispatch and reject oversize payloads before network access.
7. Add tests where content-only counting would fit but actual tool wire exceeds the limit, plus a test proving construction without an explicit limit fails instead of silently choosing 24000.

### S2.18.4 — Implement deterministic result clearing

1. Parse the Chat View into closed ToolCycles without modifying source records.
2. When over budget, visit closed Cycles in strict chronological order.
3. For one selected Cycle, replace all ToolMessage contents together with the fixed placeholder:

   ```text
   [tool result omitted from active context: budget]
   ```

4. Preserve the Assistant tool calls, every ToolMessage, call ID and order.
5. Never clear an open Cycle. Never write placeholders back to ConversationLog or use them for loop detection/Structured View.
6. Report `cleared_cycle_count` in ContextPack and cover multi-result Cycle atomicity.

### S2.18.5 — Implement legal hard trimming

1. Run hard trimming only after all eligible old-result clearing needed by the budget.
2. Protect fixed System messages, Profile/Preferences/loaded Handoff, current real User, current open Cycle and request ToolDefinitions.
3. Drop oldest whole public turns first.
4. If a single current/retained turn remains too large, drop its oldest closed ToolCycles as whole units while retaining that turn’s original User.
5. Never split a message/Cycle, leave orphan Assistant/Tool, skip newer history to retain older history or drop the current User.
6. Fail with typed `context_budget` before Provider invocation if the protected set cannot fit.
7. Return `dropped_record_count` and validate the final View again for size and tool pairing.
8. Add boundary-table tests for old completed turns, cancelled/failed turns, multi-Cycle long turns, oversized newest units and protected-set overflow.

### S2.18.6 — Migrate StructuredCompletion and Handoff

1. Remove all construction via `type(context.messages[0])`; append an explicit UserMessage instruction.
2. Make `complete_structured()` request Structured View and send no tools.
3. Make Handoff generation consume Structured View only.
4. Make deterministic Handoff fallback consume the fallback projection:
   - latest real User may become current goal, including from cancelled/failed turn;
   - latest completed final Assistant may enter recovery note;
   - ToolMessage and intermediate/mixed tool-call text never enter recovery note.
5. Preserve explicit continuation rules, existing Handoff Schema and dirty-clear-on-success behavior.
6. Ensure automatic context reduction never writes Handoff YAML or rewrites `current_goal`.

### S2.18.7 — Reconcile Stage 1 product readers and fixtures

1. Remove remaining production reads that depend on mutable history structure.
2. Migrate test fixtures to ConversationLog builders or AgentLoop; direct fixture append helpers must enforce the same Log invariants.
3. Verify `/new` and Session reset clear Log/runtime state but preserve persisted Handoff and workspace state.
4. Verify `/handoff update`, continuation exit save, independent-session fallback and config extraction with existing tool history.
5. Starting from Slice 1’s accepted-tool-call cancellation/internal-closure fixtures, verify a cancelled/failed tool turn does not poison a later ordinary chat; do not satisfy this item with model-stage cancellation alone.
6. Verify `/continue` loads only Handoff and starts with an empty ConversationLog.

### S2.18.8 — Close the slice

1. Run focused Conversation/Context/Structured/Handoff/orchestration tests.
2. Run the full parent-plan quality gates.
3. Inspect Provider payload captures for legal pairing, no terminal records, no placeholders in source Log and no tool data in structured calls.
4. Record exact validation results and any algorithm clarification in the roadmap/LOG.
5. Mark complete and activate Subplan 19 only when all product projection regressions pass.

## Required validation

- Incremental Log validation permits only the one currently open Cycle; strict View validation permits none.
- Chat View remains valid after every clearing/trimming combination.
- ContextBuilder is demonstrably pure: source snapshots, Session and Handoff compare equal before/after build.
- Structured and fallback projections contain no ToolMessage or tool-call Assistant.
- The actual serialized request is within its injected limit.
- The sole temporary 24000 source is explicit at composition, and ContextBuilder itself has no numeric default.
- Handoff/config/exit/new/continue behaviors retain Stage 1 semantics.
- No summary model, persistent history or artifact reinjection is introduced.

## Completion criteria

- Full ToolCycle/public-turn legality is enforced at append, View and pre-send boundaries.
- ContextBuilder emits purpose-specific immutable Views and never duplicates current User.
- Old results are cleared and old history trimmed only at legal semantic boundaries.
- Handoff/StructuredCompletion cannot consume tool envelopes or intermediate model output.
- All Stage 1 product readers use the Log or an explicit projection without reintroducing a second writer.
- Complete offline regression and boundary gates pass.

## Deliverables

- Complete ConversationSnapshot/ToolCycle derivation and validation.
- Chat, Structured and Handoff fallback projections.
- Canonical serialized request sizing.
- Deterministic Tool Result clearing and atomic history trimming.
- Migrated Handoff, StructuredCompletion and session product flows.
