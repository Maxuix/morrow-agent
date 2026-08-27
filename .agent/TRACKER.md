# Progress Tracker

## Current status

Subplan 89 is active on `chore/s7p-08-single-agent-matrix`, based on verified local
`main@d84ac0d`. The strict 18-cell ledger now resolves to current collected selectors, and its
focused behavior run passed all 94 selected tests. No current-contract product regression was
reproduced; the only evidence gap was closed with a cross-entrypoint integration test.

## Active task

Run the separately attributable Stage 1 through Stage 6 and S7P-00 through S7P-07 regression
lanes, then run the current-macOS host Seatbelt gate.

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

## Next action

Run Lanes A–G exactly as frozen in Subplan 89 and record each result before the host Seatbelt and
full quality gates.

## Blockers

None during plan activation. If this task remains nested inside macOS Seatbelt when the host gate
runs, the two required selectors must be executed from a genuine host-level process; an unexplained
skip blocks S7P-08 completion.
