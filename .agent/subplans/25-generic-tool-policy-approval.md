# Subplan 25 — Generic Tool Policy and Approval Foundation

> Status: completed
> Depends on: commit `cbc3d6d`
> Activation: requires explicit user authorization to start the Stage 3 stateful-tool slice

## Goal

Extend the local Registry/Executor boundary with generic side-effect and approval metadata,
plus an injected ApprovalPort, without changing current demo-tool execution outcomes,
AgentLoop state transitions, public events, history envelopes, or application state. The
standard Provider-visible parameter schema is deliberately corrected to preserve full
Pydantic JSON Schema; byte-for-byte ToolDefinition/request-size compatibility is not a goal.

At completion, `lookup_record` and `calculate` remain the only production tools and remain
approval-free. The new generic infrastructure is proven independently and is ready for the
configuration vertical slice.

## Scope

- Generic local execution metadata on RegisteredTool.
- Generic approval request/decision port and test doubles.
- Fail-closed approval enforcement in ToolExecutor.
- Stable bounded approval error outcomes.
- Complete JSON Schema preservation for future strict argument models.
- Stage 3 roadmap and current architecture wording reconciliation for this first stateful-
  tool slice.

## Non-goals

- No configuration tool factory or production registration.
- No Terminal approval UI yet.
- No AgentLoop, SessionOrchestrator, ContextBuilder, or public event changes.
- No file, Shell, Git, network, or other Stage 3 tool.
- No deadline suspension, persistent approval queue, or resumable turn.
- No Terminal adapter or production approval prompt; Subplan 27 owns that composition.

## Executable tasks

### CT.25.1 — Lock generic contracts and add red boundary tests

- Update `docs/roadmap/stage-3-local-tools-and-safety.md` to identify generic risk/approval
  infrastructure and configuration tooling as the first stateful-tool vertical slice,
  without claiming the local file/search/edit/Shell MVP or Stage 3 is complete. State that
  those capabilities require another explicit authorization.
- Reconcile `docs/ARCHITECTURE.md` with the implemented end-of-subplan reality: generic Tool
  Policy/Approval exists, but the configuration tool and all file/Shell tools do not.
- Add focused tests proving:
  - local execution metadata is absent from serialized ToolDefinition;
  - ToolExecutor and Registry do not branch on concrete tool names;
  - current demo tools execute without an ApprovalPort and preserve exact outcomes, events,
    and history envelopes;
  - a required-approval fake tool cannot run when approval is unavailable or denied;
  - approval rejection still produces one bounded outcome for ConversationLog closure.
- Do not change the architecture's production tool inventory or claim configuration support;
  only describe generic policy that is actually green by the end of this subplan.

### CT.25.2 — Add immutable local tool execution metadata

- Define strict local enums/models for:
  - effect: `none`, `session_write`, `persistent_write`;
  - approval: `never`, `required`.
- Keep `ToolEffect`, immutable approval request/decision values, and ApprovalPort in Core;
  keep `ToolExecutionPolicy`, RegisteredTool metadata, and enforcement in Runtime. This
  dependency direction must not make Core import Runtime or any Interface.
- Extend `RegisteredTool` with immutable execution policy and an optional sanitized approval-
  preview callable.
- Preserve `make_tool()` compatibility by defaulting existing tools to `none/never`.
- Use `effect` only as local approval-request display/audit context. ToolExecutor must decide
  whether to ask solely from `approval`; it must not branch on effect values in this slice.
- Freeze metadata with the task ToolSet so later Registry mutations cannot alter a running
  task's authorization semantics.
- Prove Provider serializers receive only the existing standard ToolDefinition.

### CT.25.3 — Define ApprovalPort and deterministic adapters

- Add a narrow Core port that accepts a local `ToolApprovalRequest` and asynchronously
  returns approve/deny. The request fields are exactly `call_id`, `effect`, and a tuple of
  sanitized preview lines.
- Keep requests free of raw Provider fragments, SDK objects, credentials, tracebacks, full
  tool envelopes, raw arguments/results, revisions, workspace paths, or arbitrary handler
  data.
- Add test adapters for scripted approve, scripted deny, cancellation, and unavailable
  approval.
- Make `ToolExecutor` accept its optional port at construction. There is no mutable late
  binding of a frozen task's policy; the production Terminal adapter/composition arrives in
  Subplan 27.
- Do not import prompt-toolkit, Rich, or Terminal into Runtime/Core.

### CT.25.4 — Enforce policy generically in ToolExecutor

- After strict argument validation and before handler execution:
  - resolve immutable local execution policy;
  - build a sanitized preview through the registered local previewer when required;
  - fail closed when no ApprovalPort is available;
  - invoke the handler only after approval.
- Add stable generic error codes for `approval_rejected` and `approval_unavailable`.
- Ensure preview generation failure is bounded and prevents handler execution.
- Preserve cancellation propagation so AgentLoop's existing synthetic closure remains the
  only cancellation owner.
- Prove an in-flight approval await is cancellable and that `CancelledError` is never
  converted to denial or an execution failure. AgentLoop's existing `wait_for` continues to
  own tool timeout conversion to an ordinary timeout ToolMessage.
- Preserve the meaning of the already-emitted `tool.status=running`: the call entered the
  executor and may be awaiting preflight/approval. Preview remains outside events/history.
- Preserve existing timeout, result-size, validation-detail, no-retry, and error-sanitizing
  behavior.
- Do not add any tool-name comparison or application-domain import.

### CT.25.5 — Preserve full standard argument Schema

- Change the generic Pydantic-to-tool schema path only as needed to preserve complete valid
  JSON Schema, including `additionalProperties`, nested definitions, enum constraints, and
  required fields.
- Explicitly update Adapter/request-size snapshots and tests for the new canonical serialized
  schema. This Provider-wire change is authorized; execution results, events, ToolMessages,
  and history are not allowed to change.
- Prove the change is Provider-generic and does not leak local metadata.

### CT.25.6 — Run integrated regression and close the foundation

- Run focused Registry/Executor/Provider/AgentLoop/ConversationLog tests.
- Run source checks forbidding tool-name/domain branches in ToolRegistry, ToolExecutor,
  AgentLoop, and Provider serializers. Do not scan SessionOrchestrator for configuration
  domain branches yet: its old route intentionally remains until Subplan 27.
- Run the full offline and quality gates.
- Record observed results in LOG and activate Subplan 26 only after every mandatory check is
  green.

## Mandatory gates

```bash
uv run pytest -q tests/test_tools.py tests/test_agent_tool_loop.py tests/test_agent_limits.py tests/test_core_contracts.py tests/test_provider.py
uv run pytest -m 'not live'
uv run ruff format --check .
uv run ruff check .
uv run python -m compileall -q src tests
uv run morrow --help
git diff --check
```

## Completion criteria

- Generic risk and approval metadata is immutable, local, and Provider-invisible.
- Existing demo-tool outcomes, events, ToolMessages, history, and ordinary AgentLoop behavior
  are unchanged; their standard parameters schema is intentionally more complete.
- Required-approval handlers never run without an explicit approval decision.
- Rejection/unavailability is bounded and ToolCycle-compatible.
- Approval cancellation remains owned by the existing AgentLoop cancellation path.
- ToolRegistry, ToolExecutor, AgentLoop, and Provider serializers contain no concrete tool-
  name or configuration-domain branch.
- No Terminal/application infrastructure leaks into Core or Runtime.
- No production stateful tool is registered.
- Full offline and quality gates pass.

## Delivered result

A reusable architecture-compliant Tool Policy/Approval foundation that future stateful tools
can adopt through registration metadata alone. It starts the Stage 3 foundation but does not
deliver or complete the Stage 3 local-tools MVP.
