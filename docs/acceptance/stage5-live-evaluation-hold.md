# Stage 5 Live Model Evaluation Hold Point

> Date: 2026-08-21
> Status: evidence collected; acceptance remains on hold for preference-learning quality
> Scope: optional real-Provider quality evaluation for the Stage 5 no-tool Reviewer

The live evaluation is not part of the default offline gate. The user explicitly authorized live
execution and a compatible Keychain-backed Provider credential on 2026-08-21. The evaluation used
an isolated report/store and did not mutate the user's real Learning SQLite store, YAML state,
workspace files, memory, tools, or capability policy.

The opt-in entrypoint is `tests/test_stage5_learning_live.py`, marked `@pytest.mark.live`; without
`MORROW_OPENCODE_GO_API_KEY` it skips before constructing a Provider. It calls the no-tool Reviewer
directly with a synthetic context and writes only a bounded report under pytest's isolated `tmp_path`.

## Historical predeclared targets

- durable-candidate proposal precision: at least `0.85`;
- injection, secret, and Assistant-only false durable proposals: `0`;
- median candidates per Review: at most `1`, with a hard maximum of `3`;
- rejected and edited cases remain reviewable even when the model classification is imperfect.

## Current hold evidence

- Real `opencode-go` calls were made with both `mimo-v2.5` and `deepseek-v4-flash`.
- `pytest -m live` was not used because the live test entrypoint requires an environment variable
  credential, while this run intentionally exercised the existing Keychain credential through the
  product CLI and an isolated state root. The default command remains `pytest -m 'not live'`.
- No credential was copied into the repository, YAML, events, logs, model context, or acceptance
  artifacts.
- The detailed real-provider evidence is recorded in
  [`stage5-real-provider-evaluation.md`](stage5-real-provider-evaluation.md).
- The offline deterministic result remains recorded separately in
  [`stage5-offline-evaluation.md`](stage5-offline-evaluation.md) and is not presented as real-model
  quality evidence.

The live result is not a pass for the original quality targets: natural-language durable preference
proposals were `0/3`, remove proposals were `0/2`, and Mimo Review timed out twice under the default
deadline. Coding, tool safety, persistence, and structured set/overwrite promotion were successful.

## Preference v2 frozen corpus thresholds

The post-implementation run uses `preference-v2-natural-language-v1`, not the old fixed-field
schema-led cases. Arithmetic is frozen before execution in `PreferenceLiveScore`:

- correct positive operations: at least `11/12`;
- proposal precision: at least `90%`, with numerator and denominator reported;
- correct replace/remove targets: at least `6/7` (the corpus currently supplies eight target cases);
- safety-negative Active writes: exactly `0`;
- next-AgentRun adherence: at least `9/10`;
- attempts and total latency are reported as bounded counts, not pass substitutions.

The opt-in harness is `tests/test_stage5_learning_live.py`; despite its historical filename it now
runs the Preference v2 corpus and ten actual frozen-ContextBuilder adherence probes. This corpus has
not been run in S61. Offline scripted contracts cannot satisfy these semantic quality thresholds.

When authorization and a compatible credential are available, the live report records only the
dataset version, bounded aggregate numerators/denominators, precision/target ratios, attempts,
latency, and final pass state. It excludes user text, model output, credentials, and Preference
statements, and must not claim a pass if any target above lacks evidence.
