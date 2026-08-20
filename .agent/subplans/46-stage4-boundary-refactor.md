# Subplan 46 — Stage 4 Ownership and Dependency-Boundary Refactor

> Status: active
> Prerequisite: Stage 4 accepted; Recovery wiring fix `8587622`
> Owns: internal responsibility split, port adoption, architecture gates, behavior-preserving tests

## Objective

Reduce the verified God Class and long-state-machine debt without changing Stage 4 behavior,
durable schema, security defaults, public events, CLI contracts, or ConversationLog ownership.

## Locked invariants

- `ConversationLog` remains the only chat-history grammar and append authority.
- `AgentLoop.run_task()` remains the single ordinary-chat lifecycle owner; extracted helpers are
  phases, not competing loops.
- Recovery lifecycle mutation has one production owner through the operational application API.
- All SQLite domain repositories share one `OperationalStoreSession`; cross-domain commands retain
  one `BEGIN IMMEDIATE` transaction.
- Public `OperationalApplicationService`, `SessionApplication`, CLI commands, and durable schema v9
  remain compatible during this refactor.
- No third-party dependency, Stage 5 capability, policy-default change, or live/network test.

## Tasks

- [x] S4.46.1 Freeze dependency/transaction invariants and consolidate Recovery lifecycle ownership.
- [x] S4.46.2 Extract domain application handlers behind the compatible operational API facade.
- [>] S4.46.3 Split the SQLite journal implementation by narrow domain ports while sharing one
  transaction session.
- [ ] S4.46.4 Decompose the AgentLoop run state machine without changing ConversationLog ownership or
  public event lifecycle.
- [ ] S4.46.5 Split CLI command registration by command group and remove direct journal access.
- [ ] S4.46.6 Add architecture boundary tests, reconcile docs, run full gates, and commit verified work.

## Validation

Run focused tests after each task and finish with:

```bash
uv run pytest -m 'not live'
uv run ruff format --check .
uv run ruff check .
uv run python -m compileall -q src tests
uv run morrow --help
git diff --check
```

## Completion gate

No duplicate Recovery lifecycle writer remains; application services depend on narrow transaction
ports rather than a concrete SQLite class; the concrete journal is physically partitioned without
losing atomic transactions; the AgentLoop main method and CLI module are materially smaller; and all
existing Stage 4 acceptance behavior remains green.
