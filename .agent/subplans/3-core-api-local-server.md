# Subplan 3 — Core API and Local Server

> Status: pending activation
> Branch: `feat/stage8-core-api`
> Activation base: latest verified `main` with Subplan 2 integrated
> Prerequisite: Subplan 2 verified; explicit user authorization for (a) the additive public
> `ApplicationEvent` lifecycle extension and (b) the chosen Python web-framework dependency;
> transport selection confirmed at activation (default proposal: HTTP + WebSocket, loopback only)
> Roadmap authority: stage-8 §二 (GUI positioning), §11.1–11.3, §8A, §16.1, §16.3

## Objective

Expose Morrow Core through a versioned, authenticated local API so that any client (first the Web
GUI, later other shells) consumes the same application services as the CLI — with an ordered event
stream that survives disconnects.

## Deliverables

- Versioned protocol (`/v1/...`) with separated Command and Query surfaces, an Approval resolution
  surface, and an Event stream carrying the authorized additive `ApplicationEvent` types.
- Local server owned by the Core process: loopback-only bind by default, per-session random auth
  token, CSRF and WebSocket origin checks, no exposure of credentials, full sensitive tool
  arguments, reasoning or tracebacks in any payload.
- Idempotent Command IDs end to end; command retry cannot double-apply.
- Event delivery contract: initial query snapshot + ordered events with sequence numbers + client
  gap detection + resync query; the stream is never the permanent authority.
- A scripted in-process verification client (test fixture, not a product) that exercises the full
  contract: snapshot, stream, forced disconnect/reconnect, gap resync, command idempotency, stale
  snapshot handling.
- API projections are assembled by application services; the transport layer contains no business
  state machine and never touches repositories or YAML directly.
- Redaction boundary tests proving the §16.3 items reachable at this layer: credential material in
  payloads, origin/CSRF rejection, permission-elevation attempts via API commands.

## Key semantics

- The server is a projection of Core state; closing a client changes nothing, and whether the Core
  keeps running foreground work is decided by the run mode, not by client connections.
- Event types added under this subplan are the minimum the observer GUI needs; Query/polling
  remains a complete fallback path for every projection.
- GUI shutdown never terminates Core.

## Validation

- Deterministic contract tests via the scripted client: event loss/reorder/duplication/reconnect,
  command idempotency under retry, CLI–API parity for the same WorkflowRun state, Core restart
  recovery observed through the API.
- Security tests per §16.3 reachable at the transport layer.
- Stage 7/8 matrices, full offline gate, Ruff format/check, compileall, `git diff --check`.

## Out of scope

Any browser frontend (Subplan 4); editing commands beyond what Subplans 1–2 already expose
(editor-facing commands arrive with Subplan 5); background daemon process model (Stage 9).
