# Subplan 38 — Tool Execution Journal and Durable Approval

> Status: pending
> Prerequisite: Subplan 37 accepted
> Owns: persist-before-effect protocol, durable tool states, and one-shot approval consumption

## Objective

Make every ToolCycle crash-explainable by committing the complete tool intent before any side
effect, recording approval and handler completion separately, and closing conversation history only
through ConversationLog.

## In scope

- Durable ToolCall/ToolExecution/Approval models, schemas, and narrow journal ports.
- Independent `EffectClass` and recovery-policy declaration for every production tool.
- Sanitized/bounded durable argument intent, schema digest, permission digest, side-effect evidence,
  result envelope, and state transitions.
- One transaction for Assistant tool-call message, ordered ToolExecutions, run status, and any
  application events that describe that mutation.
- Approval request/resolution/expiry/atomic consume with optimistic row version.
- ToolExecutor/AgentLoop integration: no handler before committed intent; `handler_completed` is not
  the same as persisted ToolMessage/closed ToolCycle.
- Deterministic cancellation and error closure.

## Out of scope

- Recovery decisions and tool-specific reconciliation implementation (Subplan 39).
- Full Access grants (Subplan 44).
- Background approval, remote approval, approval nonce, event delivery outbox, or public lifecycle
  changes.

## Tasks

- [ ] S4.38.1 Define ToolExecution, Approval, EffectClass, recovery declaration, and transition
  models with strict payload/redaction budgets.
- [ ] S4.38.2 Add journal/approval schemas and constraints for ordered calls, intent hashes, schema/
  permission digests, result evidence, row versions, expiry, resolution, and consumption.
- [ ] S4.38.3 Persist Assistant tool call plus all ordered execution intents atomically through the
  durable ConversationLog boundary before dispatch.
- [ ] S4.38.4 Integrate approval creation/resolution so consume and `executing` transition are one
  transaction and duplicate/stale decisions are deterministic.
- [ ] S4.38.5 Persist handler success/failure/cancellation as `handler_completed`, then append
  ToolMessage and close each ordered execution through ConversationLog.
- [ ] S4.38.6 Classify every current production tool and fail composition if a registered tool lacks
  a durable effect/recovery declaration.
- [ ] S4.38.7 Add fault-point, redaction, payload-boundary, transition, and Stage 3 regression tests;
  document the protocol.

## Locked contracts

- The handler cannot begin unless the committed execution row is observable from a fresh
  connection.
- Tool calls in one Assistant message retain provider order; results close in that order under the
  existing ConversationLog grammar.
- Approval identity is opaque and bound to intent, schema, permission snapshot, granted subset,
  expiry, and row version. One approval can be consumed once.
- Durable argument/result material is the minimum bounded redacted evidence needed for recovery and
  user explanation, never an unbounded copy of provider or command data.
- `ToolEffect` may remain for current policy decisions; crash semantics use the independent durable
  classification.

## Tests and faults

- crash/exception before and after intent commit, approval creation, approval resolution/consume,
  handler entry, handler result commit, ToolMessage append, and ToolCycle close;
- duplicate, stale, expired, mismatched, and already-consumed approval decisions;
- multiple ordered tool calls with mixed approve/deny/fail/cancel outcomes;
- side-effect spy proves zero calls when intent commit or approval consume fails;
- exact payload/redaction boundaries and missing classification at composition;
- no changes to existing public event cardinality before Subplan 43.

## Completion gate

Every production tool invocation has a committed, bounded, auditable intent before dispatch; every
approval is one-shot and evidence-bound; handler completion and ToolCycle closure are separately
visible; a crash can leave only documented states that Subplan 39 knows how to classify.

## Deliverables

- Tool execution/approval journal and effect declarations.
- Durable AgentLoop/ToolExecutor integration.
- Complete transition/fault tests and protocol documentation.
