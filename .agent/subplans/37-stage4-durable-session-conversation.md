# Subplan 37 — Durable Session, Task, Turn, AgentRun, and No-Tool Conversation

> Status: active
> Prerequisite: Subplan 36 accepted
> Owns: durable foreground identities and legal no-tool conversation history
> Schema: v2 Session, minimal open TaskRun pointer, Turn, base AgentRun, conversation, and receipt

## Objective

Persist and restore one workspace-isolated multi-turn Session without tools while preserving
ConversationLog as the only message grammar/writer and freezing reproducible, non-secret AgentRun
evidence.

## In scope

- Core identifiers/models for Workspace reference, Session, TaskRun, Turn, AgentRun, and immutable
  conversation records.
- Narrow lifecycle and conversation journal ports implemented by SQLite.
- Monotonic per-Session record sequence and exact legal snapshot restoration.
- Durable ConversationLog adapter/API used by AgentLoop rather than a second message state machine.
- Idempotent turn submission using mandatory `client_message_id`.
- Minimal foreground TaskRun creation/current association; full lifecycle and outcome wait for
  Subplan 40.
- AgentRun snapshots containing resolved bounded non-secret Profile/Preferences/config values,
  source revisions/hashes, model/provider references, RunPolicy/tool schema digests, and runtime
  instance identity.
- Clean shutdown and restart of bounded short no-tool chat, plus a narrow internal resume test
  entry point; arbitrary long context waits for Subplan 42.
- Durable `/new`, `/exit`, system-boundary, and process-local `dirty` semantic migration.

## Out of scope

- Tool calls, durable approvals, recovery reconciliation, artifacts, compaction, fork, and grants.
- Copying credentials or Provider private reasoning into snapshots.
- Direct chat writes from TaskService, CLI commands, event projections, or SQL recovery helpers.

## Tasks

- [>] S4.37.1 Add typed domain models, separate lifecycle/health axes, immutable IDs, the three
  order namespaces, base AgentRun source snapshots, and payload validators.
- [ ] S4.37.2 Add lifecycle and conversation-journal schemas/ports with workspace-scoped queries,
  foreign keys, uniqueness, and sequence constraints.
- [ ] S4.37.3 Refactor ConversationLog behind a durable append boundary that validates first, commits
  atomically, and updates its in-memory projection only from the committed record.
- [ ] S4.37.4 Integrate Session construction and AgentLoop no-tool begin/assistant/finish writes so
  Turn/User commit precedes `turn.started` and Provider invocation, without adding a second
  ordinary-chat path.
- [ ] S4.37.5 Implement command-level `client_message_id` receipts: replay closed results, return an
  open/interrupted recovery disposition without duplicating Turn/User, and reject the same key with
  a different payload.
- [ ] S4.37.6 Restore legal snapshots after clean exit, reject orphan/invalid sequences, and keep
  lifecycle separate from health quarantine; create the v2 fixture for later migration acceptance.
- [ ] S4.37.7 Replace process-local-only system prompt, `/new`, `/exit`, dirty, and persistence tests;
  prove workspace isolation, snapshot redaction/budgets, rollback behavior, and Stage 3
  conversation regressions; update architecture data ownership.

## Locked contracts

- First ordinary input creates an `open` current TaskRun pointer if needed. Subplan 37 does not
  implement completion, acceptance, correction, terminal Task states, or TaskOutcome.
- ConversationLog validates every append and is the only component allowed to change chat history.
- The only append protocol is candidate construction and validation, one `BEGIN IMMEDIATE`
  transaction for conversation plus companion rows, COMMIT, then projection replacement from
  committed rows. Failed commit leaves no memory-only record.
- An application service may coordinate IDs and the minimal Task pointer with that append, but may
  not manufacture User/Assistant records itself.
- One accepted `client_message_id` creates exactly one Turn and one UserMessage. A duplicate closed
  command replays its committed result; a duplicate open/interrupted command returns the receipt and
  recovery disposition. A later recovery may create a linked AgentRun in that Turn. A mismatched
  duplicate is a conflict.
- AgentRun base snapshots contain Stage 3 permission-context digests, not the Subplan 44
  PermissionSnapshot/grant schema, and remain immutable evidence rather than configuration authority.
- `/new` creates/selects a new Session without resetting, deleting, or auto-archiving the old one;
  unresolved recovery blocks it and Session-scoped Preferences do not carry. `/exit` never offers to
  discard already-persisted conversation.

## Tests and faults

- create/reopen/resume an isolated multi-turn no-tool Session;
- duplicate and conflicting client message IDs in absent/open/interrupted/closed states across
  same/different Sessions;
- exception and `os._exit` before/after user append, assistant append, and Turn close commit;
- failed commit never advances `conversation_position`; committed Turn/User exists before
  `turn.started`, and runtime/application/conversation order counters do not alias;
- invalid sequence, orphan assistant, incomplete/crossed Turn, and foreign-workspace lookup;
- later YAML change does not alter an older AgentRun snapshot; credentials/reasoning are absent;
- short history fits the current context budget; explicit over-budget behavior remains until 42;
- durable system boundary plus `/new`/`/exit` UX, ConversationLog grammar, cancellation, and
  `run_turn()` same-loop delegation tests remain green.

## Completion gate

A bounded scripted Provider conversation survives restart with identical legal records, exactly one
Turn/UserMessage per accepted client command, correct open/interrupted duplicate disposition,
workspace isolation, and reproducible non-secret base run evidence. No tool recovery, complete Task
state machine, PermissionSnapshot, or arbitrary-long-context claim is required yet.

## Deliverables

- Durable lifecycle and conversation journal ports/adapters.
- Session/minimal-open-Task/Turn/AgentRun Core models, v2 migration, and base snapshot contract.
- AgentLoop no-tool integration and idempotent turn submission.
- Restart/crash evidence for a multi-turn no-tool Session.
