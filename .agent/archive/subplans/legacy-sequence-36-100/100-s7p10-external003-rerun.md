# Subplan 100 — S7P-10 EXTERNAL-003 Attributed Rerun

> Status: completed and verified; GO upgrade condition met
> Branch: `chore/s7p10-external003-rerun`
> Activation base: local `main@519d4d7`
> Authorization: user explicitly requested one EXTERNAL-003 rerun

## Mission

As a developer using Morrow for a difficult repository task, implement Reactive Cells stable
propagation in a fresh evaluator workspace and pass the unchanged external verifier. This is the
single post-repair proof sample, not a campaign repetition.

## Public scenario

- Entry: `eval.py prepare` followed by ordinary `eval.py run-morrow`.
- Preconditions: clean source, fresh workspace/state, configured current Provider/Model and existing
  compatible CredentialRef.
- Oracle: verifier exit 0, all required paths present, complete tool accounting, no unexpected
  workspace paths and an attributable terminal.
- Cleanup: leave protected temporary evidence mode-restricted; commit only bounded safe facts.
- Risk: one Live model run may consume tokens and may nondeterministically fail model quality.

## Validation

- Frozen `eval.py verify EXTERNAL-003` result.
- Focused evaluator/observability tests.
- Complete offline suite and standard static gates.

## Result

- The one Live run completed normally in 65.385 seconds after nine completed model requests.
- Nine tools settled: eight succeeded and one `invalid_command` failure was recovered; evaluator
  diagnostics recorded zero invalid arguments, unaccounted calls or basic-tool blockers.
- Only `react.py` changed. The frozen verifier reported four failures, so the scenario remains
  `FAIL_MODEL`, not PASS.
- This attributable non-runtime result with complete accounting satisfies the predeclared S7P-10
  upgrade branch. The verdict is GO without changing the historical 14-run quality result.
- Focused tests passed 101; complete offline tests passed 1,285 with two Live tests deselected. All
  static gates passed.
