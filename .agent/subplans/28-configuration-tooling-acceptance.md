# Subplan 28 — Configuration Tooling Acceptance and Delivery

> Status: pending
> Depends on: Subplans 25–27

## Goal

Prove on the final integrated tree that natural-language configuration uses one standard
AgentLoop/tool path, satisfies the architecture tool boundary, preserves state and history
safety, and is documented and packaged honestly. Close the plan only with direct mandatory
evidence and truthful optional Live status.

## Executable tasks

### CT.28.1 — Build the requirement-to-evidence matrix

- Add a configuration-tooling acceptance artifact mapping every master-plan definition-of-
  done clause to exact tests, commands, source checks, product scenarios, and observed
  results.
- Separate deterministic infrastructure evidence from model-semantic evaluation.
- Record baseline commit, final tree identity, tool inventory, Adapter support, and optional
  Live status.
- Do not reuse historical Stage 1/2 configuration-routing claims as current evidence.

### CT.28.2 — Accept intent-policy behavior honestly

- Maintain explicit positive, negative, ambiguous, mixed-task, and sensitive-target corpora.
- Offline Scripted Provider tests must prove plumbing and required application behavior for
  each model choice, not claim the script discovered intent itself.
- Verify ToolDefinition description/schema contains the locked persistence and scope
  contract sent to the Provider, is written in Chinese like the existing descriptions, and
  preserves the complete strict JSON Schema.
- If explicitly authorized with a compatible credential, run an optional real-model corpus
  and record exact cases/results; otherwise record `not run` without treating absence as a
  failure or pass.
- Any real-model miss may improve description/examples but must not reintroduce a second
  classifier or keyword authority.

### CT.28.3 — Accept the real offline terminal product flow

- Exercise production bootstrap and `run_repl` with Scripted Provider and scripted terminal
  input in one integrated scenario containing:
  - ordinary discussion with no configuration call;
  - workspace Preferences update approved and applied;
  - Profile update denied and unchanged;
  - ambiguous request answered with clarification and no write;
  - multiple configuration calls in original order;
  - repeated no-op returning unchanged without revision increment;
  - zero-match remove returning unchanged and ambiguous multi-match remove failing;
  - session result revision `null` and monotonic/current workspace/global revisions;
  - reset preserving session defaults, workspace tombstones, global Provider fields, and
    already-default/missing/cleared no-op behavior;
  - an Assistant batch with an earlier applied call and a later rejected/failed call,
    proving deliberate partial persistence and truthful per-result reporting;
  - one rejected/failed tool result followed by model recovery;
  - cancellation during approval/tool activity followed by a healthy turn;
  - approval timeout cancelling the prompt await and recovering through an ordinary timeout
    ToolMessage with the bundled 120-second policy unchanged;
  - `/config` and `/workspace` deterministic command compatibility;
  - `/new`, dirty `/exit`, Ctrl+C, and EOF behavior.
- Prove natural-language configuration is logged/dirty while Slash configuration is not.
  Standard Assistant tool arguments are expected in ConversationLog; scan approval requests,
  terminal/events, ToolMessage results, and persisted state for forbidden raw arguments,
  complete state/results, internal paths, traceback, SDK, reasoning, secrets, and unrelated-
  project sentinels. Verify history contains only legal bounded ToolCycles.

### CT.28.4 — Accept architecture, state, and capability boundaries

- Prove AgentLoop, ToolExecutor, SessionOrchestrator, and Provider adapters contain no
  configuration tool-name/domain branch.
- Prove the handler depends only on the injected Application Service and returns minimal
  results.
- Prove Provider payloads contain standard ToolDefinition only; local effect/approval,
  ApprovalPort, revisions, and workspace identity are absent.
- Prove `complete_structured` has zero production callers after cutover, while its generic
  module/tests remain and are not counted as configuration evidence.
- Re-run Profile/Preferences missing/cleared/present, corrupt/future, revision, backup,
  atomic-write, failure-injection, and workspace-isolation tests.
- Prove legacy Handoff files remain ignored and unchanged.
- Prove no Provider/credential/model/security/AgentPolicy/workspace identity, file, Shell,
  Git, network, browser, MCP, Skill, persistent Session, summary, memory, background task,
  or sub-agent capability entered.

### CT.28.5 — Reconcile product and architecture documentation

- Update README with single-chain semantics, supported configuration surface, confirmation,
  per-call partial-write and approval-timeout recovery, tool-capable Adapter requirement,
  and `/config` fallback. Document that Slash edit remains scalar/set oriented and list
  append/remove is provided by the natural-language tool rather than a new Slash syntax.
- Update ARCHITECTURE current flow, tool inventory, state ownership, and approval boundary
  only after implementation evidence is green.
- Reconcile Stage 3 roadmap status without claiming unrelated file/Shell deliverables are
  complete. State explicitly that this plan delivers only the approval foundation and first
  stateful tool; Stage 3 file/search/edit/Shell MVP remains unimplemented and requires
  separate authorization.
- Mark historical Gate/structured-routing evidence as historical where needed.
- Reconcile PLAN, TODO, TRACKER, LOG, subplan index, and acceptance evidence.

### CT.28.6 — Run final package and quality gates

- Run the complete offline suite and strict collection; record observed counts rather than
  inferring them from previous baselines.
- Run Ruff format/lint, compileall, CLI help, precise source scans, Markdown/reference audit,
  capability/product tests, and `git diff --check`.
- Build a fresh wheel, inspect its inventory, install it into a fresh environment, import
  Morrow, discover/load bundled policy, and run installed `morrow --help`.
- Prove the installed package contains the configuration tool and no removed Gate/Handoff
  production module or hidden alternate route.
- Mark the plan complete only when every mandatory branch is green.

## Mandatory final gates

```bash
uv run pytest -m 'not live' -q
uv run pytest --collect-only -q
uv run ruff format --check .
uv run ruff check .
uv run python -m compileall -q src tests
uv run morrow --help
git diff --check
```

Package acceptance uses a newly built wheel and fresh environment. Optional Live intent
evaluation remains opt-in and is reported separately.

## Completion criteria

- Every master-plan requirement maps to direct green evidence.
- One natural-language AgentLoop/tool path is present; the old route is absent from source
  and package.
- Configuration scope, authorization, service ownership, state safety, result minimization,
  and ToolCycle behavior are accepted end to end.
- Locked composition/cancellation, tombstone reset, no-op, revision, partial-write, dirty-
  history, and timeout semantics are accepted end to end.
- Intent corpora and optional Live status are reported without overstating Scripted Provider
  evidence.
- No mandatory test, product, state, architecture, source, package, documentation, or
  capability gate is missing, unexpectedly skipped, or inferred.
- Active execution-state documents are reconciled and the plan is marked complete.
- Stage 3 remains honestly incomplete; no file/search/edit/Shell completion claim is made.

## Delivered result

A fully accepted, architecture-conformant natural-language configuration capability using
one ordinary AgentLoop and one standardized stateful tool.
