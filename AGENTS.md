# Morrow agent rules

Morrow is a workspace-scoped terminal agent (Python 3.12+, `uv`, Pydantic v2).
Application code is `src/morrow/`; tests are `tests/`. Ruff (`line-length = 100`) is the style source.
This file is the always-on rule set — not a copy of the architecture, roadmap, or plan.

## Authority

1. The current user request
2. Code in the tree and commands just run
3. The active `.agent/PLAN.md` and its active subplan, when an implementation plan is in progress
4. The current-stage document under `docs/roadmap/`
5. Proposals and reviews — decision history, not a second implementation spec

When code or validation conflicts with a plan or doc, update the stale document.
The current user request overrides `.agent/TRACKER.md`. Do not refuse requested work because no stage plan is active.

## Commands

```bash
uv sync
uv run pytest -m 'not live'
uv run pytest -q path/to/test_file.py
uv run ruff format --check .
uv run ruff check .
uv run python -m compileall -q src tests
uv run morrow --help
git diff --check
```

Offline (`-m 'not live'`) is the default gate. Do not claim a check passed unless it was run.
Iterate on the tests you touched; finish implementation work only after those tests and `ruff` succeed.
Do not run Live / real-network tests unless the user asks and an explicit compatible credential is present.
Use fake SDK chunks or scripted Providers. Do not assert timing with wall-clock sleeps.

## Git

Use Git as the source of truth for code history and recovery.

For larger work:

- Use a dedicated branch for each active subplan.
- Keep commits small, coherent, and recoverable.
- Commit meaningful verified progress.
- Create a checkpoint before risky changes or switching work contexts.

Use simple branch names: `feat/<name>`, `fix/<name>`, `refactor/<name>`, `docs/<name>`,
or `chore/<name>`.

Use conventional commit messages such as:

- `feat(scope): description`
- `fix(scope): description`
- `refactor(scope): description`
- `test(scope): description`
- `docs(scope): description`
- `chore(scope): description`

`wip:` commits may be used as temporary checkpoints on working branches, but must not remain
in the final merged history. Do not commit a known broken state as completed work.

Before completing a subplan:

1. Ensure the relevant changes are committed.
2. Run the required validation.
3. Update the execution state.
4. Merge only verified work.

## Boundaries

**Always**

- Inspect the relevant code before changing it.
- Keep Session-owned `ConversationLog` as the only chat-history writer.
- Keep ordinary chat on `AgentLoop.run_task()`; retained `run_turn()` is a thin delegate to that
  same loop and production composition may supply its frozen `ToolExecutor`.
- Keep credentials, reasoning, full tool arguments/results, SDK objects, and tracebacks out of events, logs, terminal output, and YAML.

**Ask first**

- Adding a third-party dependency.
- Starting Stage 3+ work: local file, Shell, Git, network, or browser tools; MCP; Skills; persistent chat history; LLM summaries; background tasks.
- Changing bundled `agent-policy.toml` defaults or the public event lifecycle.

**Never**

- Enable those Stage 3+ capabilities unless the user explicitly opens that stage.
- Write secrets into YAML, logs, events, model context, or the terminal.
- Mark implementation work complete without running the relevant commands above.

Layering and ownership: `docs/ARCHITECTURE.md`.

## Execution state

`.agent/` is for planned implementation work, not every session.

| File | Role | Update when |
|---|---|---|
| `.agent/PLAN.md` | Living plan index | Approach or scope changes |
| `.agent/TODO.md` | Tasks for the active subplan only | Task status changes |
| `.agent/TRACKER.md` | Progress, active task, next action | Progress, blockers, or next action change |
| `.agent/LOG.md` | Material history | Decisions, failures, validation results, blockers |
| `.agent/subplans/` | Ordered child plans | Splitting or completing a large plan |

Also update `docs/ROADMAP.md` / `docs/ARCHITECTURE.md` only when direction or actual structure changes.

Status: `[ ]` pending · `[>]` in progress · `[x]` completed · `[!]` blocked.

- Continuing implementation, or the user asked to change the plan → read `PLAN.md`, `TODO.md`, and `TRACKER.md`. Open recent `LOG.md` only to recover a decision or failure. Continue the active task unless the user asked for something else.
- Question, review, or exploration, and the user did not ask to resume a plan → do not update `.agent/` files.
- No active plan → do not create the next-stage plan unless the user explicitly asks.
- Do not redo completed work unless verification shows it is necessary or the user asks to revisit it.

When an implementation plan is active: one logical task at a time; mark `[>]` while in progress; mark `[x]` only after validation succeeds. Do not log routine reads or searches.

Split, activate, and retire subplans in `.agent/subplans/README.md`. Do not keep obsolete plan versions in the active `PLAN.md`.

## Read when needed

- Direction: `docs/ROADMAP.md`, current `docs/roadmap/stage-*.md`
- Ownership: `docs/ARCHITECTURE.md`
- Acceptance evidence: `docs/acceptance/`
- Human usage: `README.md`
