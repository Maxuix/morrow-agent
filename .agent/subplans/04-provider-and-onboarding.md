# Subplan 04 — Provider and Onboarding

> Stage: 1A  
> Status: pending  
> Parent: [Stage 1 implementation plan](../PLAN.md)

## Objective

Provide one real, explicitly configured model connection through general Adapter/Provider/Model boundaries while preserving credentials and offline local behavior.

## Prerequisites

- Subplans 02 and 03 are complete.

## Tasks

1. Define typed Provider-instance, model, preset, active `ModelRef`, credential-reference, and last-test-result configuration models.
2. Add the OpenCode Go preset as updateable data using the roadmap's verified endpoint and model ID.
3. Implement the OpenAI-compatible async adapter for streaming and non-streaming completion.
4. Normalize visible text, completion, and errors to internal model events; consume or discard Provider-specific reasoning fields.
5. Map authentication, network, rate-limit, timeout, invalid-response, and internal failures without leaking raw SDK data.
6. Implement keyring-backed credentials and explicit environment-variable lookup for test/non-interactive use.
7. Implement one UI-independent Provider add/test/publish use case that Subplan 10 must reuse. It accepts an explicit activation policy: set `active_model` only when none exists; otherwise preserve the current model. Interfaces supply the secret and confirmations; application/services must not prompt, echo, or render.
8. Publish all Provider configuration and `active_model` changes through Subplan 03's `GlobalConfigStore` locked whole-document update, preserving global Preferences. Stage a versioned credential reference, explicitly test it, and only then publish a valid active model.
9. Ensure failed initial setup publishes no usable half-configuration and returns a clear retry path.
10. Make all local inspection and startup validation offline; only explicit connection tests and model turns may connect.
11. Use `MemoryCredentialStore` or an isolated disposable keyring backend in default tests; only explicit Live/manual flows may access the user's real keyring.
12. Add explicit Live smoke tests for the current preset while keeping them outside default CI.

## Verification

- Empty state can complete onboarding and resolve one validated `ModelRef`.
- Configuration, logs, events, terminal output, and test artifacts contain no credential sentinel.
- Reasoning fields never become visible-text deltas, history, or Handoff inputs.
- Local configuration reads pass under the network guard.
- Default tests neither create/read user keyring entries nor write under the user's Morrow data directory.
- Provider publication preserves pre-existing global Preferences and obeys the explicit activation policy.
- Fake adapters satisfy the same contracts as the OpenAI-compatible adapter.
- A real marked Live test can stream visible output or returns a correctly classified service error.

## Completion criteria

- Runtime consumers receive only a validated `ModelRef` and a constructed `ModelProvider`.
- Core/application code contains no OpenCode-specific branches.
- Secret handling and network boundaries are demonstrated by automated tests.

## Deliverables

- OpenAI-compatible adapter and OpenCode Go preset.
- Credential adapter and onboarding application service.
- Offline adapter contracts and explicit Live smoke coverage.

## Interface boundary

Subplan 04 exposes non-interactive use cases. Subplan 06 owns no-echo secret collection, confirmation, and the startup rule that missing `active_model` invokes Provider add before entering the REPL.
