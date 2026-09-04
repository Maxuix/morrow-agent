# TODO

Active subplan: none. Stage 8 Subplan 4 `4-web-gui-observer` completed on
`feat/stage8-gui-observer`; see `docs/acceptance/stage-8-subplan-4-web-gui-observer.md`.

- [x] Server: static GUI asset serving (GET/HEAD, extension allowlist, traversal confinement),
  uniform CSP/security headers, `morrow gui` CLI sharing the serve core runner.
- [x] Packaging: wheel ships gitignored `src/morrow/gui_static` via `ignore-vcs = true`; sdist
  force-includes the bundle (build fails loudly without it); verified via `uv build`.
- [x] Frontend scaffold: pnpm-pinned Vite+React+TS+Tailwind 4, Warm Paper tokens both themes,
  bundled Inter/Newsreader/JetBrains Mono, bundle budget gate.
- [x] API client + SyncStore: snapshot + durable event pull + WS hints + gap resync; approval
  staleness gap found by browser smoke and fixed (lifecycle events refresh pending approvals).
- [x] Views: three-column observer shell, Direct card vs React Flow graph, node detail, approvals
  strip, connection banner, a11y (keyboard, dot+label).
- [x] Tests: 10 Python serving/CLI tests, 29 vitest tests, 16/16 scripted CDP browser smoke
  (navigation, live update, keyboard traversal, offline banner, Core-restart resync).
- [x] GUI–CLI parity evidence + acceptance doc; websockets dependency added (user-approved) after
  the smoke found uvicorn had no WS backend over real sockets.
- [x] Validation: full offline gate, Ruff format/check, compileall, `git diff --check`, frontend
  typecheck/test/build, `uv build` wheel+sdist bundle check.
