# Progress Tracker

## Current status

Subplan 90 is active on `feat/s7p-09-direct-pi-baseline`, based on verified local
`main@1fd7e229bef276d1a0361e775ce800ade4b318fc`. S7P-08 is complete. S7P-09 Phase A may implement
and validate the offline comparison harness; no formal live campaign run has started.

## Active task

Freeze the clean campaign source/profile/evidence pins and complete no-secret readiness. The two
agent-specific offline runners are implemented: Morrow injects the bounded approval port through
ordinary bootstrap/CapabilityPolicy/ToolExecutor composition and projects only in-memory safe
facts; Pi uses an explicit policy extension plus Seatbelt-confined bash. The formal 28-run campaign
remains held until every gate passes.

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
- The current Morrow active model is `opencode-go/mimo-v2.5`; Pi 0.84.2's installed catalog contains
  that exact provider/model and endpoint with a 1,000,000-token context window and 128,000-token
  maximum output. Pi's default selection now points to it, but Pi credential readiness is absent.
- The campaign requires 20 Morrow and 8 Pi primary runs. Each uses a fresh workspace and state;
  paired order is counterbalanced before outcomes are observed.
- The verified harness slice now freezes the strict plan/hold approval, 28-entry schedule, safe
  Morrow/Pi normalizers, real CapabilityPolicy equivalence matrix, create-only admission and
  budget boundaries, watchdog/raw hash capture and mechanical paired comparison/baseline output.
- Focused evaluator tests passed `37`; dataset self-check passed `10/10`; S7P-08 regression passed
  `3`; full non-live passed `1315`, with two live deselections. Ruff, compileall, CLI and diff gates
  passed. The only live action was the approved bounded Morrow readiness probe; no Pi request or
  credential-value access occurred.
- Audit confirmed ordinary `morrow run` intentionally uses `HeadlessApprovalPort` and therefore
  cannot execute approval-requiring project commands for the formal AUTO_SAFE lane. The dedicated
  evaluator runner now supplies its bounded port to the same production composition function;
  generic process capture is not mislabeled as the Agent runner.
- The evaluator now provides create-only `run-morrow` and `run-pi` commands. Morrow uses ordinary
  production composition with `EvaluationApprovalPort`; its projection discards full arguments and
  content. Pi disables mutable user resources, pins the approved model and tool set, and loads only
  the content-hashed policy extension. The extension confines bash with macOS Seatbelt, denies task
  network and `.git` writes, bounds commands to 120 seconds and preflights workspace paths.
- Morrow's no-secret Provider readiness probe passed. Pi auth check returned only
  `credentials_not_configured`; no Pi model request was attempted.

## Next action

Run the complete offline/static gate, commit this verified Phase A/B slice, then create the clean
evaluation worktree and freeze exact profiles/source/evidence pins. Do not admit a formal run until
Pi credential readiness and both model-probe hashes are available.

## Blockers

- Formal live campaign: Pi credential readiness, exact served revision/sampling evidence and the
  remaining clean source/evidence pins are still required.
- The approved budget is a hard 5,000,000-token ceiling with no currency ceiling. Cost remains a
  mandatory measured metric and is never inferred as zero.
- Remote publication remains unauthorized; raw evidence durability and any push must be reported
  honestly rather than assumed.
