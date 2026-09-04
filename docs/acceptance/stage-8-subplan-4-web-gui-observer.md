# Stage 8 Subplan 4 — Web GUI Run Observer

Date: 2026-09-04. Branch `feat/stage8-gui-observer`. Activated by explicit user request with the
frontend toolchain authorization granted the same day: pnpm 11.5.1 (pinned via `packageManager`)
on Node 26, Vite + React 19 + strict TypeScript + Tailwind 4, `@xyflow/react` (MIT) for the node
graph, bundled Inter + Newsreader + JetBrains Mono variable fonts (SIL OFL 1.1, self-hosted via
fontsource, no CDN), frontend rooted at `gui/`. One additional dependency decision was granted
during the browser smoke: `websockets` as a direct dependency (see "Browser-smoke findings").

## Delivered

- **Static GUI serving on the Core server** (`server/static.py`, `server/app.py`): read-only
  GET/HEAD catch-all confined to the asset root with an extension allowlist; content-hashed
  `assets/` served `immutable`, everything else `no-cache`; uniform CSP
  (`default-src 'self'`, no inline scripts/styles, `frame-ancestors 'none'`), `nosniff` and
  `no-referrer` headers on every HTTP response including API errors. `/v1/*` keeps the full token
  + Origin/Referer gate; the static surface needs no token because the session token rides the
  URL fragment (`/#token=...`) and never reaches the server.
- **`morrow gui`** (`interfaces/gui_cli.py`): same foreground Core server as `morrow serve`
  (shared `_serve_core` runner, loopback-only, WorkspaceWriterLock held until Core stop, graceful
  Ctrl+C), plus the asset mount and a browser open at the fragment-token URL. Refuses to start
  without a built bundle; `--no-browser` and hidden `--gui-dir` for development.
- **Distribution contract**: `gui/` builds to `src/morrow/gui_static/` (gitignored build output);
  the wheel target uses `ignore-vcs = true` so built wheels ship the bundle, and the sdist
  force-includes it — `uv build` fails loudly if the GUI was never built, so distributions always
  carry the observer. Verified: `uv build` produces wheel and sdist each containing the 18-file
  bundle, no `__pycache__`. Bundle budget enforced in the frontend build: JS ≤ 700 KiB (actual
  400.8), CSS ≤ 120 KiB (actual 36.9), fonts woff2-only (399.4 KiB), no inline script/style in
  the built HTML.
- **Design-token foundation** per the Warm Paper decision: CSS variables for both mandated
  palettes, low-saturation status colors (queued outline-only), Tailwind 4 `@theme` wiring, the
  three font roles (serif reading, sans chrome, mono commands/numbers), 4px grid, 8–10px radii,
  hairline borders, 120–200ms ease-out motion. Theme follows the system with a manual override;
  no inline bootstrap script, so no FOUC and no CSP exception.
- **Typed client + sync engine** (`gui/src/api/`, `gui/src/state/`): wire types mirror
  `server/projections.py` field-for-field; snapshot → anchored cursor → WS hint stream → durable
  `/v1/events?after=` pulls (paged, ordered, `event_id`-deduplicated) → reconnect with capped
  backoff and full snapshot resync; honest `connecting/live/reconnecting/offline` states. The
  engine is framework-agnostic and bound with `useSyncExternalStore`. Protocol reality captured:
  all `*.created` events carry partial payloads (the store fetches detail endpoints), and no
  `approval.resolved` event exists, so run/node lifecycle events refresh the pending-approval
  list — a staleness gap found by the browser smoke and fixed in `sync.ts`.
- **Read-only observer views** per roadmap §8.1–8.5: session/task navigation, task detail +
  artifact workspace (no fake chat view — the API has no chat endpoint yet), workflow panel with
  run lineage labels (initial/continuation/rerun + parent link), budget line (run and lineage
  counters), Direct runs as a linear card vs. a read-only React Flow graph for multi-node runs
  (status dot + text label always, never color alone), node detail with output bindings,
  artifacts and lazy agent-run metrics, pending-approval strip with bounded mono previews and a
  "resolve in CLI" pointer (resolution UI is Subplan 6), connection banner with manual retry, and
  a token-missing screen. Keyboard reachable throughout with visible accent focus rings; UI
  strings follow the CLI's Chinese localization.

## Deterministic evidence

- Python: `tests/test_stage8_gui_serving.py` (10 tests) — index/assets served without token with
  the full security-header set, immutable vs. no-cache caching, path traversal / unknown
  extension / missing file → 404, POST and evil-Origin on the static surface → 404, `/v1` token
  gate unchanged alongside the mount, serve mode without the GUI still 404s unknown paths, token
  in the URL fragment, missing-bundle refusal, browser-open wiring, `morrow gui --help` smoke.
- Frontend: 29 vitest tests (node env, no DOM, no wall-clock) — snapshot anchoring, ordered pulls,
  multi-page drain, duplicate delivery dedupe, forced-close resync with no lost/duplicated state,
  backoff → offline → manual retry, approval refresh on lifecycle events, graph mapping
  (3-node diamond, Direct detection), status labels for all 9 states, budget display.
- GUI–CLI parity: the same WorkflowRun rendered by `morrow workflow status` and the GUI agrees
  (`completed` / `succeeded` / row_version 3), extending the Subplan 3 CLI–API parity test.

## Browser smoke (looped-back, scripted)

`scripts/gui_smoke_server.py` boots the real serve composition with scripted providers, seeds a
two-node workflow parked at a deterministic approval, and serves the prebuilt bundle on
127.0.0.1:8799 with a fixed smoke token; `--serve-only` restarts the same state without reseeding.
`scripts/gui_smoke_cdp.mjs` drives a dedicated Chrome profile over CDP (never the user's browser)
and asserts, 16/16: shell render, workspace id in the top bar, session→task navigation, both
nodes in the graph with status dot+label, pending-approval display, budget line, keyboard
traversal with a visible focus indicator, live status update to completed without reload after an
out-of-band approval resolution, approval strip unmounting, both node cards settling to
已完成, the offline banner as text after the server is killed, reconnect to live after a Core
restart, and no lost/duplicated state after resync. Screenshots land in the given output
directory for eyeballing.

### Browser-smoke findings (both fixed)

1. **uvicorn had no WebSocket backend** — `/v1/events/stream` failed over real sockets
   ("No supported WebSocket library detected") while every in-process ASGI test passed. The GUI
   degraded exactly as designed (durable pulls + resync kept state correct; the banner was
   honestly stuck at reconnecting). Fixed by adding `websockets>=13,<16` as a direct dependency
   (user-approved 2026-09-04); uv.lock gained only that package.
2. **Resolved approvals never left the GUI's pending strip** — the protocol has no
   `approval.resolved` event, so the strip went stale until a full resync. Fixed store-side:
   run/node lifecycle events refresh the pending-approval list (regression test added).

## Gates

- Full offline gate: 1607 passed, 2 deselected (154s).
- GUI serving + Core API + security suites: 41 passed.
- Frontend: `pnpm run typecheck`, `pnpm run test` (29 passed), `pnpm run build` (budget green).
- `uv run ruff format --check .`, `uv run ruff check .`, `uv run python -m compileall -q src tests`,
  `git diff --check` all green; `uv build` ships the bundle in wheel + sdist (see above).

## Out of scope (unchanged)

Editing (Subplan 5), run-control actions and approval resolution UI (Subplan 6),
Context/Learning/Skill management (Subplan 9), desktop packaging (Stage 10).
