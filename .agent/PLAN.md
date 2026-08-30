# S7P-10 GO Upgrade Proof

> Status: active
> Active subplan: 98 — targeted current-code complex-run proof
> Activation base: `main@59789bc`
> Source authority: current user request, S7P-10 upgrade condition and current code/evidence

## Objective

Execute the one bounded current-main complex-run proof required by the S7P-10 CONDITIONAL GO
decision. Do not repeat the accepted 14-run campaign.

## Decisions

- Use difficult task EXTERNAL-003 because it exercises implementation, multiple tool rounds and
  validation and has an older passing reference point.
- Use the current configured DeepSeek Provider through an isolated state containing no credential
  value, with ordinary AgentLoop and capability policy composition.
- A task may fail for model quality and still prove the leaf runtime boundary only if its terminal
  source and all tool states are complete. An unexplained runtime failure cannot upgrade the gate.
- Workflow implementation remains out of scope until this proof closes.

## Execution order

1. Freeze source, workspace, config projection and evidence boundary.
2. Execute one externally bounded AgentRun and frozen verifier.
3. Repair and rerun only if evidence identifies a leaf runtime defect.
4. Publish the updated S7P-10 verdict and run offline/static validation.
5. Commit, fast-forward into local `main` and retire the branch.

## Completion

- Current-code complex-run evidence satisfies or rejects the explicit GO upgrade condition.
- The decision preserves single-sample and unavailable-metric limitations.
- All relevant validation passes and no secret/raw model evidence enters Git.
