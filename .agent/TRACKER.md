# Progress Tracker

## Current status

Subplan 90 remains active. The Pi 0.84.2 runtime-event adapter repair is verified and committed at
`86e97bc`. Campaigns r2–r4 are retained outside Git as blocked/aborted evidence and will not be
continued or reused. No new formal admission is allowed under the current token ceiling.

## Active task

Await explicit approval to raise the total token ceiling, then resolve the unrelated untracked
`docs/notes/` source change without overwriting it, refreeze a clean source pin, and start a fresh
campaign. Pi normalization now supports the observed 0.84.2 `session` event, indexless turn events,
optional reasoning-token usage, `glob`, and `agent_end` without a semantic stop. The last case is
truthfully classified `runtime_failed`, not completed or evidence-unavailable.

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
- Morrow's repaired bounded Agent probe completed normally with `stop`, 7,139 Provider tokens and
  zero tool calls. The prior failure combined a 309-digit tool-schema bound rejected by OpenCode Go
  with MiMo's repeated nonsemantic terminal chunk.
- Morrow's OpenAI-compatible stream still emits `ModelCost.unavailable()`. Under the user's explicit
  no-currency-ceiling approval, cost is recorded when available but is no longer a readiness,
  completeness or comparison gate; token accounting remains mandatory.

## Next action

Do not admit another formal run. Ask for a 27,000,000-token total ceiling, then obtain a clean source
checkout without modifying the unrelated untracked notes, refreeze the source/profile/plan hashes,
and execute a new immutable schedule from ordinal 1.

## Blockers

- Formal live campaign: the current 15,000,000-token total ceiling is insufficient. Formal
  admissions account for 3,567,421 tokens plus one interrupted request with unavailable usage; a
  fresh complete campaign projects about 17.9M more. The recommended total ceiling is 27,000,000.
- The source checkout is dirty only because of unrelated untracked `docs/notes/`; ownership and
  disposition are unresolved, so the evaluator cannot freeze a clean source pin yet.
- Remote publication remains unauthorized; raw evidence durability and any push must be reported
  honestly rather than assumed.
