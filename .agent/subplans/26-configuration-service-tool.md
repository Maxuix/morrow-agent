# Subplan 26 — Configuration Service and Standard Tool

> Status: pending
> Depends on: Subplan 25

## Goal

Create one strictly typed `update_configuration` RegisteredTool whose thin handler delegates
all Preferences/Profile behavior to the shared ConfigPatchService through an application-
owned typed command/result API. Directly prove its schema, authorization, state, result, and
single-call ToolExecutor contracts without registering it in production composition yet.

The old natural-language Gate/structured route remains temporarily active in production
through this subplan so no intermediate tree exposes two production configuration paths. It
continues to emit the unchanged `ConfigPatch` extraction schema and enters the shared command
engine through a compatibility adapter.

## Scope

- Flat, strict single-operation argument model.
- Application-owned typed command/result API and a legacy ConfigPatch adapter.
- Shared patch/reset/no-op/preflight behavior in the application service.
- Minimal safe result DTO and stable domain-to-tool errors.
- Tool factory, description, local risk metadata, and preview formatter.
- Direct ToolExecutor and service integration tests.

## Non-goals

- No production tool registration.
- No natural-language routing cutover.
- No Terminal approval implementation.
- No AgentLoop multi-call, next-model-request, or production composition integration tests;
  those belong to Subplan 27.
- No Provider/credential/model/security configuration.
- No Handoff, file, Shell, Git, network, or persistent Session capability.

## Executable tasks

### CT.26.1 — Add strict configuration command arguments

- Define `UpdateConfigurationArguments` outside generic Runtime code, with:
  - scope: session/workspace/global;
  - target: preferences/profile;
  - operation: set/unset/append/remove/reset;
  - optional path/value with exact operation-dependent validation.
- Reject extra fields, invalid scope/target pairs, missing values, reset payloads, scalar/list
  mismatches, unknown paths, and forbidden sensitive targets before persistence.
- Keep workspace identity, revisions, paths, approval, and risk out of model arguments.
- Generate one valid standard ToolDefinition with no local metadata.
- Write the Provider-visible tool name and description in Chinese, consistent with the two
  existing tools. The description must explicitly distinguish persistence from one-turn
  style, questions/discussion, hypotheses, quotations, negation, ambiguity, and sensitive
  targets.

### CT.26.2 — Add one typed service command/result contract

- Define an application-owned `ConfigurationCommand` for exactly one operation and a minimal
  `ConfigurationChangeResult` containing status, scope, target, operation, path, and
  revision. Do not put local storage identity, approval, or Provider data into either model.
- `revision` is `null` for session Preferences. Applied workspace/global results carry the
  published document revision; unchanged workspace/global results carry the current revision
  without incrementing it (`0` for a missing workspace document).
- Keep Core `ConfigPatchOperation.op` unchanged (`set|unset|append|remove`). Do not add reset
  to `ConfigPatch`, `ConfigExtractionResult`, or the still-live structured extractor schema.
- Make `ConfigPatchService.apply(ConfigPatch)` a compatibility adapter that converts and
  validates the complete legacy operation list before mutation, invokes the same command
  engine, and preserves the current whole-patch/one-store-publication atomicity. It returns
  only minimal per-operation result data, never `GlobalConfig`, `ProfileDocument`, complete
  Preferences/Profile, providers, or credential references.

### CT.26.3 — Consolidate configuration behavior in ConfigPatchService

- Move Preferences/Profile reset behavior currently owned by CommandService into the shared
  configuration application service.
- Retain one ALLOWED_PATHS and scalar/list validation authority.
- Preserve reset representation exactly:
  - session/preferences replaces only the in-memory override with `Preferences()` and has no
    revision;
  - workspace/preferences calls `clear_preferences()` and publishes a version-2 tombstone;
  - global/preferences uses the aggregate update lock, replaces only Preferences with
    defaults, and preserves Provider fields;
  - workspace/profile calls `clear_profile()` and publishes a version-2 tombstone.
- Add deterministic no-op detection before persistence using the master-plan matrix:
  - equal scalar set -> unchanged;
  - already-empty optional unset -> unchanged;
  - normalized duplicate append -> unchanged;
  - zero-match remove -> unchanged (an intentional production behavior change);
  - multiple normalized remove matches -> failure;
  - already-default session/global or missing/cleared workspace reset -> unchanged.
- No unchanged operation may write a document/tombstone, rotate a backup, replace a Session
  projection, or increment a revision.
- Preserve global aggregate-document locking and the current narrow workspace degraded modes.
- Add a side-effect-free preflight operation used by approval preview. It must reject
  workspace read-only, workspace-Preferences read-only, missing Profile for non-reset
  operations, and unsafe/corrupt/future state before ApprovalPort. It cannot reserve a
  revision or prevent a post-approval conflict; the command path rechecks everything.

### CT.26.4 — Make Slash Commands share the consolidated service

- Change `/config edit`, `/config reset`, `/workspace edit`, and `/workspace reset` to call
  the same typed service command/reset methods that the tool will use.
- Preserve current deterministic preview/confirmation/exit behavior.
- Remove direct ProjectStateStore/global-store mutation logic from CommandService where the
  configuration service now owns it.
- Prove no model call or ToolCycle is synthesized for Slash Commands.
- Preserve existing Slash grammar and preview action names. Do not add append/remove/list
  editing syntax: `/config edit` and `/workspace edit` remain scalar/set oriented. Natural-
  language tooling and direct service calls provide list operations.
- During Subplan 26, the only permitted production semantic changes are the locked no-op
  results and lack of needless revision bumps. Preview line structure, confirmation prompts,
  action names, extraction shape, supported fields, tombstone representation, and error-
  blocking behavior must not drift.

### CT.26.5 — Implement the thin standard tool factory

- Add an application-owned configuration tool factory rather than placing domain behavior
  in `runtime/tools.py`.
- Inject ConfigPatchService through construction.
- Convert validated arguments to the shared service command, invoke it once, and map the
  typed result to minimal JSON.
- Map expected read-only, validation, revision, and state failures to stable bounded
  ToolExecutionError codes without leaking state values or tracebacks.
- Register local policy metadata:
  - session write -> approval required;
  - workspace/global write -> approval required.
- Provide a sanitized approval preview using the same deterministic preview formatter as
  Slash Commands.
- Have that preview callable invoke service preflight first. Expected read-only, missing-
  Profile, and unsafe-state failures become bounded tool errors without invoking
  ApprovalPort or the handler; preview/preflight performs no write.
- Ensure the description contains explicit persistence, one-turn, scope, discussion,
  hypothesis, quotation, negation, ambiguity, and sensitive-target rules.

### CT.26.6 — Prove direct service/tool behavior

- Test every allowed scope/target/field/operation combination and representative invalid
  combinations.
- Test approved, denied, unavailable, cancelled-before-handler, every locked no-op, read-only,
  missing Profile, corrupt/future state, revision-conflict, and injected write-failure paths.
- Prove one call invokes the service at most once and never retries.
- Prove preflight failures do not call ApprovalPort or write state; prove a revision race may
  still fail after approval without leaking state.
- Prove session results use `revision: null`, workspace/global revisions are monotonic, and
  unchanged results return the current revision.
- Prove the compatibility `apply(ConfigPatch)` route shares validation/write behavior while
  retaining the old extraction schema, whole-patch atomicity, and one-publication behavior,
  and never returns complete state.
- Keep direct tool tests to one ToolExecutor call at a time. Multi-call ordering, partial
  writes, and next-model context belong to CT.27.4/27.5.
- Prove complete state, credential refs, internal paths, raw arguments/results, and
  tracebacks do not reach events or tool results.

### CT.26.7 — Run integrated regression and close the vertical slice

- Run focused configuration/service/tool/state/command tests.
- Run source checks proving the handler is thin and Runtime contains no configuration import
  or name branch.
- Run compatibility tests for the still-live Gate/extractor route and current Slash terminal
  actions; do not require SessionOrchestrator to be domain-free yet.
- Run the full offline and quality gates.
- Keep the factory unregistered in production and activate Subplan 27 only on a green tree.

## Mandatory gates

```bash
uv run pytest -q tests/test_preferences_and_orchestration.py tests/test_tools.py tests/test_state_and_workspace.py tests/test_cli_commands.py
uv run pytest -m 'not live'
uv run ruff format --check .
uv run ruff check .
uv run python -m compileall -q src tests
uv run morrow --help
git diff --check
```

## Completion criteria

- One strict standard tool definition covers the current Preferences/Profile configuration
  surface.
- ConfigPatchService is the only configuration validation/reset/write authority.
- The typed command/result API is the primary service contract; legacy ConfigPatch is a
  temporary adapter and has no reset operation.
- Slash Commands use the same service and preserve their deterministic UI contract.
- Handler code is a thin injected-service adapter with minimal safe results.
- Approval policy is generic local metadata and required for every configuration write.
- Direct tests prove preflight, reset/tombstone, state degradation, revision/null revision,
  no-op, cancellation, and single-call ToolExecutor safety.
- The tool is not yet registered in production, so no dual natural-language route exists.
- Full offline and quality gates pass.

## Delivered result

An architecture-compliant, directly accepted configuration tool vertical slice ready for an
atomic production routing cutover.
