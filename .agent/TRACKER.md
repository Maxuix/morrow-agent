# Progress Tracker

## Current status

Subplan 90 is active on `feat/s7p-09-direct-pi-baseline`, based on verified local
`main@1fd7e229bef276d1a0361e775ce800ade4b318fc`. S7P-08 is complete. S7P-09 Phase A may implement
and validate the offline comparison harness; no formal live campaign run has started.

## Active task

Complete the two agent-specific offline runners. Morrow must inject the bounded evaluation
ApprovalPort only through ordinary CapabilityPolicy/ToolExecutor/bootstrap composition and derive
the normalized trace from actual safe process-local facts. Pi must run with a content-hashed policy
extension plus OS confinement and emit the same safe schema. No live model request is authorized.

## Preparation evidence

- S7P-00 protocol v1 freezes two repetitions, all seven result classes, tool accounting, Morrow
  thresholds, Pi task IDs `MORROW-003`, `MORROW-005`, `EXTERNAL-003`, `EXTERNAL-004`, and maximum
  Pi quality deficit `1`.
- `eval.py` currently supports `start`, `rebuild`, `finalize`, `summarize` and `self-check`; its
  summary deliberately reports Pi comparison as `NOT_EVALUATED`.
- Installed Pi reports version `0.84.2` and supports non-interactive JSON event output with final
  message usage, tool start/end, turn, compaction and retry events.
- Morrow's headless record exposes safe AgentRun terminal metrics, but the default headless
  ApprovalPort denies commands that need approval. The planned evaluation ApprovalPort must remain
  behind the ordinary CapabilityPolicy and cannot override denial.
- The current Morrow active model is `opencode-go/mimo-v2.5`; Pi's model catalog does not list that
  exact model. No credential readiness check or live model call was made during plan preparation.
- The campaign requires 20 Morrow and 8 Pi primary runs. Each uses a fresh workspace and state;
  paired order is counterbalanced before outcomes are observed.
- The verified harness slice now freezes the strict plan/hold approval, 28-entry schedule, safe
  Morrow/Pi normalizers, real CapabilityPolicy equivalence matrix, create-only admission and
  budget boundaries, watchdog/raw hash capture and mechanical paired comparison/baseline output.
- Focused evaluator tests passed `35`; dataset self-check passed `10/10`; S7P-08 regression passed
  `3`; full non-live passed `1313`, with two live deselections. Ruff, compileall, CLI and diff gates
  passed. No live Provider/Pi/credential/network action occurred.
- Audit confirmed ordinary `morrow run` intentionally uses `HeadlessApprovalPort` and therefore
  cannot execute approval-requiring project commands for the formal AUTO_SAFE lane. The evaluation
  port exists but is not yet wired into the production composition path; generic process capture
  alone is not being mislabeled as the complete Morrow runner.

## Next action

Wire and test the agent-specific runners without a live model call, then commit the final Phase A
harness candidate. Do not start paid execution until the common-model and spend hold point is
resolved.

## Blockers

- Formal live campaign: exact common Provider/model and a total token/currency ceiling require user
  selection and approval. The current active Morrow model is ineligible for same-model Pi A/B.
- Remote publication remains unauthorized; raw evidence durability and any push must be reported
  honestly rather than assumed.
