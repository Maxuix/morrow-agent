# Subplan 05 — Context and Turn Runtime

> Stage: 1A  
> Status: pending  
> Parent: [Stage 1 implementation plan](../PLAN.md)

## Objective

Implement the single-turn model runtime and the sole context-construction path while preserving capability, privacy, ordering, retry, and cancellation boundaries.

## Prerequisites

- Subplans 02 through 04 are complete.

## Tasks

1. Define the Stage 1 fixed system boundary: identity, present capabilities, prohibition on pretending to access/modify/execute project content, and treatment of user state as data rather than authority.
2. Implement `ContextBuilder` ordering for fixed system content, an effective Preferences snapshot, Profile, an explicitly loaded Handoff revision, and accepted in-process messages. In Stage 1A the Preferences snapshot contains only system defaults plus an optional persisted global language initialized from terminal locale; workspace/session merge behavior is introduced only in Subplan 08.
3. Implement typed snapshot replacement so successful state edits can affect the next turn without rebuilding an unrelated session. Subplan 08 replaces the same Preferences snapshot slot with its three-layer resolver rather than adding another context path.
4. Implement an injectable, conservatively configured character-based context budget for Stage 1A that always preserves fixed system content, valid Profile/Handoff, and the current user input, then keeps the newest complete prior turns. Document the unit and keep the default adjustable from validation evidence.
5. Reject a single oversized current message rather than silently truncating it.
6. Implement `AgentRuntime.run_turn()` as one model call that translates model events into the public Stage 1 event lifecycle.
7. Add one automatic retry only for network/rate-limit/timeout failures before visible text; never replay after visible output and never retry cancellation.
8. Fix history admission rules: append the user message when the turn is accepted; append the complete assistant message only for `finish_reason=stop`; preserve the user message but do not append partial assistant output for `cancelled` or `error`; never replay cancelled/failed partial output into a later turn.
9. Implement bounded structured completion support with extraction, Pydantic validation, and at most one repair attempt. It must receive a purpose-specific snapshot from `ContextBuilder`; Handoff/config services may not assemble a second system/Profile/Handoff/history prompt path.
10. Add event, context, retry, cancellation, budget, and redaction tests.

## Verification

- A ten-turn scripted conversation reaches the Provider in original accepted-message order.
- Handoff content is absent until a specific revision is explicitly loaded.
- Unknown event fields/types are tolerated by consumers while lifecycle invariants remain enforced.
- No retry occurs after visible text or cancellation.
- Provider reasoning and unsanitized exceptions never enter context or public events.
- Oversized input fails before a Provider call.
- Cancellation and error preserve the accepted user message but exclude partial assistant text from later context.
- Both streaming and structured completion obtain any user state through `ContextBuilder`.

## Completion criteria

- `ContextBuilder` is the only model-context assembly path.
- `run_turn()` remains a single-turn primitive with no command, tool-loop, or persistent-session responsibilities.
- All runtime tests pass offline.

## Deliverables

- Context builder and budget policy.
- Single-turn runtime and public event translation.
- Validated structured-completion helper.
