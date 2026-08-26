# Stage 7 Preflight Reliability Repairs — S7P-05 Validation and Completion Truth

> Status: active
> Active subplan: 84 — validation facts, outcome contracts and completion gate
> Branch: `codex/feat/s7p-05-validation-completion-truth`
> Base: verified local `main@ffa9770`
> Source authority: the user-requested S7P-05 checklist, current code and deterministic probes

## 1. Objective

Make Direct-agent completion an evidence-backed runtime decision. Separate ordinary command success
from validation, freeze a lightweight task outcome contract before durable admission, and gate a
model's final `stop` on net workspace changes, required validation, path boundaries, unresolved
tools and an optional authoritative verifier.

S7P-05 does not judge business semantics, add an autonomous reviewer, change permissions or tool
effects, alter runtime-policy defaults, add dependencies, implement S7P-06 context compression, or
run live Provider/network/credential tests.

## 2. Located defects and reproductions

1. `ToolRunContext.metrics()` derives `validation_outcome` from every `CommandToolFact`: any
   `status='exited'` command with exit code zero becomes `passed`; any arbitrary non-zero command
   becomes `failed`. `CommandToolFact` contains no validator identity or validation scope.
2. `ProcessExecutionService` collapses pytest, Ruff, mypy, uv, npm and make into the broad
   `project_command` class, while `ls`, `pwd` and `echo` are `opaque`. It discards argv after
   execution and therefore cannot later distinguish a real verifier from a successful utility.
3. A deterministic current-main probe records an `opaque` `ls`-equivalent fact with exit code zero
   and receives `validation_outcome='passed'`.
4. `AgentLoop` commits any non-empty tool-free `ModelFinishReason.STOP` immediately, freezes
   permissions, appends the assistant message and closes the turn. A scripted task saying
   `Please edit src/example.py` completed with ordinary `stop`, user+assistant history and zero
   `ChangeToolFact` values.
5. There is no Outcome Contract, workspace baseline, completion checker, verifier port or
   correction feedback path. `PreparedAgentRunSpec`/`AgentRunSnapshot` freeze prompt/tool/runtime
   evidence only.
6. Tool/model round limits have distinct existing stop codes, but missing change, failed required
   validation, unexpected path and unresolved tool states do not. The terminal summary displays a
   command-derived validation claim and cannot distinguish executed checks from inferred completion.
7. The mini-eval owns external verifier and unexpected-path truth only in the test harness; the
   production loop does not consume an equivalent authority.

Activation reproduction:

```text
opaque_exit_0_validation= passed
no_tool_finish_reason= stop
assistant_committed= ['user', 'assistant']
change_fact_count= 0
```

## 3. Frozen design decisions

### Validation facts are not command facts

- Keep `CommandToolFact` as bounded execution truth only. Add a separate tagged `ValidationFact`
  with validator kind, normalized workspace-relative scope, terminal status, exit code and a fixed,
  bounded evidence summary. It must not contain command text, argv, stdout/stderr, source content,
  secrets, reasoning or tracebacks.
- Classify at process preflight, while safe argv structure is available. Recognize direct and
  wrapper forms for pytest, `python -m pytest`, `ruff check`, `ruff format --check`,
  `python -m compileall` and the repository's declared static/build checks. `uv run` may wrap a
  recognized validator. A shell form contributes validation only when it parses as one simple
  command with no control, redirection, substitution or environment syntax; ambiguous shell,
  generic `project_command`, `ls`, `pwd`, `echo` and `cat` remain command facts only.
- Normalize scope from cwd plus declared path operands and fail closed to no validation fact when
  option parsing or confinement is ambiguous. An explicit validation declaration comes from the
  trusted Outcome Contract/application caller and is satisfied only by a compatible recognized
  fact; the model cannot self-declare `echo` as a validator.
- Aggregate the latest fact for each `(validator, scope)` key. A later successful rerun may replace
  an earlier failure for that same key; unrelated command failures neither create nor poison
  validation. Timeout/cancel/failure remain distinct. `validation_outcome='passed'` requires at
  least one actual passing `ValidationFact` and no latest failed validation key.

### Outcome Contract and baseline evidence

- Add strict bounded models for `OutcomeContract`, `ValidationRequirement` and baseline evidence.
  The contract records mode (`change`, `explanation`, or conservative `unspecified`), whether a
  net write is required, target paths, optional exclusive allowed paths, forbidden paths, required
  validations, optional verifier id, whether no-change is allowed, and its preparation version.
- Support an explicit trusted contract at the application/runtime boundary. Otherwise compile a
  minimal contract deterministically from the user task: explicit edit/fix/create/delete/rename
  language creates a change contract; explicit explain/review/analyse-only language permits no
  write; quoted/backticked workspace paths and explicit test commands become path/validation
  declarations. Ambiguity stays `unspecified` and is reported, not guessed into business truth.
- Prepare the contract and a read-only workspace baseline before the durable user/AgentRun
  transaction. In Git workspaces, freeze HEAD/repository state plus bounded hashes for pre-existing
  dirty/untracked paths so user-owned dirt is not attributed to the run. Use a bounded, no-follow
  manifest fallback outside Git. Protected metadata, `.git`, environments and caches are excluded;
  truncation/inability is an explicit inconclusive fact, never a clean baseline.
- Freeze only value-safe contract/baseline evidence or its verified bounded references in
  `PreparedAgentRunSpec` and `AgentRunSnapshot`, so recovery reuses the original contract rather
  than reparsing an empty resume input or accepting post-start workspace state.

### Completion gate and one correction

- Add a runtime-owned `CompletionChecker` that compares final workspace evidence with the frozen
  baseline and returns a bounded `CompletionCheckResult`: changed/target/unexpected/forbidden path
  facts, unresolved call count, required-validation statuses, known failures, verifier status,
  completion basis and reason/next-action codes. It never claims business correctness.
- A change contract needs a relevant net diff; a successful write tool whose effect was later
  reverted does not count. Explicit allowed/forbidden path policy is checked against run-attributed
  net paths while pre-existing unchanged dirt remains neutral. Baseline truncation or drift that
  prevents attribution is inconclusive and blocks normal success for a required-change contract.
- Before accepting final `stop`, check `ConversationLog.unresolved_call_ids`, workspace outcome,
  required validations and latest known validation failures. If configured, the injected verifier
  is authoritative: failure/inconclusive blocks success. Without one, record
  `completion_basis='runtime_evidence_without_verifier'`; do not present it as verifier approval.
- Buffer each model attempt's text deltas until its finish reason is known. Emit and commit a final
  candidate only after the completion gate passes. A rejected candidate is never written to chat
  history or exposed as a successful final claim.
- If one recoverable completion failure occurs and model/time/tool budgets permit, inject a bounded
  system feedback projection containing only facts and next actions, then allow exactly one model
  correction. This projection is not a user message and is not a ConversationLog writer. A second
  failure or exhausted budget closes with an explicit stop code.
- Add distinct stop codes for missing required change, validation failure/missing validation,
  unexpected or forbidden workspace changes, unresolved tools, verifier failure and inconclusive
  completion. Existing model/tool-call/run-timeout codes remain the distinct budget outcomes. Do
  not add event types or raw payload fields; reuse the existing error/completion lifecycle.

### Observation, composition and compatibility

- Retain `CompletionCheckResult` and `ValidationFact` on Session as process-local facts. Extend the
  terminal summary to display command success separately from validation and show verified versus
  runtime-evidence completion basis.
- Add only bounded terminal aggregates needed by future schedulers to AgentRun observability:
  validation outcome, completion outcome/basis and reason code. Use the next additive operational
  migration with clean old-row defaults; do not persist per-command arguments/results or workspace
  content.
- Inject outcome preparation/checking through normal bootstrap/orchestrator composition. Keep
  `run_turn()` a thin delegate to `run_task()`, Session-owned ConversationLog as the sole chat
  writer, ToolExecutor frozen per prepared run, and the existing public event type/payload shape.
- Preserve compatibility for explanation and legacy/unspecified tasks: they may complete without a
  diff when no explicit write/test/verifier requirement exists, but their basis is reported as
  inferred runtime evidence rather than verified business completion.

## 4. Test-first implementation sequence

1. Add failing strict-model and metric tests proving ordinary command facts never set validation,
   and latest recognized validator facts aggregate by kind/scope/status.
2. Add table-driven process classification tests for direct/`uv run` pytest, Ruff and compileall;
   path scope normalization; utility commands; ambiguous/multi-command shell; timeout, signal and
   redaction boundaries.
3. Add Outcome Contract compiler/explicit-contract tests for change, explanation and unspecified
   tasks, target/allowed/forbidden paths, validation requirements and bounded safe serialization.
4. Implement Git baseline plus no-follow bounded fallback and compare tests covering clean repos,
   pre-existing dirty/untracked user files, modified dirty content, new unexpected files, reverted
   writes, symlinks, non-repositories, truncation and scan failure.
5. Add completion checker matrices for relevant/empty diff, required validation pass/fail/missing,
   latest rerun, known failures, unresolved calls, unexpected/forbidden paths and verifier
   pass/fail/inconclusive/no-verifier basis.
6. Integrate stop gating and buffered deltas into AgentLoop. Prove a rejected final candidate is
   neither emitted as success nor appended; one bounded correction can succeed; the second failure
   gets the exact stop code; cancellation/error/history pairing remain legal.
7. Freeze and rehydrate contract/baseline evidence through fresh/resumed durable runs; add the
   additive observability migration and exact terminal aggregate/replay/backup/doctor tests.
8. Add scripted Direct-agent acceptance cases for utility-only false validation, code task with no
   diff, failed required test plus false prose, unexpected file, successful scoped change+checks,
   and optional fake verifier authority. No live model is used.
9. Update architecture and terminal documentation, publish
   `docs/acceptance/s7p-05-validation-completion-truth.md`, update execution state and run focused
   plus repository-wide offline gates.

## 5. Validation

```bash
uv run pytest -q tests/test_capabilities.py tests/test_local_process.py
uv run pytest -q tests/test_agent_tool_loop.py tests/test_context_runtime.py
uv run pytest -q tests/test_agent_run_observability.py tests/test_operational_store.py
uv run pytest -q tests/test_stage4_recovery.py tests/test_stage4_journal.py
uv run pytest -q tests/test_stage3_product_acceptance.py tests/test_code_agent_mini_eval.py
uv run pytest -m 'not live'
uv run ruff format --check .
uv run ruff check .
uv run python -m compileall -q src tests
uv run morrow --help
uv run morrow run --help
git diff --check
```

If sandboxed `uv` cannot access its cache, use the current worktree's already-synchronized
`.venv/bin/python -m pytest`, `.venv/bin/ruff` and `.venv/bin/morrow` equivalents and record the
restriction plus exact fallback. No live Provider/model/Pi/MCP/network/credential test is allowed.

## 6. Completion, review and integration

- After coherent verified commits, the dedicated Luna Max implementation task must spawn one
  read-only `gpt-5.6-luna` / `max` subagent in that same task to review the complete activation-
  base...HEAD diff.
- Review must focus on false-positive validation, command parser bypass, scope mismatch, dirty-user
  attribution, symlink/scan confinement, buffered false claims, correction/history integrity,
  recovery freeze, verifier authority, stop-code precision and tests that only prove mocks.
- The implementation task reproduces and fixes every confirmed finding, reruns affected and full
  offline gates, commits acceptance/execution state and leaves a clean branch.
- It does not merge, push, delete its branch/worktree, touch the three user-owned research
  documents or start S7P-06. The root task verifies ancestry/cleanliness, fast-forward merges into
  local `main`, then retires clean task resources.
