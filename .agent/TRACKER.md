# Progress Tracker

## Current status

Subplan 90 is active on `feat/s7p-09-direct-pi-baseline`, based on verified local
`main@1fd7e229bef276d1a0361e775ce800ade4b318fc`. S7P-08 is complete. S7P-09 Phase A may implement
and validate the offline comparison harness; no formal live campaign run has started.

## Active task

Resolve the Morrow Agent streaming readiness blocker before freezing the final comparison plan.
The checkout is clean and both runners are implemented. Pi now resolves the same Keychain-backed
credential without copying it, passes auth readiness and completed its exact-model no-tool probe.
Morrow's non-stream Provider test passes, but its two bounded Agent probes both failed `internal`
before any tool call. The formal 28-run campaign remains held.

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
- Pi now uses a command reference to the existing Morrow Keychain entry, with no secret in Pi
  configuration. `pi auth check --no-refresh` returned `ready/api_key`; the exact no-tool probe
  completed on `opencode-go/mimo-v2.5` with `stop`, 404 total tokens and Provider cost.
- Morrow's non-stream Provider readiness test passed. Two bounded Agent no-tool probes then failed
  identically with request/terminal code `internal`, zero tool calls and unavailable usage/cost.
  The retry bound is exhausted; no further model request was made.
- Local adapter inspection confirms Morrow's OpenAI-compatible stream currently emits
  `ModelCost.unavailable()` even on success. The approved budget needs no currency ceiling, but the
  original complete-cost evidence gate cannot pass without an explicit accounting-contract change.

## Next action

Do not admit a formal run. First resolve or explicitly reclassify the repeatable Morrow streaming
failure, then decide whether complete cost remains mandatory or a frozen derived/optional cost
contract is authorized. Re-run only the bounded Morrow probe after that blocker changes.

## Blockers

- Formal live campaign: Morrow Agent streaming readiness, exact served revision/sampling evidence
  and the final comparison-plan/evidence pins are still required.
- Complete cost evidence is incompatible with the current Morrow adapter, which reports cost as
  unavailable; no currency ceiling is enforced, but the original reporting gate remains frozen.
- The approved budget is a hard 5,000,000-token ceiling with no currency ceiling. Cost remains a
  mandatory measured metric and is never inferred as zero.
- Remote publication remains unauthorized; raw evidence durability and any push must be reported
  honestly rather than assumed.
