# Progress Tracker

## Current status

Subplan 89 is complete and fast-forward integrated into local `main` through evidence tip
`a5a5d61`. All 18 cells, Lanes A–G, the real current-macOS Seatbelt gate, complete offline suite and
quality gates passed. S7P-09 is not active.

## Active task

None. Await an explicit user decision before opening S7P-09.

## Preparation evidence

- Current non-live collection discovers `1297` selected tests; two live tests are deselected. This
  is collection evidence only, not a regression-pass claim.
- The S7P-08 checklist contains 18 required capability surfaces and explicitly requires failure
  and recovery evidence for high-risk paths.
- Existing tests already expose two real macOS Seatbelt selectors guarded against nested Codex
  Seatbelt execution. Both are mandatory host-level gates for this subplan.
- Subplan 87 removed Runtime completion inference. The S7P-08 validation cell will test scoped
  validation telemetry and model-owned stop truth, not revive the superseded gate.
- No live Provider/model/Pi/MCP/network/credential run, dependency change or production mutation
  occurred during preparation.
- `tests/acceptance/s7p08_single_agent_matrix.json` contains exactly 18 unique capability rows,
  executed positive selectors, and explicit failure/recovery selectors for every high-risk row.
- Its strict contract rejected seven initially stale guessed selectors; all were replaced with
  current collected node IDs. It also records the superseded Stage 2 structured/handoff file and
  the Subplan 87 validation-stop replacement instead of counting them as current evidence.
- The focused ledger behavior command passed `94 passed in 12.52s`. A new production-composition
  test proves interactive and headless dispatch share AgentLoop, frozen Provider/Model, RunPolicy,
  ToolSet, Permission, Preference, Skill and prompt/context evidence, plus terminal stop meaning.
- No focused failure required production-code repair.
- Lane results: A `157 passed, 1 deselected`; B `132 passed`; C `137 passed`; D `243 passed`;
  E `260 passed, 1 deselected`; F `157 passed`; G `198 passed`.
- On Darwin with no enclosing `CODEX_SANDBOX`, both exact macOS Seatbelt selectors ran and passed:
  `2 passed in 0.75s`, zero skip.
- Final collection found `1300` offline selections and two live deselections. Mini Eval self-check
  passed all 10 tasks. The final offline rerun passed `1300 passed, 2 deselected in 91.64s`.
- `uv sync`, Ruff format/check, compileall, both CLI help entrypoints and `git diff --check` passed.
  The final base-to-tip audit found no production, dependency, runtime-default or public-event
  change and strengthened two initially permissive evidence assertions.

## Next action

Do not start S7P-09 automatically.

## Blockers

Remote publication is not authorized. Local `main` is ahead of `origin/main`; no push was attempted,
and remote synchronization remains pending explicit authorization.
