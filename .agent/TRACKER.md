# Progress Tracker

## Current status

Stage 7 is complete and remediated. Stage 8 Subplans 1–2 are complete: the Subplan 2 review
remediation (four confirmed contract bugs plus two accepted suggestions) is integrated and, with
explicit user authorization on 2026-09-03, local `main` through `bd3a8c5` is published to
`origin/main` — neither ahead nor behind. Subplan 3 (Core API and Local Server) is now active on
`feat/stage8-core-api` with both prerequisite authorizations granted: the additive public
`ApplicationEvent` lifecycle extension and promoting `starlette` + `uvicorn` to direct
dependencies (already present as transitive deps of `mcp>=2.0.0`).

## Active task

Stage 8 Subplan 3 implementation: versioned `/v1` Command/Query/Approval/Event protocol, local
loopback server with per-session token and CSRF/origin checks, Core Host single-writer command
bus per contracts C4, `morrow serve` CLI entry, read-only Catalog Query APIs, and a scripted
in-process verification client with deterministic contract and §16.3 security tests.

## Next action

Implement the wire protocol models and the additive workflow ApplicationEvent emission, then the
Core Host and ASGI transport.

## Blockers

None. Remote publication is authorized and current. No Live Provider/MCP/network/credential test
is authorized; the loopback server is exercised by scripted in-process clients only.
