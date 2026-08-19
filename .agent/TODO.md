# TODO

## Current stage

Stage 4 production implementation is active at durable no-tool Session history. The v1 Operational
Store foundation has landed; Full Access remains inactive.

## Active subplan

Subplan 37 — Durable Session, Task, Turn, AgentRun, and No-Tool Conversation.

## Tasks

- [x] S4.37.1 Add typed domain models, separate lifecycle/health axes, immutable IDs, the three
  order namespaces, base AgentRun source snapshots, and payload validators.
- [>] S4.37.2 Add lifecycle and conversation-journal schemas/ports with workspace-scoped queries,
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

Only Subplan 37 may be executed. Tool journals, recovery, artifacts, grants, and Full Access remain
inactive.
