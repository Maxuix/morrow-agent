# TODO

## Current stage

Stage 4 remains accepted. The user explicitly authorized a post-acceptance architecture refactor;
Stage 5 remains inactive.

## Active subplan

Subplan 46 — Stage 4 ownership and dependency-boundary refactor.

## Tasks

- [x] S4.46.1 Freeze dependency/transaction invariants and consolidate Recovery lifecycle ownership.
- [x] S4.46.2 Extract domain application handlers behind the compatible operational API facade.
- [>] S4.46.3 Split the SQLite journal implementation by narrow domain ports while sharing one
  transaction session.
- [ ] S4.46.4 Decompose the AgentLoop run state machine without changing ConversationLog ownership or
  public event lifecycle.
- [ ] S4.46.5 Split CLI command registration by command group and remove direct journal access.
- [ ] S4.46.6 Add architecture boundary tests, reconcile docs, run full gates, and commit verified work.
