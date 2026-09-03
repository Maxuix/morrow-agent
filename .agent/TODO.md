# TODO

Active subplan: Stage 8 Subplan 4 `4-web-gui-observer` on `feat/stage8-gui-observer`.
Activation decisions (2026-09-03): toolchain approved (Vite + React + TypeScript + Tailwind 4,
pnpm, Node 26); node graph = @xyflow/react (MIT); serif = Newsreader (with Inter + JetBrains Mono,
all bundled via fontsource, no CDN); frontend lives in `gui/`, prebuilt assets ship as package data
under `src/morrow/gui_static/`.

- [ ] Server: static GUI asset serving with relaxed non-`/v1` GET gate, restrictive CSP/security
  headers, and `morrow gui` CLI (loopback, token applied, browser open, graceful Ctrl+C).
- [ ] Packaging: hatch include rules so `gui_static/` ships in wheel/sdist; font/third-party
  notices bundled.
- [ ] Frontend scaffold: `gui/` Vite+React+TS+Tailwind, pnpm lockfile, Warm Paper design tokens
  (light/dark CSS variables), three bundled font families, React Flow theme from tokens.
- [ ] API client: typed protocol mirror, snapshot + ordered event pull + gap detection + resync,
  WS hint channel, honest connection indicator.
- [ ] Views: Session/Task navigation, main workspace (Chat/Task/Artifacts), Workflow panel
  (node states + budget, Direct = linear card), tool/approval/status bar; read-only only;
  keyboard navigation and non-color status signals.
- [ ] Tests: frontend vitest contract tests (snapshot+events, gap resync, reconnect dedup);
  Python tests for static serving security headers/auth boundary and `morrow gui` wiring.
- [ ] GUI–CLI parity evidence + acceptance doc under `docs/acceptance/`.
- [ ] Validation: focused pytest matrix, Ruff format/check, compileall, `git diff --check`,
  frontend typecheck/test/build; update `.agent` execution state and subplan closeout.
