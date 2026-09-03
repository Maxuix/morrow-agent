# Progress Tracker

## Current status

Stage 7 is complete and remediated. Stage 8 Subplans 1–2 are complete and published. Subplan 3
(Core API and Local Server) is complete on `feat/stage8-core-api`: the versioned `/v1`
Command/Query/Approval/Event protocol, Core Host single-writer model per contract C4, loopback
`morrow serve` with session token and Origin/CSRF checks, additive workflow `ApplicationEvent`
emission, end-to-end command idempotency, read-only Catalog APIs, and the scripted in-process
verification client. Focused suites 22 passed; full offline gate 1588 passed, 2 deselected;
static gates and CLI help smoke green. Acceptance evidence:
`docs/acceptance/stage-8-subplan-3-core-api-local-server.md`.

## Active task

Subplan 3 closeout: docs/execution-state commit, fast-forward merge into `main`, remote
publication (authorized 2026-09-03), topic branch retirement.

## Next action

Subplan 4 (`4-web-gui-observer`) starts only on explicit activation and additionally needs
frontend toolchain authorization.

## Blockers

None. Remote publication was authorized on 2026-09-03 and `main` was pushed through `bd3a8c5`.
No Live Provider/MCP/network/credential test is authorized.
