# Subplan 3 — Core API and Local Server

> Status: active 2026-09-03 (both prerequisite authorizations granted explicitly by the user:
> additive `ApplicationEvent` lifecycle extension; `starlette` + `uvicorn` as direct dependencies)
> Branch: `feat/stage8-core-api`
> Activation base: latest verified `main` with Subplan 2 integrated
> Prerequisite: Subplan 2 verified; explicit user authorization for (a) the additive public
> `ApplicationEvent` lifecycle extension and (b) promoting `starlette` + `uvicorn` to direct
> dependencies (both already ship in the locked environment as transitive deps of the direct
> `mcp>=2.0.0` dependency, so no new framework weight is introduced; a different framework choice
> requires re-approval)
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
- CLI entry `morrow serve`: starts the foreground headless API server, prints the loopback
  address/port and the one-time session token, and shuts down gracefully on SIGINT (drain
  in-flight requests; running foreground Workflows follow the existing owning-process rules —
  Stage 8 has no background daemon).
- Core Host concurrency model per contracts doc C4: the thread-bound SQLite store is never handed
  to ASGI workers. One Core runtime thread/event loop owns all durable writes through a bounded
  serialized command bus (queue-full → explicit backpressure, never silent drop); read projections
  run on the Core thread or dedicated read-only connections; an explicit `RunSupervisor` owns at
  most one in-process driver per WorkflowRun; server shutdown is never recorded as user
  cancellation.
- Event delivery reuses the existing workspace monotonic cursor + append-only event + command
  receipt machinery (no second event truth): client takes an initial snapshot under a read
  transaction capturing the max cursor, then consumes events after that cursor; WebSocket pushes
  only `latest_cursor` notifications and clients pull from durable `/events?after=`; gap detection
  and resync query are part of the contract, and the stream is never the permanent authority.
- Idempotent Command IDs end to end; command retry cannot double-apply.
- Read-only Catalog Query APIs (AgentDefinitions, Providers/Models, Skills, Tools, Artifact
  contracts) shipped here, not in the editor subplan: the editor (Subplan 5) and planner
  (Subplan 7) both consume them, so the backend surface lands once with the server.
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
