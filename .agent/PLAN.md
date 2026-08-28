# Stage 7 Consolidated Main — S7P-09 Paused

> Status: integrated code verified; merge commit and branch retirement in progress on local `main`
> Paused subplan: Subplan 90 — S7P-09 Repeated Direct Evaluation and Same-Condition Pi Baseline
> Integrated branches: `feat/s7p-09-direct-pi-baseline`, `feat/s7p-09-direct-pi-baseline-run`
> Activation base: verified local `main@1fd7e229bef276d1a0361e775ce800ade4b318fc`
> Source authority: current user request, S7P-00 protocol v1, completed S7P-08, current code/tools

## 1. Current objective

Consolidate all verified local Stage 7 work on `main`, retaining the completed Pi-aligned seven-tool
interface, core-boundary simplification and best-effort Learning Review repair. Resolve cross-branch
integration defects, run the complete offline/static gate, then retire the merged topic branches and
their clean execution worktree. S7P-09 live admission remains paused.

## 1a. Completed product objective

Keep learning best-effort and mechanically triggered after an accepted completed task. Tool-call
failures and inferred task-resolution quality do not gate Review creation. The Learning Reviewer
uses the main Agent model, task timeout and context budget rather than separate conservative
limits. Repair the Preference Review jobs JSON surface. Confirm the managed Skill projection fix
already present at `3670860` through a real public run.

The earlier Pi-first core simplification remains completed and S7P-09 remains paused.

## 1b. Earlier objective

Restore a small Pi-like coding loop before continuing evaluation work. Registered workspace
read/write/edit/bash tools run directly; command and file-content keyword heuristics do not decide
permission or approval. Keep only structural boundaries needed for correctness and explicit
authority: workspace path confinement, read-only sessions, revision/conflict checks, atomic
publication, timeouts/cancellation/output bounds, exact active-credential redaction, Full Access
grants, and extension-specific Skill/MCP policy. Project instructions load once from the root and
malformed optional context warns/skips instead of blocking task preparation.

## 2. Frozen decisions

- The current user request pauses S7P-09 campaign admission and overrides its former permission-
  equivalence assumptions. No live evaluation run is authorized by this repair.
- Core command parsing and sensitive-keyword scanning are not security boundaries. `git`, shell
  redirection/pipelines, `mv`, `cp`, `tee`, `.env`, `secret`, credential examples and PEM fixtures
  remain ordinary model-visible workspace content.
- Exact active credential values are still redacted from command output. Workspace escape,
  external symlinks, read-only sessions, stale revisions, non-atomic publication, Full Access and
  Skill/MCP authority remain real boundaries.
- Project instruction discovery is root-only with precedence `AGENTS.override.md`, `AGENTS.md`,
  `CLAUDE.md`; bad or oversized files warn and skip, and task paths never trigger nested discovery.

- S7P-00 protocol v1 thresholds and task/verifier data are immutable in this subplan.
- The primary campaign has exactly 28 admitted runs: Morrow 20 and Pi 8. Formal results are never
  discarded or replaced after observation.
- Morrow and Pi use the same canonical Provider/model/revision, task bytes, sampling contract,
  baseline tree, verifier and 1,800-second external deadline.
- Product-native prompts and tool schemas may differ, but capability/permission boundaries must be
  equivalent, content-hashed and proved offline before live execution.
- Morrow runs use ordinary bootstrap, TaskRun, AgentRun, AgentLoop, ToolExecutor, permission and
  ConversationLog boundaries. Evaluation approval can confirm an already-confined policy decision
  but cannot override denial or grant broader authority.
- The user-requested Direct Coding prompt simplification is a pre-campaign product change:
  `direct-coding/v2` keeps one concise execution-side permission statement and expresses the rest
  as positive action guidance. It must be included in the next clean source/profile pin before any
  new admission.
- The user-requested context/compaction resilience repair is also frozen before the next admission:
  explicit long-horizon runs without exact window metadata use the conservative character budget,
  Provider summary presentation noise is normalized before strict durable validation, and URL-safe
  compaction IDs cannot fail randomly. No prior source pin may be reused.
- Raw events, reasoning, full tool payloads, credentials and tracebacks stay outside Git. Only
  bounded normalized evidence, hashes, summaries and the baseline record are committed.
- S7P-09 measures the frozen Agent. It does not tune thresholds/tasks after results, restore the
  removed CompletionChecker or begin Workflow/S7P-10 work.

## 3. Paused evaluation context

The user approved `opencode-go/mimo-v2.5` for both Agents, raised the hard total ceiling from
5,000,000 to 15,000,000 tokens, and kept no currency ceiling. Pi 0.84.2's installed catalog contains the exact model at the same
service endpoint, with a 1,000,000-token context window and 128,000-token maximum output. Before a
formal paid run, the harness must still freeze and verify:

1. Morrow Agent streaming readiness after two identical bounded `internal` failures;
2. a cost-accounting contract compatible with Morrow's unavailable Provider cost;
3. the exact served model revision and equal sampling contract;
4. the final non-secret comparison plan and clean source/evidence pins.

Credential checks must report only readiness and must never request or print credential values.
Pi credential readiness and its exact-model no-tool probe pass without exposing or copying the
credential. Formal attempts have now accounted for 3,567,421 tokens plus one interrupted request
with unavailable usage. Sampled Morrow/Pi usage projects about 17.9M tokens for one fresh complete
campaign, so the approved 15M total is insufficient. No new admission is allowed without a larger
token ceiling and a clean source pin. The user subsequently raised the hard total ceiling to
50,000,000 tokens and authorized a recoverable stash of unrelated untracked notes for the clean
campaign pin. Because an external process continued creating new notes after the stash, campaign
execution uses one dedicated clean worktree branch instead of repeatedly moving that external work.

## 4. Execution order

1. Merge the completed core/Learning branch into local `main` and resolve its policy integration.
2. Merge the completed seven-tool cleanup branch without restoring legacy model adapters.
3. Reconcile current architecture, tests and execution state around the combined behavior.
4. Run focused and complete offline/static/CLI gates and commit verified integration repairs.
5. Remove merged topic branches and the clean execution worktree; preserve unfinished notes in a
   recoverable stash.
6. Do not resume S7P-09 admissions until the user explicitly requests a new evaluation plan.

## 5. Completion

Completion requires both topic histories to be ancestors of `main`, the seven-tool interface and
simplified core boundary to coexist, targeted regressions and the complete non-live suite to pass,
Ruff/compileall/CLI/diff checks to pass, and the merged branches/worktree to be retired safely.
