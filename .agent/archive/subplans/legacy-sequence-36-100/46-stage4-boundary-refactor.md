# Subplan 46 — Stage 4 Ownership and Dependency-Boundary Refactor

> Status: completed
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
- [x] S4.46.3 Activate narrow SQLite journal ports while sharing one transaction session and
  confining the concrete adapter to cross-domain composition.
- [x] S4.46.4 Decompose AgentLoop event emission without changing ConversationLog ownership or
  public event lifecycle.
- [x] S4.46.5 Remove CLI reach-through to API journal internals and establish domain command seams.
- [x] S4.46.6 Reconcile docs, run full gates, and commit verified work.

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

No duplicate Recovery lifecycle writer remains; domain application services depend on narrow
transaction ports rather than a concrete SQLite class; one concrete journal continues to preserve
atomic cross-domain transactions; AgentLoop event rendering and CLI Recovery composition have
explicit seams; and all existing Stage 4 acceptance behavior remains green.

## Completion evidence

- Full offline suite: `613 passed, 1 deselected`.
- `uv run ruff format --check .`: 162 files already formatted.
- `uv run ruff check .`, compileall, main CLI help, Recovery CLI help, and `git diff --check` passed.
- No live or real-network tests were run.
