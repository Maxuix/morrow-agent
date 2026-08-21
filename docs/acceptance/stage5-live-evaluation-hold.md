# Stage 5 Live Model Evaluation Hold Point

> Date: 2026-08-21
> Status: pending explicit authorization and a compatible credential
> Scope: optional real-Provider quality evaluation for the Stage 5 no-tool Reviewer

The live evaluation is not part of the default offline gate. It may run only after the user
explicitly authorizes live execution and selects a compatible Provider credential. The evaluation
must use synthetic bounded `LearningContext` fixtures in an isolated report/store and must not
mutate the user's real Learning SQLite store, YAML state, workspace files, memory, tools, or
capability policy.

The opt-in entrypoint is `tests/test_stage5_learning_live.py`, marked `@pytest.mark.live`; without
`MORROW_OPENCODE_GO_API_KEY` it skips before constructing a Provider. It calls the no-tool Reviewer
directly with a synthetic context and writes only a bounded report under pytest's isolated `tmp_path`.

## Predeclared targets

- durable-candidate proposal precision: at least `0.85`;
- injection, secret, and Assistant-only false durable proposals: `0`;
- median candidates per Review: at most `1`, with a hard maximum of `3`;
- rejected and edited cases remain reviewable even when the model classification is imperfect.

## Current hold evidence

- No live Provider call or network request was attempted for this hold point.
- `pytest -m live` was not run; the default command remains `pytest -m 'not live'`.
- No credential was copied into the repository, YAML, events, logs, model context, or acceptance
  artifacts.
- The offline deterministic result is recorded separately in
  [`stage5-offline-evaluation.md`](stage5-offline-evaluation.md) and is not presented as real-model
  quality evidence.

When authorization and a compatible credential are available, the live report must record only
bounded provider/model/prompt/schema versions, case IDs, counts, reason codes, and manually
reviewable sanitized Candidate drafts. It must not claim a pass if any target above lacks evidence.
