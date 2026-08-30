# Subplan 100 — S7P-10 EXTERNAL-003 Attributed Rerun

> Status: active
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
