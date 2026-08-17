# Subplan 27 — Single-Chain Product Integration

> Status: completed
> Depends on: Subplan 26

## Goal

Atomically replace the keyword/structured natural-language configuration route with the
standard `update_configuration` tool in production composition, while preserving explicit
Slash Commands and the ordinary AgentLoop/ToolCycle/event lifecycle.

After this subplan, every non-Slash input follows one AgentLoop path. There is no committed
production state in which both natural-language routes are reachable.

## Scope

- Terminal implementation of the generic ApprovalPort.
- Composition-root injection and capability-aware tool registration.
- Removal of Gate/extractor/structured configuration production code.
- Tool description and ambiguous-intent behavior.
- Unsupported-Adapter behavior and explicit `/config` fallback.
- Integrated REPL, AgentLoop, approval, state, and cancellation tests.

## Non-goals

- No new public event type or turn state.
- No tool-name branch in AgentLoop, ToolExecutor, or Orchestrator.
- No automatic approval for session writes.
- No file/Shell/Git/network tools or persistent approval queue.
- No Live run without explicit credential and user request.

## Executable tasks

### CT.27.1 — Implement the terminal ApprovalPort adapter

- Let the terminal interface render the validated preview and collect explicit yes/no input.
- Add one async CLI session runner that creates `Terminal` and one `PromptSession` before
  session composition, constructs the Terminal ApprovalPort from them, passes the port to
  `build_session_application()`, then passes the same Terminal/PromptSession to `run_repl()`.
  Tests inject a scripted ApprovalPort directly; there is no late mutable binding.
- Inject the adapter through composition rather than importing Terminal/prompt-toolkit into
  Core, Runtime, or the handler.
- Lock interaction behavior:
  - deny returns `approval_rejected` and lets the ordinary ToolCycle/model continuation run;
  - approval Ctrl+C raises cancellation of the whole turn and uses AgentLoop synthetic
    closure; it is not a deny;
  - approval EOF cancels only the current turn, while main-prompt EOF remains `/exit` and
    Slash confirmation EOF retains its current exit behavior;
  - `CancelledError` and tool/run timeout cancel the in-flight `prompt_async` await, leave no
    prompt task behind, and timeout becomes the existing ordinary timeout ToolMessage;
  - an unavailable adapter fails closed with `approval_unavailable`.
- Keep approval text out of AgentEvent payloads and ConversationLog.
- Ensure handler execution begins only after the generic ToolExecutor receives approval.
- Accept the existing event order: Terminal renders `tool.status=running` before the preview;
  running includes preflight/approval wait and preview remains Terminal-only.
- Keep bundled `tool_timeout_seconds=120` unchanged and test timeout recovery without wall-
  clock sleep.

### CT.27.2 — Register the configuration tool through normal composition

- Construct Session and ConfigPatchService before freezing the ToolSet.
- Register `lookup_record`, `calculate`, and `update_configuration` through the same Registry
  API when the Adapter declares OpenAI function-tool support.
- Freeze definitions, handlers, and local policy metadata together for the task.
- Pass ApprovalPort only to the generic ToolExecutor.
- Keep unsupported Adapters tool-free; do not add a structured configuration fallback.
- Update the exact production inventory assertions in `tests/test_stage_boundary.py` and
  Provider tool-set assertions in `tests/test_stage2_e2e.py` to include
  `update_configuration` for a tool-capable Adapter. Preserve the exact two-tool assertion
  for the isolated demo registry and preserve `FORBIDDEN_TOOL_KEYWORDS` checks rather than
  weakening them.

### CT.27.3 — Remove the old natural-language configuration route

- Delete ConfigIntentGate, GateDecision, all keyword lists, and their production tests.
- Remove `config_extractor` and `config_patch_service` routing dependencies from
  SessionOrchestrator.
- Remove bootstrap structured configuration completion and its fallback question.
- Remove ConfigExtractionResult/extraction helpers if no non-configuration owner remains.
- Retain generic structured-completion infrastructure and tests for legitimate remaining
  uses; do not delete it merely because configuration no longer uses it. Record that it has
  zero production callers after cutover and never use it as configuration acceptance
  evidence.
- Prove every non-Slash input calls AgentRuntime exactly once and never calls a pre-router.
- Run the Orchestrator-specific configuration name/domain source scan here, not in Subplan 25.

### CT.27.4 — Integrate intent and mixed-task behavior in the ordinary loop

- Exercise positive persistence, ordinary discussion, one-turn style, negation, quotation,
  hypothesis, ambiguous scope, and mixed task/config scenarios with Scripted Provider
  sequences.
- For ambiguity, require a normal Assistant clarification with no tool call or state write.
- Permit ordinary work and one or more configuration calls in the same public turn.
- Prove one Assistant with multiple configuration calls executes serially with one approval
  per call. There is deliberately no batch transaction: if an earlier call is applied and a
  later call is rejected or fails, the earlier state remains applied and both ToolMessages
  are authoritative.
- Require the final Assistant, when the turn continues, to describe each `applied`,
  `unchanged`, rejected, or failed status consistently with its ToolMessage and never claim
  batch atomicity.
- Prove every accepted applied result refreshes the next model request's
  Session/Profile/Preferences system-state projection.
- Add one generic SYSTEM_BOUNDARY rule: the model may not claim an action or state change
  occurred until a tool result reports success/unchanged. Do not name the configuration tool
  or add a tool-specific system branch.
- Do not claim Scripted Provider cases prove a real model's semantic accuracy.

### CT.27.5 — Preserve cancellation, budgets, and legal history

- Cover cancellation while approval is pending, after denial, before handler execution,
  after one call in a multi-call batch, after one applied call followed by later rejection/
  failure, and after an applied result before final text.
- Prove unresolved calls receive existing synthetic closure where appropriate and a healthy
  next user turn succeeds.
- Prove approval time remains under existing tool/run deadlines and timeouts close cleanly.
- Re-run context trimming and complete ToolCycle pairing with configuration results.
- Prove every natural-language configuration request is an ordinary logged dirty Session
  turn affecting `/new`, `/exit`, and context budget. Prove Slash configuration remains
  outside ConversationLog and does not set dirty.
- Prove legal Assistant tool-call argument JSON remains in ConversationLog while approval
  requests, public events, tool results, and state metadata omit raw arguments.
- Preserve exactly one turn.started/turn.completed and current public event schemas.

### CT.27.6 — Run the integrated cutover gate

- Run focused orchestration/bootstrap/terminal/configuration/AgentLoop tests.
- Run negative source scans proving the old Gate/extractor and configuration-specific
  branches are absent.
- Run the complete offline and quality gates.
- Update TRACKER/LOG and activate Subplan 28 only after the single-chain product tree is
  fully green.

## Mandatory gates

```bash
uv run pytest -q tests/test_preferences_and_orchestration.py tests/test_terminal.py tests/test_agent_tool_loop.py tests/test_stage2_e2e.py tests/test_stage_boundary.py tests/test_context_projections.py
uv run pytest -m 'not live'
uv run ruff format --check .
uv run ruff check .
uv run python -m compileall -q src tests
uv run morrow --help
git diff --check
```

## Completion criteria

- Production has exactly one non-Slash AgentLoop path.
- `update_configuration` is registered only through the normal capability-aware Registry.
- Gate, keywords, extraction model, and configuration pre-routing are absent.
- Terminal owns confirmation through ApprovalPort without Runtime/interface coupling.
- CLI composition creates the shared Terminal/PromptSession before ToolExecutor construction;
  Ctrl+C, EOF, timeout, deny, and unavailable behavior match the locked contracts.
- AgentLoop, ToolExecutor, Orchestrator, Provider wire, and public events have no
  configuration-name branch or lifecycle special case.
- Unsupported Adapters have no hidden fallback and `/config` remains functional.
- Approval, denial, EOF, cancellation, timeout, mixed calls, and follow-up turns preserve
  legal history and state safety.
- Per-call partial writes are explicit, replayable, and reported without rollback claims;
  natural-language configuration is dirty/logged while Slash configuration is not.
- Full offline and quality gates pass.

## Delivered result

The production single-chain configuration experience: model intent recognition, standard
tool call, generic authorization, shared application service, ordinary ToolMessage, and
continued AgentLoop reasoning.
