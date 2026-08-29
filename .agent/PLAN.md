# Stage 7 — S7P-09 Repeated Direct/Pi Baseline Resumed

> Status: active; continuing Subplan 90 from local `main`
> Active subplan: Subplan 90 — S7P-09 Repeated Direct Evaluation and Same-Condition Pi Baseline
> Consolidation base: `a7e22e5`; current source includes the later Learning checkpoints
> Activation base: verified local `main@1fd7e229bef276d1a0361e775ce800ade4b318fc`
> Source authority: current user request, S7P-00 protocol v1, completed S7P-08, current code/tools

## 1. Current objective

Resume Subplan 90 from the consolidated local `main`. First restore the explicit denial boundary
required by the frozen Morrow/Pi permission-equivalence contract, then run the complete offline/static
gate and create a new clean source/profile/evidence pin. If the approved ceiling cannot carry the
28-run primary, use the explicitly marked 14-run reduced single-repetition pilot; formal admission
remains gated by preflight and cumulative capacity. The authorized r16 pilot has completed all 14
fresh admissions and executions after successful short Mimo probes. It produced one Morrow PASS,
but the reduced one-repetition sample and incomplete Provider usage cannot satisfy the primary
comparison gate.

The user next requested a fresh model-scoped 14-run budget using
`opencode-go/deepseek-v4-flash`, with immediate notification on Provider failure. Morrow was updated
to retain and select that exact model, but the first bounded Morrow no-tool readiness request
stopped as `invalid_response` with unavailable usage. The subsequently authorized Pi probe completed
with 536 tokens, isolating the blocker to Morrow's Adapter response handling. No DeepSeek campaign
plan or admission was created. The Adapter now follows Pi's provider-neutral latest-usage-snapshot
semantics, its full offline gate passes, and a repaired Morrow readiness probe completed with 3,171
tokens. Subsequent retained r17/r18 attempts exposed a second generic compatibility gap: OpenCode
occasionally accompanies valid tool calls with whitespace-only text, which Pi accepts but Morrow
passed into its non-empty `AssistantMessage.content` validator. The Adapter now normalizes that
optional tool-call text to `None` without weakening final-text validation. Formal execution remains
paused until a fresh campaign is pinned from the newly repaired source; r17/r18 admissions are not
reused.

## 1a. Completed product objective

Keep learning best-effort and mechanically triggered after an accepted completed task. Tool-call
failures and inferred task-resolution quality do not gate Review creation. The Learning Reviewer
uses the main Agent model, task timeout and context budget rather than separate conservative
limits. Repair the Preference Review jobs JSON surface. Confirm the managed Skill projection fix
already present at `3670860` through a real public run.

The earlier Pi-first core simplification remains completed. This continuation does not restore
keyword-based command classification or per-call heuristic approval.

## 1b. Earlier objective

Restore a small Pi-like coding loop before continuing evaluation work. Registered workspace
read/write/edit/bash tools run directly; command and file-content keyword heuristics do not decide
permission or approval. Keep only structural boundaries needed for correctness and explicit
authority: workspace path confinement, read-only sessions, revision/conflict checks, atomic
publication, timeouts/cancellation/output bounds, exact active-credential redaction, Full Access
grants, and extension-specific Skill/MCP policy. Project instructions load once from the root and
malformed optional context warns/skips instead of blocking task preparation.

## 2. Frozen decisions

- The current user request explicitly resumes Subplan 90. The 28-run primary campaign remains
  create-only and cannot start until the new source/profile/evidence plan passes its hold point and
  capacity check. The current user-approved budget fallback is the explicit
  `reduced-single-repetition-v1` plan variant: 14 runs, all ten Morrow tasks once and the four Pi
  tasks once. It is a pilot observation and does not complete the repeated primary baseline.
- Core command parsing and sensitive-keyword scanning are not security boundaries. `git`, shell
  redirection/pipelines, `mv`, `cp`, `tee`, `.env`, `secret`, credential examples and PEM fixtures
  remain ordinary model-visible workspace content.
- Exact active credential values are still redacted from command output. Workspace escape,
  external symlinks, read-only sessions, stale revisions, non-atomic publication, Full Access and
  Skill/MCP authority remain real boundaries. Explicit `network`, `git_write` and
  `privilege_escalation` risk flags remain denied; ordinary command content is not parsed to infer
  those flags.
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
- Provider retry follows Pi's transient boundary for 408, 409, 429, 5xx, timeout, connection
  interruption and premature stream termination, while quota/balance/billing and unattributed
  Morrow internals remain terminal. Partial usage stays explicitly unavailable in evaluation.
- The user separately authorized aligning the ordinary bundled `model_retry_limit` with Pi at
  three retries. The maximum model-attempt bound remains unchanged.
- S7P-09 measures the frozen Agent. It does not tune thresholds/tasks after results, restore the
  removed CompletionChecker or begin Workflow/S7P-10 work.
- Morrow admission generates its minimal isolated configuration directly from the frozen
  Provider/service/model selection plus the matching configured Keychain reference, then calls the
  existing `build_active()`. Admission is created only after that load succeeds; this is not a new
  readiness command and does not add a no-tool model probe.

## 3. Evaluation context and current hold point

The user approved `opencode-go/mimo-v2.5` for both Agents and initially set a hard total ceiling of
50,000,000 tokens with no currency ceiling, then explicitly added 30,000,000 tokens for the current
reduced pilot, raising the active ceiling to 80,000,000. Pi 0.84.2's installed catalog contains the exact model
at the same service endpoint, with a 1,000,000-token context window and 128,000-token maximum
output. Before any further formal paid run, the harness must still freeze and verify:

1. Morrow Agent streaming readiness after two identical bounded `internal` failures;
2. a cost-accounting contract compatible with Morrow's unavailable Provider cost;
3. the exact served model revision and equal sampling contract;
4. the final non-secret comparison plan and clean source/evidence pins.

Credential checks must report only readiness and must never request or print credential values.
Pi credential readiness and its exact-model no-tool probe pass without exposing or copying the
credential. Retained formal attempts include known usage and incomplete requests, and the
conservative reservation for a fresh complete campaign currently exceeds the remaining capacity
under the active 80,000,000-token ceiling before all unknown-usage requests are resolved. The
reduced r16 plan was freshly pinned at `main@90b0e9b` and all 14 frozen admissions were executed.
Every bundle validates. Morrow produced one PASS, two model failures, one budget exhaustion and six
runtime failures; Pi produced two model failures and two runtime failures. Some Morrow usage remains
unavailable, and the reduced schedule has only one repetition, so the paired comparison remains
incomplete. The unrelated `docs/notes/` work remains preserved in its named recoverable stash.

## 4. Execution order

1. Repair and regression-test the explicit Morrow risk-denial ordering while preserving direct
   registered command execution.
2. Run focused and complete offline/static/CLI gates and commit the verified repair.
3. Refreeze the comparison plan from the current clean source, current profile hashes and protected
   evidence root; do not reuse any prior admission or schedule.
4. Run offline preflight, permission equivalence and conservative campaign-capacity checks, passing
   every retained campaign root explicitly into the cumulative budget audit. Use the reduced
   variant's 14-run reservation when the full 28-run primary does not fit.
5. If capacity and all hold-point facts pass, present the exact formal admission boundary before
   scheduling; otherwise retain the new plan as blocked evidence and stop. The authorized r16
   reduced pilot has completed this step and is retained as bounded evaluation evidence. Its single
   Morrow PASS does not promote the reduced campaign to a comparison PASS.

## 5. Completion

Completion of this continuation requires the explicit deny boundary and seven-tool interface to
coexist with the simplified core boundary, all targeted and complete non-live/static gates to pass,
and a clean immutable plan to be either admitted within capacity or recorded as blocked with its
precise reason. Full S7P-09 completion still requires the 28 valid primary runs and its comparison
gate as defined by Subplan 90.
