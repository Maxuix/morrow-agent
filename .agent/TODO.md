# TODO

Active subplan: Stage 8 Subplan 3 (`3-core-api-local-server`) on `feat/stage8-core-api`.

- [ ] Wire protocol models and `/v1` surface: separated Command/Query, Approval resolution, Event
  stream (`snapshot + /events?after= + WebSocket latest_cursor + gap resync`).
- [ ] Additive `ApplicationEvent` lifecycle extension for workflow run/node/approval facts
  (authorized 2026-09-03), emitted through the existing workspace monotonic cursor machinery.
- [ ] Local server owned by the Core process: loopback-only bind, per-session random auth token,
  CSRF and WebSocket origin checks, redaction boundary intact in every payload.
- [ ] Core Host concurrency per contracts C4: single Core runtime thread/event loop, bounded
  serialized command bus with explicit backpressure, RunSupervisor one driver per WorkflowRun,
  shutdown never recorded as user cancellation.
- [ ] CLI entry `morrow serve`: foreground headless server, prints loopback address/port and
  one-time session token, graceful SIGINT drain.
- [ ] Read-only Catalog Query APIs: AgentDefinitions, Providers/Models, Skills, Tools, Artifact
  contracts.
- [ ] Scripted in-process verification client fixture covering snapshot, stream, forced
  disconnect/reconnect, gap resync, command idempotency, stale snapshot handling.
- [ ] Deterministic contract tests: event loss/reorder/duplication/reconnect, command idempotency
  under retry, CLI–API parity for the same WorkflowRun state, Core restart recovery via API,
  §16.3 transport-reachable security tests.
- [ ] Full offline gate, Ruff format/check, compileall, `git diff --check`, CLI help smoke.
