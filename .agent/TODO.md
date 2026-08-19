# TODO

## Current stage

Stage 4 production implementation is active at the tool execution journal. Durable no-tool Session
history has landed; Full Access remains inactive.

## Active subplan

Subplan 38 — Tool Execution Journal and Durable Approval.

## Tasks

- [>] S4.38.1 Define ToolExecution, Approval, EffectClass, recovery declaration, structured tool
  facts, transition models, and the named test-only fault-injector port with the ADR's exact
  payload/redaction budgets.
- [ ] S4.38.2 Add journal/approval schemas and constraints for ordered calls, intent hashes, schema/
  permission digests, result evidence, row versions, expiry, resolution, and consumption.
- [ ] S4.38.3 Persist Assistant tool call plus all ordered execution intents atomically through the
  durable ConversationLog boundary before dispatch, including pre-effect file/config evidence such
  as before hash, expected-after hash, expected size, target/parent conditions, and truncation facts.
- [ ] S4.38.4 Integrate approval creation/resolution so consume and `executing` transition are one
  transaction, expiry uses the injected production Clock, Terminal resolution delegates through the
  same application path, and duplicate/stale decisions are deterministic.
- [ ] S4.38.5 Persist handler success/failure/cancellation as `handler_completed`, then append
  ToolMessage and close each ordered execution through ConversationLog.
- [ ] S4.38.6 Hand-classify every current production tool independently of `ToolEffect`; Host and
  native sandbox default to externally effectful/unknown, and composition fails if any registered
  tool lacks a durable declaration.
- [ ] S4.38.7 Add fault-point, redaction, payload-boundary, transition, and Stage 3 regression tests;
  document the protocol.

Only Subplan 38 may be executed. Recovery, artifacts, grants, and Full Access remain inactive.
