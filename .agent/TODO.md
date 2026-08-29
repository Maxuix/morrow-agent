# TODO

## Current task

Subplan 94 is complete, verified and integrated. No implementation subplan is active.

## Tasks

- `[x]` Delete dead re-export modules, unused aliases/methods and unreachable compatibility paths.
- `[x]` Move internal imports/tests from compatibility entrypoints to current owning contracts.
- `[x]` Delete historical evaluation-only resources and their dedicated tests.
- `[x]` Run focused tests and the complete offline/static/CLI/diff gate.
- `[x]` Commit, integrate and retire Subplan 94 before beginning persisted-state cleanup.

## Boundaries

- Preserve current external interoperability behavior.
- Do not modify or execute retained Live evaluation evidence.
- Do not add dependencies or run Live/network tests.
