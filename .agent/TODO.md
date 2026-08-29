# TODO

## Current task

Subplan 94: remove repository-level compatibility surfaces before changing persisted-state schemas.

## Tasks

- `[x]` Delete dead re-export modules, unused aliases/methods and unreachable compatibility paths.
- `[x]` Move internal imports/tests from compatibility entrypoints to current owning contracts.
- `[x]` Delete historical evaluation-only resources and their dedicated tests.
- `[x]` Run focused tests and the complete offline/static/CLI/diff gate.
- `[>]` Commit, integrate and retire Subplan 94 before beginning persisted-state cleanup.

## Boundaries

- Preserve current external interoperability behavior.
- Do not modify or execute retained Live evaluation evidence.
- Do not add dependencies or run Live/network tests.
