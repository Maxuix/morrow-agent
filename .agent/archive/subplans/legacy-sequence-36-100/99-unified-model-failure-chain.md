# Subplan 99 — Unified Model Failure Chain

> Status: completed and verified

## Goal

Replace the streaming model boundary's dual event/exception error representation with one typed,
safe and attributed failure value, while leaving retry policy solely in AgentLoop.

## Scope

- Core model failure contract and Provider exception wrapper.
- OpenAI-compatible Adapter stream, complete and discovery failure classification.
- ModelCallRunner, AgentLoop retries and safe internal-origin terminal detail.
- Scripted providers, reviewers and tests affected by the contract.

## Non-goals

- New validation gates or Provider health checks.
- Database schema changes or public event lifecycle changes.
- A live Provider proof run or Workflow implementation.

## Validation

- Focused model Adapter and AgentLoop tests.
- `uv run pytest -m 'not live'`
- `uv run ruff format --check .`
- `uv run ruff check .`
- `uv run python -m compileall -q src tests`
- `uv run morrow --help`
- `git diff --check`

## Result

- `ModelFailure` is the sole typed failure value across stream events and non-stream exception
  transport; the old parallel event fields and transient-internal flag are removed.
- OpenAI-compatible streaming always returns expected failures as events. AgentLoop performs the
  sole retry-policy decision from the failure's classified `retryable` fact.
- Ambiguous internal failures retain safe Provider/Adapter/Runtime origin in terminal metrics while
  public lifecycle events remain unchanged.
- Focused suites passed 155 tests with one explicit Live test skipped. The complete offline gate
  passed 1,285 tests with two Live tests deselected; all static gates passed.
