# Subplan 29 — Stage 3B.1 Policy and Workspace Foundation

> Status: pending; not active
> Depends on: completed Subplan 28, explicit user authorization, and Gate P0 passed

## Goal

Replace static per-tool approval as the sole authorization decision with a generic,
argument-aware capability policy; freeze a workspace capability and permission preset for
one process-local Session; and create the generic per-run fact path needed by later file,
process, and Git tools. Do not register any project filesystem/process/Git tool yet.

## Scope and expected ownership

Primary files/modules:

- `src/morrow/core/capabilities.py` for immutable local-only policy/fact values;
- `src/morrow/runtime/capabilities.py` for deterministic policy evaluation;
- `src/morrow/runtime/tools.py` for intent/preflight/verdict enforcement;
- `src/morrow/runtime/agent.py` and `runtime/session.py` for generic process-local ToolRunContext/facts;
- `src/morrow/application/context.py` for the capability-derived system boundary;
- `src/morrow/bootstrap.py` and `interfaces/cli.py` for frozen preset composition;
- existing tool/configuration factories only as required for compatibility migration;
- focused policy/tool/AgentLoop/bootstrap/boundary tests.

Do not add `files.py`, `process.py`, sandbox, Git, new Provider tool definitions, network,
persistence, or Full Access activation in this subplan.

## Executable tasks

### S3.29.1 — Capture the exact baseline and compatibility matrix

- Record current `HEAD`, production tool inventory, ToolExecutor ordering, public event schema,
  ApprovalPort request/preview limits, static SYSTEM_BOUNDARY text, bundled policy values,
  16,000/56,000 result/cycle limits, and full offline baseline.
- Compare Pi's fixed-commit tool factory/operations boundaries and faux-provider harness;
  record borrowed, strengthened, and rejected behavior for the final evidence matrix.
- Add/adjust tests before refactoring so current demo/configuration behavior is explicit.
- Preserve one-start/one-completion, legal ToolCycle closure, serial multi-call execution,
  approval timeout/cancel recovery, output budgeting, and loop detection.

### S3.29.2 — Add local capability models

- Add strict frozen enums/models for `AccessScope`, `ApprovalMode`, `ProcessIsolation`,
  `PermissionProfile`, `WorkspaceCapability`, `OperationKind`, `RiskFlag`,
  `OperationIntent`, `PolicyVerdict`, `PolicyDecision`, `ToolRunContext`, per-call
  `ToolCallContext`, `ToolHandlerOutcome`, and the master-plan strict ToolFact tagged union,
  plus the injected `SensitiveResourcePolicy` contract used by later content-producing services.
- Keep these values local; assert none appear in Provider `ToolDefinition` or messages.
- Represent `full_access` but mark it unsupported in the Stage 3 capability set.
- Keep `ToolEffect` as audit/display metadata, not authorization.

### S3.29.3 — Implement the current deterministic policy slice

- Implement Manual/Auto Safe decisions for existing tools and the generic read/write/process/
  forbidden operation categories needed by Subplans 30–32.
- Represent Auto Sandboxed but return `unsupported_capability` for every process intent until
  Subplan 33 installs and proves a backend; do not pre-authorize its future process row here.
- Preserve `update_configuration` as always requiring approval.
- Return deny before preview/ApprovalPort for unsupported or forbidden capability.
- Include stable reason codes and sanitized preview summaries.
- Treat destructive/external/network/Git-write operations as denied in Stage 3.
- Treat all Host project processes as approval-required; never infer safety from command name.
- Keep Full Access, destructive real-workspace operations, external effects, and network denied.

### S3.29.4 — Evolve RegisteredTool and ToolExecutor generically

- Add an optional intent resolver/preflight contract receiving validated arguments and the
  generic ToolCallContext.
- Enforce: validate → resolve intent → evaluate → deny/approve → handler → bound result/facts.
- Pass the exact per-call cycle-derived result limit to a semantic result builder; require
  complete typed envelopes or `output_budget`, never a raw JSON prefix for Stage 3 handlers.
- Preserve a compatibility path while migrating `lookup_record`, `calculate`, and
  `update_configuration`; remove any obsolete static-decision branch only after all tests use
  the new generic path.
- Expand local approval requests only with sanitized policy/effect/reason fields; do not add
  raw arguments or change public tool events.
- Add per-tool local preview-budget metadata so later mutation tools can request 40-line/4-KiB
  Diff previews without branching by tool name or enlarging existing Host/config previews.
- Add typed ordinary errors for denied/unsupported capability and preflight failure.

### S3.29.5 — Add process-local run facts

- Create one ToolRunContext per `AgentLoop.run_task()` and pass it to ToolExecutor without
  domain-specific branching.
- Allow a handler outcome to carry an ordinary Provider payload plus local ToolFacts.
- Validate the bounded common/variant ToolFact fields from the master plan and keep facts out
  of the Provider success envelope unless a tool deliberately returns a sanitized projection.
- Accumulate facts in original call order and retain at most the latest completed run on
  Session for terminal/application inspection.
- Preserve ConversationLog as the only chat-history writer; facts are not chat messages.
- Do not persist facts or change public events; Stage 4 owns durable AgentRun/Artifact state.

### S3.29.6 — Freeze workspace capability, CLI preset, and prompt boundary

- Derive workspace root only from confirmed `WorkspaceIdentity.path` at composition time.
- Add an explicit workspace-startup CLI option for `manual`, `auto-safe`, and reserved
  `auto-sandboxed`; default to `manual`.
- Prevent model/tool/configuration inputs from changing the selected preset.
- Replace the categorical Stage 2 SYSTEM_BOUNDARY with the generic capability-derived renderer;
  snapshot the current three-tool prompt and tool-free Adapter behavior.
- Add a direct regression proving `UpdateConfigurationArguments`, ConfigPatch, Profile, and
  Preferences have no permission/mode field and natural-language configuration cannot change
  `manual` to `auto`.
- Intersect a degraded/read-only Session with a read-only capability.
- Until Subplan 33, selecting `auto-sandboxed` returns a controlled unsupported error before
  starting the interactive Agent; never substitute Host.
- Expose no Full Access CLI switch in Stage 3.

### S3.29.7 — Reconcile tests without opening future capability

- Add policy truth-table, frozen-profile, Provider-leak, approval ordering, denial-before-
  preview, unsupported-mode, read-only intersection, complete result-envelope, multi-call
  budget, prompt/tool-inventory alignment, facts isolation, and cancellation tests.
- Keep the production tool inventory exactly the existing three tools.
- Keep Stage 3 file/process/Git tool names forbidden until the subplan that registers them.
- Run focused and complete offline/quality gates.

## Completion criteria

- One immutable permission profile and workspace capability are frozen in production composition.
- CapabilityPolicy returns deterministic allow/approval/deny decisions from validated intent.
- Denied/unsupported operations never call preview, ApprovalPort, or handler.
- Existing configuration approval and all AgentLoop/ToolCycle semantics remain green.
- ToolRunContext/facts exist without public-event or persistent-state changes.
- Every production handler uses the new outcome path and no static production approval branch remains.
- The current system boundary is truthful for the current ToolSet and ready for later inventory cutovers.
- SensitiveResourcePolicy is local, frozen, independently tested, and absent from Provider wire.
- Full Access and Auto Sandboxed execution remain unavailable and fail closed.
- Production still exposes only `lookup_record`, `calculate`, and `update_configuration`.
- Focused tests, full offline suite, Ruff format/check, compileall, CLI help, and
  `git diff --check` pass.

## Delivered result

A domain-agnostic, argument-aware capability foundation ready for read-only workspace tools,
with no project capability activated prematurely.
