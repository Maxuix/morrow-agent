# Subplan 4 — Web GUI Run Observer

> Status: completed 2026-09-04 on `feat/stage8-gui-observer`; acceptance
> `docs/acceptance/stage-8-subplan-4-web-gui-observer.md`
> Branch: `feat/stage8-gui-observer`
> Activation base: latest verified `main` with Subplan 3 integrated
> Prerequisite: Subplan 3 verified; explicit user authorization for the frontend toolchain
> (Node/React/TypeScript plus Tailwind and the bundled open-license fonts per the design decision;
> node-graph library selection per roadmap §11.4) — GRANTED 2026-09-03: pnpm + Node 26,
> @xyflow/react, Newsreader + Inter + JetBrains Mono bundled, `gui/` at repo root
> Roadmap authority: stage-8 §8.1–8.4 (information architecture), §11.3–11.4, §8A
> Design authority: `docs/decisions/stage-8-gui-design-language.md` (Warm Paper: dual light/dark
> themes following the system, CSS variables + Tailwind, bundled Inter + serif + JetBrains Mono)

## Objective

Deliver the read-only Web GUI observer: a React + TypeScript client showing Session/Task/
Workflow/Node/Artifact state live, proving the GUI–CLI parity gate on the same Core API.

## Deliverables

- Vite/React/TypeScript client scaffold under a dedicated `gui/` directory (or as approved at
  activation), with API types generated from or checked against the Subplan 3 protocol.
- CLI entry `morrow gui`: starts the same Core server as `morrow serve` and opens the default
  browser at the loopback URL with the session token applied; foreground process model, Ctrl+C
  shuts down gracefully.
- Distribution strategy: development uses the Vite dev server; production GUI assets are
  prebuilt static HTML/CSS/JS (including the bundled fonts) embedded in the Python package and
  served directly by the Core server — an end user installing via `pip`/`uv tool install` never
  needs Node.js. The packaging contract is: pinned Node and package-manager versions with a
  committed lockfile and deterministic build; the wheel/sdist includes the prebuilt assets as
  package data (Hatch include rules) so offline install + `morrow gui` works; no CDN or runtime
  font/asset fetching; font licenses and third-party notices shipped alongside; the build is
  compatible with a restrictive CSP (no inline scripts/styles); a bundle-size budget is enforced
  in the frontend CI to keep the wheel lean. (Desktop packaging/installers remain Stage 10.)
- Design-token foundation per the Warm Paper decision: CSS-variable tokens for light/dark, Tailwind
  wired to the tokens, the three bundled font families with their assigned roles (serif for reading content,
  sans for UI chrome, mono for commands/diffs/previews), status-dot + label treatment for run
  states, and the React Flow theme derived from the same tokens.
- Main layout per roadmap §8.1: Session/Task navigation, main workspace (Chat/Task/Artifacts),
  Workflow panel with node states and budget, tool/approval/status bar.
- Read-only projections only: Task/Workflow/Node/Agent/Tool/Artifact/budget views; approval
  *display* is in scope but approval *resolution* UI waits for Subplan 6 unless trivially available
  through the API.
- Client state built as snapshot + ordered event stream + gap detection + resync query; a stale
  client resyncs instead of showing wrong state; disconnects surface as an honest connection
  indicator.
- Accessibility baseline from the start: keyboard navigation, state not conveyed by color alone.
- GUI–CLI parity evidence: the same WorkflowRun rendered by CLI and GUI agrees; reconnect produces
  no lost or duplicated state.

## Key semantics

- The GUI never imports runtime-internal objects as a business interface and never reads
  authoritative stores; UI caches are projections.
- Direct (single-node) tasks render as a simple linear card, not a forced large canvas.

## Validation

- Component/contract tests against the scripted server fixture (or in-process server): snapshot +
  event application, gap resync, reconnect, parity checks.
- Browser-level smoke via the browser-testing skill on the loopback server: navigation, live status
  updates, reconnect banner, keyboard-only traversal of the run view.
- Repository gates: Ruff format/check, compileall, `git diff --check`, plus the frontend toolchain's
  own typecheck/test commands recorded in the subplan closeout.

## Out of scope

Editing of any kind (Subplan 5), run-control actions (Subplan 6), Context/Learning/Skill
management (Subplan 9), desktop packaging (Stage 10).
