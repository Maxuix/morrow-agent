# Subplan 14 — Configuration, Provider, and Structured-Completion Remediation

> Stage: 1B remediation
> Status: completed
> Parent: [Stage 1 implementation plan](../PLAN.md)
> Depends on: Subplan 13

## Objective

Restore the local configuration gate, make every user confirmation describe the exact pending mutation, resolve credentials consistently, enforce one total structured-completion budget, and make Provider diagnostics truthful.

## Required design decisions

1. Forbidden configuration fields are rejected only when local rules first identify a persistence/configuration attempt. Ordinary discussion of Providers, models, credentials, permissions, or security remains ordinary chat with zero extraction calls.
2. `ConfigExtractionResult` must enforce discriminated consistency: `config_patch` requires a valid patch, `clarification_required` requires one bounded question, and `no_change` carries neither. Invalid combinations enter the single repair path and never fall through to chat silently.
3. Natural-language and deterministic edits share one preview/apply flow. Command parsing may construct a validated `ConfigPatch`, but no global/workspace/session mutation occurs until the terminal confirms a preview showing scope, target, every operation, field, and value/removal.
4. Credential resolution is one injected resolver shared by startup, add, configure, test, show, and active-provider construction. Its order is exactly `environment_credential(provider_id)` followed by `CredentialStore.get(credential_ref)`; environment values never enter YAML or visible output, and `provider show` reports only whether this resolver can obtain a value while remaining offline.
5. Non-secret Provider edits reuse the existing resolved credential. `provider configure <id> --base-url ...` does not prompt when either credential source is usable. Credential rotation remains inside the existing command family as `provider configure <id> --replace-credential`, which obtains a hidden replacement value, stages/tests it, and publishes its new reference only after success. Because environment credentials have resolver precedence, `--replace-credential` refuses to run while that Provider's environment credential is set and instructs the user to unset it first; a stored replacement must never appear successful while the environment still masks it. If no usable credential exists, configure may prompt once even without the flag because the required connection test cannot otherwise run.
6. Structured completion uses one monotonic deadline across initial and repair calls. The repair request retains the original bounded task instruction and target JSON Schema plus a sanitized validation summary, while all state still enters only through `ContextBuilder`.
7. Provider failures retain a typed `ModelErrorCode`. A failed explicit `provider test` persists a sanitized failure result and returns a non-zero CLI status; `provider show` reports actual resolvability rather than merely the presence of a reference.

## Executable tasks

1. Expand must-trigger, must-not-trigger, mixed-task, and forbidden-field corpora, including ordinary Provider/model/security questions and forbidden persistence requests.
2. Add model validators and orchestration tests for every valid and invalid `ConfigExtractionResult` combination, with exact complete/stream call counts and zero side effects on failure.
3. Refactor `/config edit`, `/workspace edit`, and `/handoff edit` to return a pending patch/preview action; centralize exact preview rendering and apply only after confirmation.
4. Test declined, EOF-cancelled, invalid, conflict, and successful previews, including multi-operation patches and snapshot refresh on success.
5. Introduce an injected/shared credential resolver and use it in active construction, configure, test, and show without leaking the value.
6. Implement the exact `--replace-credential` configure behavior, including refusal while an environment credential masks the store, and ensure `--base-url` alone does not call `getpass` when the shared resolver succeeds.
7. Implement a single deadline for structured completion and a self-contained repair prompt. Test first-call timeout, reduced repair budget, no remaining budget, cancellation, invalid first output, invalid repair, and Handoff fallback.
8. Preserve typed Provider error classification through adapter/service boundaries; make failed Provider test exit non-zero while retaining the old working configuration and credential reference.
9. Add offline CLI tests for add/list/show/configure/test and model list/current, including corrupt global state, missing referenced credentials, environment-only credentials, failed probes, and NetworkGuard enforcement.
10. Run targeted configuration/Provider/structured tests, the complete non-Live suite, Ruff format/check, and compile checks.

## Verification

- Ordinary chat containing sensitive vocabulary reaches the normal stream path and makes zero extraction calls.
- Invalid structured configuration results repair once or fail with zero writes; they never become chat by field mismatch.
- Every edit is byte-for-byte unchanged before confirmation and applies exactly the displayed patch afterward.
- Environment-only credentials can build/test the configured active Provider without being persisted or shown; base-URL-only configure does not prompt for a secret.
- Structured completion never exceeds its one total deadline except negligible scheduler overhead.
- Provider show/test output and exit status match actual credential and connectivity state without exposing secrets.

## Completion criteria

- All confirmed gate, extraction-shape, preview, environment credential, configure prompting, timeout, repair-prompt, Provider diagnostic, and CLI exit-code defects have regression coverage.
- Aggregate config updates preserve Preferences, Providers, and `active_model` under one revision/lock.
- Local inspection commands remain offline.

## Deliverables

- Conservative and correctly scoped configuration routing.
- Exact confirm-before-write configuration commands.
- Consistent credential resolution and truthful Provider diagnostics.
- Deadline-bounded, repairable structured completion.
