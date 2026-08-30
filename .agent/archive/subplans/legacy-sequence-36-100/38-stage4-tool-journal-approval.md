# Subplan 38 — Tool Execution Journal and Durable Approval

> Status: completed
> Prerequisite: Subplan 37 accepted
> Owns: persist-before-effect protocol, durable tool states, and one-shot approval consumption
> Schema: v3 tool execution, approval, and structured fact journal

## Objective

Make every ToolCycle crash-explainable by committing the complete tool intent before any side
effect, recording approval and handler completion separately, and closing conversation history only
through ConversationLog.

## In scope

- Durable ToolCall/ToolExecution/Approval models, schemas, and narrow journal ports.
- Independent `EffectClass` and recovery-policy declaration for every production tool.
- Sanitized/bounded durable argument intent, schema digest, permission digest, side-effect evidence,
  result envelope, and state transitions.
- One transaction for Assistant tool-call message, ordered ToolExecutions, and run status;
  `application_events` remain owned by Subplan 43.
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

- [x] S4.38.1 Define ToolExecution, Approval, EffectClass, recovery declaration, structured tool
  facts, transition models, and the named test-only fault-injector port with the ADR's exact
  payload/redaction budgets.
- [x] S4.38.2 Add journal/approval schemas and constraints for ordered calls, intent hashes, schema/
  permission digests, result evidence, row versions, expiry, resolution, and consumption.
- [x] S4.38.3 Persist Assistant tool call plus all ordered execution intents atomically through the
  durable ConversationLog boundary before dispatch, including pre-effect file/config evidence such
  as before hash, expected-after hash, expected size, target/parent conditions, and truncation facts.
- [x] S4.38.4 Integrate approval creation/resolution so consume and `executing` transition are one
  transaction, expiry uses the injected production Clock, Terminal resolution delegates through the
  same application path, and duplicate/stale decisions are deterministic.
- [x] S4.38.5 Persist handler success/failure/cancellation as `handler_completed`, then append
  ToolMessage and close each ordered execution through ConversationLog.
- [x] S4.38.6 Hand-classify every current production tool independently of `ToolEffect`; Host and
  native sandbox default to externally effectful/unknown, and composition fails if any registered
  tool lacks a durable declaration.
- [x] S4.38.7 Add fault-point, redaction, payload-boundary, transition, and Stage 3 regression tests;
  document the protocol.

## Locked contracts

- The handler cannot begin unless the committed execution row is observable from a fresh
  connection.
- Tool calls in one Assistant message retain provider order; results close in that order under the
  existing ConversationLog grammar.
- Approval identity is opaque and bound to intent, Tool Schema, the effective Stage 3 permission-
  context digest, granted subset, expiry, and row version. Subplan 44 later adds a
  PermissionSnapshot FK. One approval can be consumed once.
- Durable argument/result material is the minimum bounded redacted evidence needed for recovery and
  user explanation, never an unbounded copy of provider or command data.
- `ToolEffect` may remain for current policy decisions; crash semantics use the independent durable
  classification.
- File writes larger than the supported revision limit are rejected before effect; they are not
  advertised as hash-reconcilable. `handler_completed` is the only durable handler-result boundary.
- The v3 journal persists bounded structured changed-path/config/validation facts needed by
  TaskOutcome; full diffs and large reports wait for Subplan 41 Artifacts.

## Tests and faults

- crash/exception before and after intent commit, approval creation, approval resolution/consume,
  handler entry, handler result commit, ToolMessage append, and ToolCycle close;
- duplicate, stale, expired, mismatched, and already-consumed approval decisions;
- multiple ordered tool calls with mixed approve/deny/fail/cancel outcomes;
- side-effect spy proves zero calls when intent commit or approval consume fails;
- exact payload/redaction boundaries and missing classification at composition;
- exact inventory covers `update_configuration`, `list_directory`, `read_file`, `find_files`,
  `search_text`, `apply_patch`, `write_file`, `show_changes`, `run_command`, `git_status`,
  `git_diff`, and capability-gated `promote_sandbox_changes`;
- named one-shot logical faults at prepare, intent commit, approval, handler entry/result, message
  append, and cycle close are available to Subplan 39 without production behavior;
- no changes to existing public event cardinality before Subplan 43.

## Completion gate

Every production tool invocation has a committed, bounded, auditable intent before dispatch; every
approval is one-shot and evidence-bound; handler completion and ToolCycle closure are separately
visible; a crash can leave only documented states that Subplan 39 knows how to classify.

## Deliverables

- Tool execution/approval journal and effect declarations.
- Durable AgentLoop/ToolExecutor integration.
- Complete transition/fault tests and protocol documentation.
