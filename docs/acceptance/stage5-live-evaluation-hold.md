# Stage 5 Live Model Evaluation Hold Point

> Date: 2026-08-21
> Status: Preference v2 Reviewer v4 corpus passed on 2026-08-23
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
runs the Preference v2 corpus and ten actual frozen-ContextBuilder adherence probes. The first
complete post-implementation run is recorded below. Offline scripted contracts cannot satisfy these
semantic quality thresholds.

When authorization and a compatible credential are available, the live report records bounded
dataset/prompt/schema/provider/model identifiers, aggregate numerators/denominators,
precision/target ratios, attempts, latency, final pass state, and sanitized signatures for failed
cases only. A failed signature contains case ID, operation kind, scope, synthetic target ID, match
count, and a stable Reviewer-error flag. It excludes user text, statements, raw model output,
reasoning, exception text, and credentials, and must not claim a pass if any target lacks evidence.

## 2026-08-23 Preference v2 result

The Keychain-backed `deepseek-v4-flash` run used an isolated pytest report root. The first attempt
exposed a stale `Session(persisted=...)` live fixture and produced no score; commit `3493c88`
removed that obsolete argument and added a non-live regression. The repaired corpus completed with:

- positive operations: `8/12` (required `11/12`);
- proposal precision: `10/16 = 0.625` (required `>= 0.90`);
- replace/remove targets: `7/8 = 0.875` (satisfies `>= 6/7`);
- safety-negative Active writes: `0` (satisfies `0`);
- next-AgentRun adherence: `10/10` (satisfies `>= 9/10`);
- attempts: `32`; total latency: `173908 ms`.

The sanitized aggregate report is
[`stage5-preference-v2-live-report-2026-08-23.json`](stage5-preference-v2-live-report-2026-08-23.json).
It contains no user text, Preference statements, raw model output, reasoning, or credential. Stage 5
live acceptance is failed, not pending or passed; Subplan 62 owns focused remediation.

### Reviewer v3 remediation replay

The one v3 replay improved positive operations to `11/12`, targets to `8/8`, and retained `10/10`
adherence with zero safety-negative Active writes. Precision improved to `13/16 = 0.8125` but stayed
below `0.90`. Sanitized case signatures isolated three failures: global cancellation was emitted as
replace instead of remove; explicit repository-content denial and a hidden-control message each
emitted an extra workspace add. The bounded report is
[`stage5-preference-v2-live-report-v3-2026-08-23.json`](stage5-preference-v2-live-report-v3-2026-08-23.json).

No immediate second replay was run. Reviewer v4 tightens only those general semantic boundaries;
Stage 5 remains failed until a reviewed final replay meets every frozen threshold.

### Reviewed Reviewer v4 final replay

After the one Subplan 62 Grok review and independently validated report/test hardening, the single
final v4 replay passed every frozen threshold:

- positive operations: `12/12`;
- proposal precision: `14/14 = 1.0`;
- replace/remove targets: `8/8 = 1.0`;
- safety-negative Active writes: `0`;
- next-AgentRun adherence: `10/10`;
- attempts: `32`; total latency: `139066 ms`;
- failed cases: none.

The exact sanitized report is
[`stage5-preference-v2-live-report-v4-final-2026-08-23.json`](stage5-preference-v2-live-report-v4-final-2026-08-23.json).
The credential remained in macOS Keychain and was injected only into the live subprocess. Stage 5
real-Provider acceptance is complete, subject only to final repository gates and branch closeout.
