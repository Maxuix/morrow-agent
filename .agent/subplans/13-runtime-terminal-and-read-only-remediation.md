# Subplan 13 — Runtime, Event, Terminal, and Read-Only Remediation

> Stage: 1B remediation
> Status: completed
> Parent: [Stage 1 implementation plan](../PLAN.md)
> Depends on: Subplan 12

## Objective

Make production identifiers unique, enforce the public event lifecycle for every accepted turn, distinguish successful from truncated model streams, make EOF/interrupt behavior terminate safely, and enforce the independent read-only downgrade.

## Required design decisions

1. Create one production ID source per application composition and inject it into workspace, session, turn, and event creation. Test doubles remain deterministic, but production must never import or instantiate `FixedIdSource`.
2. Align ID-source call signatures explicitly: workspace generation uses a bound prefix or a typed factory rather than passing a prefix-requiring method as a zero-argument callable.
3. `AgentRuntime` owns the public lifecycle invariant. Once a user turn is accepted it emits exactly one `turn.started` and exactly one `turn.completed`, including unexpected Provider/adapter exceptions. Safe typed errors may precede completion; raw exceptions and secrets may not escape in events.
4. Only an explicit normal Provider finish maps to public `turn.completed(stop)`. `length`, content filtering, unsupported tool-call finishes, malformed chunks, missing finish signals, and abrupt stream endings emit a sanitized typed `error` followed by exactly one public `turn.completed(error)`. Richer finish detail remains adapter-internal, no new public `FinishReason` is added in Stage 1, and assistant text enters history if and only if the public finish reason is `stop`.
5. EOF at the primary prompt is an exit request. If further confirmation is possible, preserve the dirty-session confirmation flow. The independent save/discard/cancel switch prompt is also a required confirmation. If stdin is closed/EOF recurs during either confirmation, terminate once with an explicit warning and exit code 2, without writing an independent Handoff or switching/resetting the session; never spin or repeatedly call the prompt.
6. The degraded mode is workspace-state-wide but not application-global. If either Profile or Handoff is corrupt or uses an unsupported schema, the session starts independently with no Handoff injection; `/continue` and every workspace-persistent mutation (workspace Preferences, Profile, Handoff, onboarding, save/update/reset/clear) are unavailable, and exit never attempts a workspace write. Valid counterpart documents may be displayed read-only but are not loaded or mutated during that session. A corrupt/unsupported workspace Preferences document alone is isolated as a non-overwritable empty workspace layer: workspace-preference mutation is unavailable, but valid Profile/Handoff state may still load and `/continue` remains available; this narrower condition does not trigger the Profile/Handoff degraded mode. Ordinary chat, session-only Preferences, global Preferences, and Provider management remain available through their normal boundaries. Missing or `state=cleared` version-2 documents are valid states and do not trigger degraded mode.

## Executable tasks

1. Add failing tests for duplicate event/session IDs and the broken injected workspace ID factory; then introduce and wire one production-safe ID source.
2. Add Provider-raises tests covering exceptions before output and after partial output, asserting one complete public lifecycle and no partial assistant history admission.
3. Add adapter contract tests for `stop`, `length`, content-filter/unsupported finishes, reasoning-only chunks, missing finish, and malformed/empty responses.
4. Refactor runtime exception/finalization handling so cancellation stays a completion reason, retry rules remain limited to pre-visible retryable errors, and no path double-completes.
5. Add terminal tests for clean EOF, dirty independent EOF, closed stdin during exit confirmation, closed stdin during the independent save/discard/cancel switch prompt, dirty continuation save, cancellation during save, and successful conversation after Ctrl+C.
6. Replace the EOF busy-loop path with an explicit terminal outcome that preserves the roadmap's save/discard guarantees and returns a deterministic exit code.
7. Add degraded-mode integration tests for incompatible Profile, incompatible Handoff, valid counterpart documents, `/continue`, every workspace mutation, session/global Preferences, Provider management, chat, and exit. Assert the workspace-state-wide negative rules and the allowed non-workspace paths explicitly.
8. Enforce read-only behavior at the application/command boundary rather than relying only on individual write services.
9. Run targeted runtime/adapter/terminal tests, the full non-Live suite, Ruff format/check, and compile checks.

## Verification

- Production sessions, turns, and events use unique IDs; deterministic tests remain injectable.
- Normal, retry, provider exception, invalid finish, cancellation, and empty response paths all satisfy one-start/one-completion lifecycle rules.
- Partial/truncated assistant output is visible if already streamed but is never admitted to history as a successful assistant message.
- Closed stdin cannot busy-loop; dirty independent content never silently writes or overwrites a Handoff.
- Read-only downgrade cannot load continuity state or invoke any workspace-state persistence path; explicitly allowed global/session paths remain available.

## Completion criteria

- Confirmed ID, lifecycle, finish-reason, EOF, and read-only defects have direct regression tests.
- Real terminal behavior remains compatible with Ctrl+C/Ctrl+D acceptance semantics.
- No Stage 2 event types or task loop are introduced.

## Deliverables

- Production-safe ID composition.
- Total public event lifecycle enforcement and strict finish mapping.
- Bounded EOF behavior and enforced independent read-only mode.
