# Stage 8 Subplan 3 — Core API and Local Server

Date: 2026-09-03; review remediation 2026-09-04. Initial branch `feat/stage8-core-api`, remediation
branch `fix/stage8-core-api-review`. Both prerequisite authorizations were granted
explicitly by the user on 2026-09-03: the additive public `ApplicationEvent` lifecycle extension
and promoting `starlette` + `uvicorn` to direct dependencies (both already shipped as transitive
deps of `mcp>=2.0.0`; the lockfile gained no new packages).

## Delivered

- Versioned `/v1` protocol with separated surfaces: Commands (sessions, tasks, workflow
  start/pause/resume/cancel/rerun/abandon, patch validate/save/apply, approval resolution), Queries
  (sessions, tasks, workflow run/node views, agent-run observations, artifacts, approvals), the
  read-only Catalog (AgentDefinitions, WorkflowDefinitions/Revisions, Providers/Models, Skills,
  Tools, Artifact contracts), and the Event stream.
- Local server owned by the Core process (`morrow serve`): loopback-only bind (non-loopback
  refused), per-session random token printed once, Origin/Referer allowlist, JSON-only mutations,
  graceful SIGINT shutdown that drains requests and never records server shutdown as user
  cancellation. A latent `WorkspaceService` lookup bug used by `--workspace-id` CLI paths was
  repaired with an exact `get()`.
- Core Host concurrency per frozen contract C4: one Core runtime thread/event loop owns the
  thread-bound SQLite session; mutations serialize through a bounded command bus whose queue-full
  is an explicit 503, never a silent drop; `RunSupervisor` holds at most one in-process driver per
  WorkflowRun; read projections run on the same Core loop.
- Event delivery reuses the workspace monotonic cursor and append-only event machinery — no second
  event truth. Clients take a snapshot capturing the max cursor in one transaction, WebSocket
  pushes `latest_cursor` hints only, and facts come from durable `GET /v1/events?after=`. Gap
  detection, complete multi-page pulls and snapshot resync are part of the scripted client
  contract. Event rows participate in an outer transition transaction and WebSocket hints publish
  only after its successful commit.
- Additive public `ApplicationEvent` types (`workflow_run.created`, `workflow_run.status_changed`,
  `workflow_node.status_changed`, `approval.requested`) emitted through the optional
  `WorkflowTransitionService.event_sink` seam — CLI composition unchanged (no sink attached).
- Idempotent Command IDs end to end: native receipts for start/approval/rerun/patch apply. Rerun
  receipt + child creation and patch-apply receipt + continuation handoff each share one
  transaction, so a crash-retry cannot double-apply; replay ensures any nonterminal child has a
  driver. Naturally idempotent transition commands retain the serialized receipt wrapper and
  rebuild answers from current durable facts.
- Approvals: `ServerApprovalPort` registers its waiter before emitting a value-free
  `approval.requested` hint. The API atomically commits resolution, consume/deny, event and receipt
  before releasing a claimed live waiter; ToolCycle recognizes and verifies that durable decision
  instead of consuming twice. Dormant approvals use the same durable command path.
- Redaction boundary: every payload is assembled in `server/projections.py` via explicit field
  allowlists, including AgentRun observations; ProviderService owns catalog/config/credential
  queries and the server sees only `credential_configured` booleans; approval query surfaces carry
  bounded previews while lifecycle events carry only a count, never full tool arguments; error
  payloads are bounded and traceback-free.

## Deterministic evidence

`tests/test_stage8_core_api.py` (20 tests) and `tests/test_stage8_core_api_security.py` (11 tests)
drive the real serve composition through `tests/fixtures/core_api_client.py`, a scripted
in-process ASGI client that doubles as the reference event-stream consumer. No sockets, no
network, no wall-clock sleeps (pytest's `network_guard` stays intact).

Contract coverage:

- meta/snapshot/event pull with exact cursor sequences;
- WebSocket hello + cursor hints, forced disconnect/reconnect, gap resync and duplicate re-pull
  dedup, stale-snapshot detection and resync;
- session command idempotency (`accepted` → `replay`) and reused-ID conflict;
- workflow start → drive → complete with run/node events, and CLI–API parity for the same
  WorkflowRun (`morrow workflow status` equals the API view);
- pause during an in-flight approval (draining, never blocked), live approval resolution, drain
  settle to paused, resume to completion;
- approval denial plus double-resolve rejection;
- rerun receipt idempotency: retry returns the same child, exactly two runs exist;
- Core restart recovery observed through the API: queued run survives, pre-restart events persist,
  resume drives to completion, cursors stay gapless;
- patch validate/save/apply through the API: continuation child, superseded parent, inherited
  imports marked in effective outputs, apply replay returns the same child;
- command-bus backpressure: queue-full raises 503 `busy` while blocked commands drain.

Security coverage (§16.3 transport-reachable items):

- missing/wrong token → 401; WebSocket without/with wrong token → close 4401; evil Origin → 403
  (WS 4403); loopback origins accepted; non-JSON mutations → 415; unknown paths and other API
  versions → 404;
- permission-elevation attempts via extra command fields rejected by strict wire models;
- provider catalog carries no credential value or reference; error payloads bounded and
  traceback-free.

## Review remediation

The 2026-09-04 branch review reported 6 bugs, 7 suggestions and 2 nits. All 15 were confirmed as
real correctness, contract-test, projection-ownership or protocol-hardening gaps and repaired:

- Core Host cancellation is explicitly non-user cancellation all the way through AgentLoop, so an
  approval-waiting workflow remains open and recoverable after host shutdown; ordinary scheduler
  task cancellation keeps the existing user-cancel contract.
- Live approval delivery is claimed exactly once, with durable consume/deny and receipt committed
  before the waiter resumes; waiter registration precedes the redaction-safe lifecycle event.
- `morrow serve` retains the workspace writer lock until `host.stop()` closes Core resources, and
  a composition failure is preserved rather than being masked by a second stop error.
- Patch Apply atomically records its command receipt with the continuation handoff; replay returns
  the same child, ensures its driver, and continuation cancellation emits parent NodeRun events.
- Tool catalog derives from the composed Session ToolSet; event hints are commit-safe; the
  reference client drains every event page; pause replay uses one explicit Command ID; conflicts
  map to 409; AgentRun and Provider projections have explicit ownership/allowlists.
- Mutation Content-Type parsing accepts `application/json` parameters but rejects lookalikes such
  as `application/jsonp`; stale architecture/history comments were corrected.

## Gates

- Review-focused suites: Stage 8 Core API/security 31 passed; Stage 7 workflow regression 68
  passed; durable tool/observability/runtime-control regression 65 passed; serial scheduler plus
  Core API/security 58 passed.
- Full offline gate: 1597 passed, 2 deselected (290.25s).
- `uv run ruff format --check .`, `uv run ruff check .`, `uv run python -m compileall -q src tests`,
  `git diff --check` all green; `morrow --help` / `morrow serve --help` smoke green.
