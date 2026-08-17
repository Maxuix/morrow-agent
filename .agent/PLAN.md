# Natural-Language Configuration Tooling Plan

> Status: complete; review remediated
> Active subplan: none
> Scope: first Stage 3 stateful-tool vertical slice; no file, Shell, Git, network, Session persistence, or memory work

## Objective

Replace the separate keyword/structured-completion configuration route with one ordinary
AgentLoop path in which the model expresses an explicit configuration intent by calling one
standard function tool. Preserve the architecture boundary that AgentLoop, ToolExecutor,
SessionOrchestrator, Provider adapters, and public events remain domain-agnostic.

The completed product flow is:

```text
non-Slash user input
  -> AgentLoop.run_task()
  -> model answers normally or emits update_configuration
  -> generic ToolExecutor validation / budget / risk / approval
  -> thin RegisteredTool handler
  -> injected ConfigPatchService
  -> Session, GlobalConfigStore, or ProjectStateStore
  -> one bounded ToolMessage
  -> same AgentLoop continues to a final answer
```

No keyword detector, second model router, structured configuration completion, configuration-
specific event, pending-action side channel, or tool-name branch may survive in the natural-
language path.

## Product decisions

1. **One natural-language state machine.** Every non-Slash input enters `AgentLoop.run_task()`.
   The model's standard tool selection is the only executable configuration-intent signal.
2. **One standard tool.** The model receives one `update_configuration` function tool. One
   call expresses one atomic operation; one model response may contain multiple calls, which
   the existing loop executes serially in original order. Each call is approved and committed
   independently; there is deliberately no cross-call transaction or rollback.
3. **No keyword authority.** `ConfigIntentGate`, its vocabularies, mixed-task rejection, and
   forbidden-word routing are removed. A future hint may be added only if it cannot affect
   routing, tool availability, authorization, or persistence.
4. **Current state model only.** The tool covers Preferences and workspace Profile. Handoff
   is removed and must not re-enter any domain, state, command, tool, context, or document.
5. **Sensitive configuration is absent, not filtered late.** Provider configuration,
   credentials, active model, workspace identity, permissions, and `AgentPolicy` are not in
   the tool schema or handler dispatch.
6. **Application Service owns behavior.** The handler is a thin adapter over the injected
   `ConfigPatchService`; Slash Commands and the tool share one typed command/result API for
   validation, reset, read-only, revision, no-op, and write behavior. The legacy
   `ConfigPatch` route remains a temporary adapter only until the atomic cutover.
7. **Generic risk policy.** Approval is local Registry/Executor metadata enforced through a
   generic ApprovalPort. The handler cannot read user input, prompt the terminal, emit an
   event, or know a Provider SDK.
8. **No AgentLoop lifecycle change.** Approval occurs inside generic tool execution. The
   public event set and the one-start/one-completion contract remain unchanged.
9. **No hidden fallback.** Adapters without function-tool support retain ordinary chat and
   explicit `/config` commands; they do not fall back to the removed structured route.
10. **No new dependency.** The plan uses existing Pydantic models, Registry/Executor,
    terminal stack, and state stores.

## Authority and precedence

1. The user decision for one AgentLoop and standardized configuration tooling owns product
   direction.
2. `docs/ARCHITECTURE.md`, especially its tool-capability boundary and state ownership,
   owns dependency direction and data authority.
3. This plan owns scope, ordering, temporary compatibility, and completion evidence for the
   configuration-tool slice.
4. The active subplan owns executable tasks for one bounded slice.
5. Current code and commands just run override stale plan wording; reconcile the plan before
   continuing if they conflict.

## Baseline

The implementation baseline is commit `cbc3d6d` (`refactor: remove handoff continuity
bridge`). At that baseline:

- Session-owned ConversationLog is process-local and is the only chat-history authority.
- ProjectStateStore owns only `profile.yaml` and `preferences.yaml`.
- global/workspace/session Preferences merge in that order.
- `ConfigPatchService` owns the current deterministic patch and state-write behavior.
- natural-language configuration still uses `ConfigIntentGate` plus structured completion.
- production registers only `lookup_record` and `calculate`, both without local side effects.
- Stage 3 implementation has started with its generic approval foundation. This plan is its
  first stateful-tool slice, not the Stage 3 local file/Shell MVP.

Subplan 25 is activated by the user's explicit instruction to execute the current plan.

## Locked external tool contract

The Provider-visible tool remains an ordinary OpenAI-compatible function definition:

```text
ToolDefinition
└── function
    ├── name: update_configuration
    ├── description: Chinese persistence-intent and scope rules
    └── parameters: JSON Schema for UpdateConfigurationArguments
```

The argument model is flat and strict:

```text
UpdateConfigurationArguments
├── scope: session | workspace | global
├── target: preferences | profile
├── operation: set | unset | append | remove | reset
├── path: str | None
└── value: JSON value | None
```

Combination rules:

- `reset` requires no `path` and no `value`;
- `unset` requires `path` and no `value`;
- `set`, `append`, and `remove` require both `path` and `value`;
- session/global scope permits only Preferences;
- Profile permits only workspace scope;
- scalar fields permit only set/unset; list fields permit only append/remove;
- Profile fields required by the domain model cannot be unset into an invalid value;
- unknown fields and extra arguments fail before the handler;
- `workspace_id`, revision, storage path, risk, and approval are trusted local context and
  never model arguments.

Allowed targets and fields remain the current `ALLOWED_PATHS` matrix:

| Scope | Target | Fields |
|---|---|---|
| session | preferences | language, response_detail, instructions |
| workspace | preferences | language, response_detail, instructions |
| global | preferences | language, response_detail, instructions |
| workspace | profile | name, summary, goals, tech_stack, constraints, conventions |

The success result is minimal and bounded:

```json
{
  "status": "applied",
  "scope": "workspace",
  "target": "preferences",
  "operation": "set",
  "path": "language",
  "revision": 4
}
```

`unchanged` is a successful result and performs no write. Results never include complete
Preferences, complete Profile, GlobalConfig, Provider data, credential references, internal
paths, raw exceptions, or tracebacks.

For `reset`, the result retains the same fixed shape with `path: null`.

`revision` is always `null` for process-local session Preferences. Workspace/global applied
results carry the new monotonic document revision; unchanged results carry the current
revision without incrementing it (`0` for a missing workspace document).

## Intent-recognition contract

There is no pre-Agent classifier. Recognition belongs to the main model's normal tool
selection and is guided by the tool description.

Required semantics:

- one-turn response requests do not persist;
- “this session” maps to session Preferences;
- “this project/workspace” maps to workspace Preferences or Profile;
- “all projects/global default” maps to global Preferences;
- questions, explanation, hypothesis, examples, quotations, and negation do not call the
  tool;
- missing scope, target, operation, or an operation-required path/value causes one bounded
  clarification instead of a guessed tool call;
- mixed work and configuration may share one public turn and ordinary ToolCycles;
- the model cannot claim success until it receives an `applied` or `unchanged` ToolMessage.

Model behavior is evaluated, not trusted as authorization. Schema validation, domain
validation, risk policy, approval, and state ownership remain deterministic.

## Generic local execution metadata

Risk and approval remain local and generic. They are not Provider protocol fields:

```text
RegisteredTool
├── definition
├── arguments_model
├── handler
├── execution_policy
│   ├── effect: none | session_write | persistent_write
│   └── approval: never | required
└── approval_preview (optional local callable)
```

Ownership is fixed: Core owns the generic `ToolEffect`, immutable approval request/decision
values, and async ApprovalPort; Runtime owns `ToolExecutionPolicy`, Registry metadata, and
generic enforcement; Interface owns the Terminal adapter; Application owns configuration
commands, service, previews, and tool factory. None of these objects enters Provider wire.

The generic ToolExecutor order is:

```text
registry lookup
  -> strict argument validation
  -> local policy evaluation
  -> side-effect-free preflight and sanitized approval preview when required
  -> ApprovalPort decision
  -> handler execution only after approval
  -> canonical bounded outcome
```

It must not branch on tool names or application domains. `effect` is copied into the local
approval request for display/audit context only; this slice branches only on `approval`, not
on `effect`. Missing ApprovalPort fails closed with
`approval_unavailable`; denial returns `approval_rejected`. Both become ordinary bounded
ToolMessages so the accepted ToolCycle closes and the model can recover.

The first implementation requires approval for both session and persistent configuration
writes. Relaxing session approval requires later evidence and a separate policy decision.

Approval waiting remains inside the existing tool/run deadline in this slice. Deadline
suspension, persistent approval queues, and resumable turns are explicitly out of scope.

## Locked runtime, approval, and state contracts

These contracts are implementation inputs, not choices deferred to the active subplan.

### ApprovalPort and composition

- `ToolApprovalRequest` contains only `call_id`, `effect`, and sanitized `preview` lines. It
  contains no raw arguments, result envelope, storage path, revision token, SDK object,
  credential, traceback, or arbitrary handler data.
- `ToolExecutor` accepts an optional `ApprovalPort` at construction. Tests inject scripted
  ports. The production CLI enters one async session runner, creates `Terminal` and one
  `PromptSession`, constructs a Terminal ApprovalPort over those objects, passes that port to
  `build_session_application()`, and passes the same Terminal/PromptSession to `run_repl()`.
  There is no late mutable policy binding.
- Approval Ctrl+C raises cancellation for the entire active turn. AgentLoop owns synthetic
  ToolCycle closure and no handler runs. Slash confirmation remains outside AgentLoop and
  keeps its current Ctrl+C-as-deny behavior.
- Approval EOF cancels only the active turn. Main-prompt EOF remains the `/exit` path; Slash
  confirmation EOF retains the current closed-input exit code.
- Tool/run timeout or external cancellation must cancel the in-flight `prompt_async` await.
  The adapter and executor never swallow `CancelledError`; a tool timeout becomes the
  existing ordinary timeout ToolMessage and cannot leave a prompt task behind.
- The existing `tool.status=running` event continues to mean that a call entered the
  executor, including preflight/approval wait. The user therefore sees the generic running
  line before approval preview. Preview is Terminal-only and never a public event or history
  record.
- The bundled `tool_timeout_seconds = 120` remains unchanged. Timeout while reading an
  approval is a supported recovery path that must be tested and documented, not treated as
  impossible. Changing bundled policy needs separate authorization.

### Preflight, reset, and no-op behavior

- The configuration preview callable invokes a side-effect-free service preflight before it
  reaches ApprovalPort. Workspace/Profile read-only state, workspace Preferences read-only
  state, missing Profile for non-reset operations, and unsafe/corrupt/future state fail
  before user confirmation and perform no write. A revision race may still fail after
  approval and is reported as an ordinary tool failure.
- Reset preserves the current storage representation exactly:

| Scope/target | Reset operation |
|---|---|
| session/preferences | replace in-memory value with `Preferences()`; revision is `null` |
| workspace/preferences | publish through `clear_preferences()` as a version-2 tombstone |
| global/preferences | under the aggregate lock, replace only Preferences with defaults and preserve providers |
| workspace/profile | publish through `clear_profile()` as a version-2 tombstone |

- No-op classification is fixed: equal scalar `set`, already-empty optional `unset`, and a
  normalized duplicate `append` return `unchanged`; `remove` with zero normalized matches
  intentionally changes the old behavior to `unchanged`; `remove` with multiple normalized
  matches remains a hard failure; reset of an already-default session/global value or a
  missing/cleared workspace document returns `unchanged`.
- Every unchanged case performs no store publication, backup change, Session projection
  replacement, or revision increment. Workspace/global return the current revision; session
  returns `null`.

### Multi-call and history behavior

- One Assistant response with N stateful calls produces N serial approvals and N independent
  atomic service operations. Earlier applied calls remain applied if a later call is denied,
  fails, times out, or is cancelled. No cross-call transaction or compensating rollback is
  introduced.
- When the turn continues, the final Assistant must report each applied/unchanged/rejected/
  failed ToolMessage rather than claim batch atomicity. Cancellation may end the turn without
  final Assistant text; already written state and closed ToolMessages remain authoritative.
- Natural-language configuration is an ordinary AgentLoop turn: it enters ConversationLog,
  marks the process-local Session dirty, affects `/new` and `/exit` confirmation, and consumes
  context budget. Slash configuration remains outside AgentLoop, does not write chat history,
  and does not mark the Session dirty.
- Standard Assistant tool-call argument JSON is retained in ConversationLog to preserve a
  legal ToolCycle; successful calls therefore retain validated configuration values such as
  language or instructions. Arguments are not copied to public events, approval requests,
  tool results, or YAML metadata, and sensitive configuration targets remain absent from the
  schema.

## Handler and service ownership

The configuration handler may only:

1. convert validated arguments to the application-owned typed configuration command;
2. call the injected ConfigPatchService;
3. map the service result to a minimal JSON-safe result;
4. translate expected domain failures to stable ToolExecutionError codes.

It may not directly depend on Terminal, YAML stores, Provider adapters, SDK objects, module-
level mutable state, or raw user text.

ConfigPatchService remains the authority for:

- scope/target/path and scalar/list operation rules;
- session/global/workspace dispatch;
- Profile and Preferences validation;
- workspace read-only and Preferences-only degraded modes;
- reset behavior shared with Slash Commands;
- no-op detection;
- optimistic revision checks and atomic store publication;
- current Session projection updates after successful writes.

Subplan 26 introduces an application-owned `ConfigurationCommand` and minimal
`ConfigurationChangeResult`. `UpdateConfigurationArguments` maps to that command directly.
The still-live structured extractor continues to produce the unchanged Core `ConfigPatch`
shape (which does not gain `reset`); `ConfigPatchService.apply()` becomes a compatibility
adapter into the same command engine, preserves whole-patch validation and one-publication
atomicity, and returns only minimal per-operation results. Full GlobalConfig,
ProfileDocument, or Preferences documents never cross from the service to a tool handler.

No mechanical new Core Port is required while one injected Application Service satisfies
the boundary. Introduce a port only if a second implementation or external boundary creates
a real need.

## Ordered execution strategy

| Order | Subplan | Status | Depends on | Integrated result |
|---|---|---|---|---|
| 25 | [Generic Tool Policy and Approval Foundation](subplans/25-generic-tool-policy-approval.md) | completed | `cbc3d6d` | Generic risk/approval and complete schema work; no state or demo outcome change |
| 26 | [Configuration Service and Standard Tool](subplans/26-configuration-service-tool.md) | completed | 25 | Directly tested tool factory delegates all behavior to the shared service; not yet production-registered |
| 27 | [Single-Chain Product Integration](subplans/27-configuration-single-chain-integration.md) | completed | 26 | Production atomically removes the old route and enables the standard tool |
| 28 | [Configuration Tooling Acceptance and Delivery](subplans/28-configuration-tooling-acceptance.md) | completed | 25–27 | Final product, package, safety, model-contract, and documentation evidence is green |

Only one subplan may be active at a time. A failed gate reopens the subplan that owns the
broken contract.

## Temporary compatibility between subplans

- Subplan 25 adds generic metadata/approval behavior but all existing demo tools remain
  `effect=none/approval=never`. Their execution outcomes, envelopes, events, and history stay
  unchanged. The canonical Provider-visible argument schema intentionally becomes the full
  Pydantic JSON Schema, so serialized ToolDefinition bytes, request-size estimates, and
  related fixtures are explicitly allowed and required to change.
- Subplan 26 adds and directly tests the configuration tool factory but does not register it
  in production. The old natural-language route remains the only production route until the
  atomic cutover. It may acquire the shared no-op semantics through the compatibility adapter,
  but preview structure/action names, confirmation behavior, extraction schema, reset
  tombstones, and supported fields do not change.
- Subplan 27 performs one integrated cutover: register the tool, remove Gate/extractor and
  structured configuration routing, and update composition/terminal wiring together. A
  committed intermediate tree must never expose two production natural-language routes.
- Subplan 28 adds no new behavior unless acceptance reveals a defect owned by an earlier
  subplan.

## Cross-cutting contracts

1. ConversationLog remains the only chat-history writer; every accepted configuration call
   receives exactly one ordered ToolMessage before terminal closure or continuation.
2. ContextBuilder remains synchronous and side-effect-free.
3. Provider adapters receive only standard ToolDefinition and message wire. Local risk,
   approval, services, revisions, and workspace identity never enter Provider payloads.
4. AgentLoop and ToolExecutor contain no configuration tool-name branch in Subplans 25–28.
   SessionOrchestrator retains its existing configuration route through Subplan 26 and must
   be domain-branch-free only after the Subplan 27 cutover. Generic policy branching may use
   approval metadata only.
5. Tool handlers never retry. Model/tool/run/context/result/Cycle budgets continue to apply.
6. Cancellation before approval or execution changes no state. Once the synchronous atomic
   ConfigPatchService write begins, it returns one definite success/failure result without
   an internal cancellation point.
7. Duplicate semantic operations follow the locked no-op matrix above; zero-match removal is
   the one deliberate change from the current hard failure.
8. Public events contain no complete arguments/results. Approval UI may show only the
   validated configuration preview required for informed consent.
9. Missing/corrupt/future Profile and Preferences preserve their current narrow degraded
   modes and byte-safety contracts.
10. Legacy Handoff files remain ignored and untouched.
11. Slash Commands retain deterministic behavior and share ConfigPatchService; they do not
    synthesize model calls or ToolCycles. Their existing `edit` syntax remains scalar/set
    oriented; this plan does not silently add Slash list operations. List changes are
    available through the natural-language tool and direct typed service API.
12. No file, Shell, Git, network, browser, MCP, Skill, persistent Session, summary, memory,
    background task, or sub-agent capability enters this plan.
13. After the cutover, generic `complete_structured` infrastructure and its unit tests remain
    available but have zero production callers; they are not evidence for configuration.

## Documentation policy

- Update the Stage 3 roadmap before implementation to name configuration tooling as the
  first stateful-tool vertical slice and generic approval foundation. Completing this plan
  does not complete Stage 3; file/search/edit/Shell still require a separate explicit
  authorization and plan.
- Keep `docs/ARCHITECTURE.md` as a current-state document: describe the new production tool
  only after the cutover is implemented and accepted.
- Mark old Stage 1/2 configuration-routing evidence as historical rather than rewriting it
  to claim the Gate never existed.
- Final README must explain natural-language configuration availability, approval,
  tool-capable Adapter requirements, and the `/config` deterministic fallback.

## Validation gates

Every implementation subplan must run its focused tests and finish with:

```bash
uv run pytest -m 'not live'
uv run ruff format --check .
uv run ruff check .
uv run python -m compileall -q src tests
uv run morrow --help
git diff --check
```

Subplan 28 additionally requires strict test collection, a fresh wheel build/install,
installed import/resource/CLI smoke, precise source/capability scans, and a real offline
`run_repl` scenario. Live intent evaluation is optional, requires an explicit compatible
credential and user request, and is never inferred from offline Scripted Provider tests.

## Principal risks and containment

| Risk | Containment |
|---|---|
| Model calls the tool for discussion, negation, or one-turn style | Narrow description corpus, mandatory approval, negative intent evaluation |
| Model misses an explicit persistence request | Positive/ambiguous intent corpus and description iteration; do not restore a second router |
| Approval implementation couples Runtime to Terminal | Core ApprovalPort plus injected terminal/test adapters; handler and executor know only the port |
| Config-specific logic leaks into ToolExecutor | Generic local metadata and source tests forbidding name branches |
| Two natural-language routes coexist during migration | Tool factory remains unregistered until the atomic Subplan 27 cutover |
| Tool returns full state or sensitive configuration | Minimal result DTO and secret/provider sentinel scans |
| Repeated tool calls create needless revisions | Service-owned no-op detection and idempotency tests |
| Approval or cancellation leaves an open ToolCycle | Generic rejection/cancellation envelopes and ConversationLog grammar tests |
| A later call fails after an earlier call wrote state | Deliberate per-call atomicity; no rollback; ordered ToolMessages and truthful final response |
| Approval wait reaches the current 120-second tool timeout | Cancel the prompt await, emit the ordinary timeout result, document/test recovery without changing bundled policy |
| Unsupported Adapter silently changes behavior | No hidden fallback; explicit `/config` remains available |
| Stateful tool work expands into all Stage 3 capabilities | Locked scope and capability scans after every subplan |

## Definition of done

The plan is complete only when:

- every non-Slash input follows the single ordinary AgentLoop path;
- a standard `update_configuration` ToolDefinition is the sole natural-language
  configuration action surface;
- Gate, keyword corpora, ConfigExtractionResult, configuration extractor wiring, and the
  structured configuration route are absent from production and package surfaces;
- Preferences/Profile scope, fields, operations, reset, degraded modes, revisions, no-op
  behavior, and writes have one Application Service authority shared with Slash Commands;
- per-call approval and partial-write semantics, session-null revision, tombstone-preserving
  reset, and the no-op matrix match the locked contracts;
- configuration handler dependencies and outputs satisfy the architecture tool boundary;
- generic policy/approval contains no tool-name or configuration-domain branch;
- AgentLoop, ToolCycle, context, Provider wire, event lifecycle, cancellation, budgets, and
  terminal segmentation have no regression;
- approval denial/unavailability, invalid arguments, read-only state, conflicts, execution
  failure, and cancellation all return bounded replayable results and preserve state;
- Provider/credential/model/security/AgentPolicy/workspace identity cannot be selected by
  the tool;
- unsupported Adapters expose no hidden second natural-language path;
- intent behavior is covered by positive, negative, ambiguous, mixed-task, and sensitive-
  target corpora with optional Live status reported truthfully;
- active documentation, acceptance evidence, package contents, PLAN/TODO/TRACKER/LOG, and
  the subplan index are reconciled;
- Stage 3 remains explicitly incomplete and no file/search/edit/Shell capability is claimed;
- all focused, offline, Ruff, compile, CLI, boundary, package, and diff gates pass.
