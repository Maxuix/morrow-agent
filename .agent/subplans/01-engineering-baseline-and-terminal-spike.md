# Subplan 01 — Engineering Baseline and Terminal Spike

> Stage: 1A  
> Status: selected, not started  
> Parent: [Stage 1 implementation plan](../PLAN.md)

## Objective

Create the smallest maintainable Python application and prove the highest-risk terminal integration before domain behavior is built on top of it.

## Scope

- Python 3.12 package managed by `uv` with a `src` layout and `morrow` console entry point.
- Minimal bootstrap seam for dependency injection; no domain implementation beyond what the terminal spike needs.
- prompt-toolkit input, Rich rendering, asyncio streaming, first-press `Ctrl+C` cancellation, and `Ctrl+D` exit behavior.
- Offline test and quality configuration.

## Prerequisites

- Git repository initialized.
- Stage 1 roadmap, architecture baseline, and active plan accepted.

## Tasks

1. Create `pyproject.toml`, `uv.lock`, `src/morrow`, and `tests` without prebuilding future modules; set the distribution name to `morrow-agent`, import package to `morrow`, and console entry point to `morrow`.
2. Declare runtime dependencies from the Stage 1 roadmap and separate test/quality dependencies.
3. Configure Ruff, pytest, pytest-asyncio, and an explicit `live` marker that is excluded from default runs.
4. Create the `morrow` entry point with a minimal bootstrap/composition seam.
5. Make state root, provider, credential store, clock, and ID generation injectable from the start.
6. Implement a disposable or minimal-integrated terminal spike that streams scripted chunks asynchronously.
7. Verify that the first `Ctrl+C` cancels only the active generation and returns to a usable prompt.
8. Verify that `Ctrl+D` requests normal application exit rather than terminating inside a rendering callback.
9. Record the chosen terminal integration pattern in `.agent/LOG.md` and remove spike-only dead code.

## Verification

- The package installs and `morrow --help` starts through the declared entry point.
- A scripted stream renders chunks in order without real network access.
- Cancellation closes the producer task, leaves no late chunks, and permits another prompt.
- EOF reaches one explicit exit path.
- `uv run ruff format --check .`, `uv run ruff check .`, and the Subplan 01 tests pass.

## Completion criteria

- The terminal architecture risk has a tested implementation pattern.
- No Stage 1 domain behavior or future empty package tree was introduced.
- All verification passes and the accepted pattern is documented.

## Deliverables

- Reproducible Python environment and CLI entry point.
- Minimal terminal integration and automated smoke coverage.
- Stable injection seam used by subsequent subplans.

## Out of scope

- Real Provider calls, workspace discovery, context construction, persisted state, and production REPL commands.
