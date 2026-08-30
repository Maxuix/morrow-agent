# S7P-10 Attributed Proof Rerun

> Status: completed; GO upgrade condition met
> Active subplan: none
> Activation base: `main@519d4d7`
> Source authority: current user authorization, Subplans 98–99 and current evaluator code

## Objective

Run exactly one fresh EXTERNAL-003 proof after the unified model-failure-chain repair. Decide whether
the S7P-10 GO upgrade condition is now met using the existing frozen task/verifier and safe durable
origin evidence; do not repeat the accepted 14-run campaign.

## Decisions

- Use current configured `opencode-go/deepseek-v4-flash` through a fresh mode-0700 isolated state
  containing only its Provider/Model configuration and CredentialRef.
- Exercise the ordinary public evaluator path: prepared workspace, `run-morrow`, durable AgentRun
  observation and frozen EXTERNAL-003 verifier.
- Execute one Live run only, under the existing 1,800-second external bound. No no-tool probe or
  additional Provider request is allowed.
- Do not repair code during this testing-only task. Classify and report the observed result.

## Completion

- Exactly one current-main EXTERNAL-003 run has safe runtime evidence and a verifier result.
- Internal failure, if any, is attributable as Provider, Adapter or Runtime without raw payloads.
- S7P-10 acceptance evidence and execution state reflect the mechanical result.
- Relevant offline/static validation passes and verified evidence is integrated locally.

Completed by Subplan 100. The scenario is truthfully retained as `FAIL_MODEL`; its normal AgentRun
terminal and complete tool accounting satisfy the separately defined S7P-10 GO upgrade condition.
