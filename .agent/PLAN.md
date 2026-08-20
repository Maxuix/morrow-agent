# Pre-Stage 5 Boundary Refactor Plan

> Status: active — behavior-preserving architecture refactor
> Active subplan: 48 — runtime, persistence, storage, and application boundaries
> Stage 5 status: inactive
> Baseline: `408da68`

## Objective

Reduce the verified God Method, God Class, hidden-protocol, and composition-duplication debt before
Stage 5 adds learning records, commands, events, and CLI surfaces. Preserve current Stage 4 product
behavior and all existing authorities.

## Authority

1. Current user request and later explicit decisions.
2. Current code and validation just run.
3. This plan and the active Subplan 48.
4. `docs/ARCHITECTURE.md` and accepted Stage 4 ADRs.
5. Roadmap documents; Stage 5 remains design input, not active implementation scope.

## Locked boundaries

- `ConversationLog` remains the only chat-history authority.
- Ordinary chat remains on `AgentLoop.run_task()`; `AgentRuntime.run_turn()` stays a thin delegate.
- The public runtime event schema, order, and cardinality do not change.
- One outer SQLite transaction continues to own cross-domain atomic writes.
- No schema, migration, capability, network, Skill, MCP, policy-default, or credential change.
- No generic ORM/repository framework, mixin hierarchy, or one-class-per-table rewrite.
- Keep compatibility facades while replacing hidden and private coupling behind them.

## Refactor route

1. Replace optional `getattr`-based durable runtime capabilities with explicit typed persistence
   contracts and an explicit process-local implementation path.
2. Introduce typed run state and extract tool-cycle execution without moving ConversationLog write
   ownership or public-event timing out of `AgentLoop`.
3. Split `SessionPersistence` into focused internal coordinators for Turn submission/restoration,
   permission evidence, and durable tool execution while retaining one compatible committer facade.
4. Partition SQLite journal implementation by bounded domain behind one shared transaction context
   and the existing narrow Core ports.
5. Replace application collaborators' parent-facade/private-member dependency with an explicit
   command context; centralize headless operational composition shared by CLI and bootstrap.
6. Remove unused duplicate ports, reconcile architecture documentation, and run the full offline
   and quality gates.

## Completion gates

- No durable `AgentLoop` capability is discovered with `getattr`.
- `SessionCommitter` or its replacement describes the actual runtime contract.
- Application domain collaborators do not call private methods on their parent facade.
- REPL and operational CLI composition share one factory for common state services.
- SQLite domain partitioning preserves shared transaction timestamp/replay/rollback behavior.
- Focused tests pass after every logical slice.
- Final gate: `uv run pytest -m 'not live'`, Ruff format/check, compileall, CLI help, and
  `git diff --check`.
- Verified work is committed, fast-forwarded to `main`, and the topic branch is retired according
  to `AGENTS.md`.
