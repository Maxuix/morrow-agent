# Subplan 37 — Durable Session, Task, Turn, AgentRun, and No-Tool Conversation

> Status: pending
> Prerequisite: Subplan 36 accepted
> Owns: durable foreground identities and legal no-tool conversation history

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
- Clean shutdown and restart of no-tool chat.

## Out of scope

- Tool calls, durable approvals, recovery reconciliation, artifacts, compaction, fork, and grants.
- Copying credentials or Provider private reasoning into snapshots.
- Direct chat writes from TaskService, CLI commands, event projections, or SQL recovery helpers.

## Tasks

- [ ] S4.37.1 Add typed domain models, legal lifecycle/health values, immutable IDs, source
  snapshots, and payload validators.
- [ ] S4.37.2 Add lifecycle and conversation-journal schemas/ports with workspace-scoped queries,
  foreign keys, uniqueness, and sequence constraints.
- [ ] S4.37.3 Refactor ConversationLog behind a durable append boundary that validates first, commits
  atomically, and updates its in-memory projection only from the committed record.
- [ ] S4.37.4 Integrate Session construction and AgentLoop no-tool begin/assistant/finish writes
  without adding a second ordinary-chat path.
- [ ] S4.37.5 Implement `client_message_id` command receipt behavior, duplicate-result replay, and
  conflict rejection for same key/different payload.
- [ ] S4.37.6 Restore legal snapshots after clean exit, reject orphan/invalid sequences, and keep
  lifecycle separate from health quarantine.
- [ ] S4.37.7 Prove workspace isolation, snapshot redaction/budgets, rollback behavior, and Stage 3
  conversation regressions; update architecture data ownership.

## Locked contracts

- First ordinary input creates a current TaskRun if needed. A final assistant answer yields a
  completed-but-not-accepted TaskRun.
- ConversationLog validates every append and is the only component allowed to change chat history.
- `TaskService` may atomically coordinate IDs/status with a ConversationLog append through an
  application transaction, but may not manufacture User/Assistant records itself.
- A duplicate `client_message_id` returns the committed command result and never starts another
  AgentRun; a mismatched duplicate is a conflict.
- AgentRun snapshots are immutable evidence, not a new Preferences/Profile authority.

## Tests and faults

- create/reopen/resume an isolated multi-turn no-tool Session;
- duplicate and conflicting client message IDs across same/different Sessions;
- exception and `os._exit` before/after user append, assistant append, and Turn close commit;
- invalid sequence, orphan assistant, incomplete/crossed Turn, and foreign-workspace lookup;
- later YAML change does not alter an older AgentRun snapshot; credentials/reasoning are absent;
- existing ConversationLog grammar, cancellation, and `run_turn()` delegation tests remain green.

## Completion gate

A scripted Provider conversation survives process restart with identical legal records, exactly one
model execution per accepted client message, correct workspace isolation, and reproducible
non-secret run evidence. No tool-related schema or behavior is required yet.

## Deliverables

- Durable lifecycle and conversation journal ports/adapters.
- Session/Task/Turn/AgentRun Core models and snapshot contract.
- AgentLoop no-tool integration and idempotent turn submission.
- Restart/crash evidence for a multi-turn no-tool Session.

