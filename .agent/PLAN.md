# Stage 7 Static Workflow Runtime Implementation Plan

> Status: Subplan 4 completed and integrated; Stage 7 remains in progress
> Active subplan: none
> Next subplan: 5 — Serial DAG Scheduler (ready; not active)
> Planning base: local `main@4d8b408` (tree clean, full offline gate green; later `.agent`-only
> commits such as the plan-repair commit do not invalidate this verified code base)
> Roadmap authority: `docs/roadmap/stage-7-workflow-runtime.md`
> Entry evidence: `docs/acceptance/s7p-10-stage7-entry-review.md` — GO
> Revision: 2026-08-31 conditional-GO plan review applied
> (`docs/acceptance/stage-7-plan-review-revision-2026-08-31.md`): validate/publish purity,
> declared tool requirements, disable-versus-revoke separation, a typed result-submission
> protocol, truthful `needs_revision` semantics, one unified Scheduler with an all-isolated
> first slice, and bounded read-only parallelism deferred to Stage 8. The superseded nine-way
> plan is archived under `.agent/archive/subplans/stage7-workflow-runtime-v1/`.

## 1. Objective

Build the smallest reliable static Workflow Runtime that can compose the existing single-Agent leaf
without moving graph logic, role logic, or a second chat-history writer into `AgentLoop`:

```text
versioned AgentDefinition
+ immutable WorkflowRevision
+ typed Artifact handoff with an explicit result-submission protocol
+ one-node isolated vertical slice on a single unified Scheduler
+ deterministic serial DAG scheduling
+ recoverable Explorer -> Coder -> Reviewer
+ CLI/query observation, built-in templates, optionally approved additive events
+ an opt-in Direct invoking-session adapter added only after serial stability is proven
```

Stage 7 executes every Workflow serially; all leaves use isolated Sessions until Subplan 7 adds
the Direct invoking-session adapter. Bounded read-only parallelism is Stage 8 work with its own
entry conditions (see `docs/roadmap/stage-8-adaptive-orchestration-and-gui.md`); the read-only
`access_mode` capability ceiling remains enforced serially in Stage 7. Stage 7 prepares only the
low-cost facts that Stage 8 will need: stable node identity, immutable revision lineage, NodeRun
attempts, Artifact producer provenance and a run frozen to one revision. It does not implement
GraphPatch, runtime graph mutation, task-generated DAGs, GUI editing, a Replanner or any
concurrent frontier.

## 2. Authority and execution rules

1. Current user decisions and later explicit scope changes.
2. Current code and validation just run.
3. This plan and the one active child subplan.
4. The Stage 7 roadmap.
5. Acceptance, proposals and reviews as decision history rather than parallel specifications.

Implementation follows these rules:

- at most one child subplan is active and it starts from the latest verified `main`;
- later subplan schemas, APIs and runtime behavior are not implemented early;
- existing AgentRun preparation, permissions, ToolExecutor, Artifact storage, recovery and
  ConversationLog boundaries are reused rather than wrapped in equivalent Workflow-owned systems;
- ordinary chat stays on the current Direct path until Subplan 7 proves opt-in Direct Workflow
  parity; switching a default requires separate evidence;
- no third-party dependency is added without explicit user approval;
- no Live Provider/MCP/network/credential test runs without separate authorization and compatible
  credentials;
- no public `AgentEvent` lifecycle or bundled runtime-policy default change is included;
  `ApplicationEvent` is also a client-facing cursor contract, so adding Workflow event types in
  Subplan 8 requires an explicit scope check and user authorization first. Query/CLI delivery must
  remain functional if that event addition is deferred;
- planning alone does not authorize production implementation; the user explicitly started
  Subplan 1 on 2026-08-31. Later child activation remains sequential.

## 3. Proportionality and availability rules

Safety is a means of preserving a runnable, truthful system. A new hard gate is allowed only when
all of the following are true:

1. it protects an invariant used by the current Stage 7 slice;
2. violation can cause permission escalation, persistent-state corruption, duplicate/unknown side
   effects, historical drift, or a graph whose execution is undefined;
3. the existing AgentRun, ToolExecutor, Artifact, Session or Operational Store owner does not
   already enforce it;
4. the cheapest enforcement point is identified;
5. one deterministic rejection test and one adjacent legal acceptance test can be written.

If those conditions are not met, the mechanism is a warning, a serial/Direct fallback, telemetry or
future work—not a blocker.

| Classification | Stage 7 examples | Behavior |
|---|---|---|
| Invalid / reject | malformed definition, missing reference or required binding, cycle, no producer for a required output, revision hash corruption/OCC-stale update, capability escalation | reject only that definition/run with an actionable error |
| Unsafe to continue | `read` contract/effective ToolSet conflict, runtime capability drift, recovery with unknown side effect | fail preparation or block only the affected Workflow; a legal `write` node remains runnable serially |
| Temporarily unavailable | Provider/MCP unavailable at node start, transient resource failure before side effects | fail the target NodeRun; reserve `blocked` for an unresolved/unknown side effect and do not invalidate definitions or application startup |
| Quality / optimization | cost unavailable, weak model result, optional context absent, no parallel benefit | record honestly; warn or fall back; never relabel as a safety failure |

Additional proportionality constraints:

- validate once at the authoritative input boundary; internal typed objects are not repeatedly
  revalidated by new layers;
- every rejection rule has a nearby positive-path test; a guard that prevents a legal Direct or
  supported Workflow from running is a defect;
- Workflow errors remain isolated to the relevant definition/run; existing Direct chat remains
  operable;
- Provider health is a runtime fact, not a compile-time network probe;
- Stage 7 text adds one value-sensitive detection profile inside the existing redaction/refusal
  owner: benign vocabulary (for example, “password validation”) is data; high-confidence
  credential tokens or explicit non-placeholder secret values are unsafe. The current owner in fact
  already carries three detection variants — the generic needle substring scan
  (`refuse_secret_material`), the reduced-needle provider-runtime variant (which already exempts
  `credential_ref`), and the value-shaped preview detection in `core/execution.py` (which already
  keeps code identifiers such as `credential`/`api_key` legal and flags only value-shaped secrets).
  The new profile therefore consolidates the existing value-shaped detection into one shared
  implementation dispatched by profile, rather than adding a fourth independent rule. Subplan 1
  now shares the existing preview/literal patterns for Definition publication; Subplan 2 extends
  the same owner with durable Artifact/Outcome profiles and calibration so Stage 7 does not create a separate scanning subsystem, policy engine or
  second refusal authority. Its detection accuracy is a
  Stage 7-critical component: a false positive can block a legal terminal, a false negative can
  persist a secret, so §8 requires a dedicated calibration test set beyond the per-subplan cases.
  Input/definition values
  are rejected locally before publish/Start, while output projections redact only the unsafe value
  and truthfully mark incomplete instead of preventing terminal closure. Legacy non-Workflow
  callers keep their byte-identical current rule. The same mode covers Workflow-produced root `TaskOutcome`
  projections: benign paths/facts such as `password_validation.py` or `authorization test passed`
  cannot block terminal closure, while an actual credential value is omitted/redacted and marked
  by the fixed existing `completion_basis` fact `workflow_evidence_redacted=true` before the Outcome
  is persisted; no second Outcome type or `content_complete` field is added;
- because current Artifact/TaskOutcome models apply the legacy keyword rule during both construction
  and rehydration, Subplan 2 adds one persisted internal `TextSafetyProfile` discriminator
  (`legacy_strict|workflow_value_sensitive`) to those two durable envelopes. Existing rows and every
  ordinary caller default to `legacy_strict`; only typed Workflow Artifact/Outcome application seams
  select `workflow_value_sensitive`. YAML, prompts and public CLI cannot choose it. Both profiles are
  dispatched inside the existing redaction/refusal owner through one shared entry point, so no
  caller gains a way to bypass refusal and no second policy authority appears;
- missing usage or cost is `unavailable`, not zero and not a failed Node. Stage 7 hard aggregate
  limits are intentionally restricted to facts Morrow can authoritatively admit: primary
  `agent_generation_request_count` (the existing durable request rows with `purpose=agent`), a
  positive relative `admission_timeout_seconds` and concurrency. A Revision contains the
  duration, never an absolute wall-clock deadline; Start uses an injected clock to freeze
  `WorkflowRun.admission_deadline_at = started_at + duration`. Static node count is already bounded
  by the frozen DAG and unique NodeRun rows; no second Node-admission counter is created;
- serial admission freezes
  `effective_node_generation_request_cap = min(declared_node_max_agent_generation_requests,
  workflow_remaining_agent_generation_requests)` and runs
  whenever that cap is greater than zero. Only zero remaining requests stops admission. The cap is
  enforced through the existing durable request-admission seam and recorded on the leaf evidence;
- tool-call/round counts and Provider token/cost are reported, but are not hard aggregate limits in
  Stage 7. If any required Provider usage is unavailable, the aggregate is `unavailable`; no guessed
  worst-case token charge may block later legal nodes. Existing per-AgentRun context, retry and tool
  timeout policy remains authoritative and is not mislabeled as a total-token budget;
- Stage 7 admits one node at a time, so no reservation ledger exists. The reviewed whole-node
  maximum reservation design for parallel batches was rejected as over-conservative; the Stage 8
  read-only-parallelism entry instead adopts per-request atomic claim against the Workflow
  remaining budget under each leaf's frozen node-local cap, settling actual cost after each
  response. Exhaustion never reclassifies a completed Node or invalidates the graph;
- automatic compaction Provider summaries are not currently admitted through that durable seam.
  Stage 7 therefore does not relabel this counter as total Provider/model requests: compaction stays
  under existing per-AgentRun context/retry bounds and its count/usage gap is reported as excluded or
  unavailable. Instrumenting every compaction attempt would require a generic AgentLoop observation
  migration without changing Stage 7 scheduling truth, so it is explicitly deferred;
- the deadline stops new Node/agent-generation-request admission; it is an admission cutoff, not a
  total wall-clock deadline for compaction or Tool execution. It does not forcibly interrupt an executing
  Tool and manufacture an unknown side effect. After that Tool settles, the existing durable
  agent-request admission seam rejects the next generation request and closes the active node as
  `failed(reason=budget_exhausted|deadline_exceeded)`; an outcome-unknown Tool uses `blocked`
  instead. Active cancellation/recovery stays with existing controls;
- no generic Policy DSL, rule engine, schema registry, converter framework, distributed lock,
  tracing platform or plugin abstraction is created without two current consumers and evidence
  that existing seams are insufficient;
- tests are proportional to changed risk; exhaustive matrices, load tests, fuzzing, wall-clock
  sleeps and Live campaigns are not routine implementation gates.

## 4. Locked architecture

### 4.1 Definitions, revisions and runs

- workspace-scoped `agent-definitions.yaml` and `workflow-definitions.yaml` hold editable user
  desired sources loaded through typed, revision-checked adapters. The adapter/document envelope
  supplies an OCC document revision and, for each definition ID, a hash computed from that canonical
  normalized body; neither is a user-editable per-definition body field. Sources contain no runtime current-version pointer or
  operational enable switch. Built-ins are packaged
  read-only source objects projected through the same typed contract, not workspace YAML entries that
  `edit` may overwrite; Stage 7 tells users to create a new-ID user definition instead of adding a
  copy command. `validate` is pure and read-only for every definition kind: it parses, resolves
  references, runs static checks and returns candidate diagnostics, and it never creates a
  Version/Revision, never advances a head and never lazy-publishes, so CI validation and read-only
  inspection stay write-free. Only an explicit `publish` command creates an immutable
  Version/Revision and advances the head. `run` requires an exact already-published Revision and
  offers one explicit opt-in `--ensure-published` escape that publishes the current desired source
  first and echoes the chosen Revision; without it, `run` against an unpublished definition fails
  with an actionable publish instruction. A packaged built-in therefore becomes runnable only
  through the same explicit publication path as a user definition, idempotently (the canonical
  content-hash no-op makes repeats free). No startup migration or background step publishes
  built-ins silently, and an unpublished built-in is visible-but-not-runnable rather than an error.
- full immutable `AgentDefinitionVersion` rows, `WorkflowRevision` rows and their published-head
  pointers live in the Operational Store. Each head records the exact source revision/hash it was
  published from and an OCC-protected `enabled` admission flag, so a newer YAML edit is simply
  visible as desired state ahead of published state; comparison uses that definition's body hash, so
  editing an unrelated entry in the same document does not make every head stale. Document revision
  remains OCC/audit metadata. No cross-YAML/SQLite transaction or saga is claimed. Enable/disable
  changes the head only and creates no Version/Revision.
- an AgentDefinition publication service is the only path from desired source to a stored immutable
  Version/head. AgentFactory and Workflow compilation consume published Versions, so old Workflow
  Revisions remain executable after later source edits.
- the pure WorkflowCompiler normalizes, validates and hashes without IO. One
  `WorkflowCompilationService` is the sole application publication path: it invokes the compiler,
  then transactionally stores the runnable WorkflowRevision and advances its SQLite published head.
  Repositories accept/preserve candidates but cannot create or hash a second revision path.
- compilation probes a command receipt first. For a new command it always builds the full candidate
  against current immutable catalogs/config, then treats publication as a no-op only when the
  current published Revision has the same canonical compiled `content_hash`, including normalized
  source metadata (name/description/tags/origin), graph/contracts/budget, every exact immutable ref,
  `resolved_model_ref` and compiler version. IDs/timestamps/parent lineage/source OCC metadata and
  operational Head state are excluded. Source body hash is only desired/published evidence;
  it cannot short-circuit compilation when `invoking_active` may resolve differently.
- `model_selection=invoking_active` has one explicit resolution-point contract: it is resolved
  exactly once at the consuming artifact's own freeze boundary and never re-resolved afterwards.
  For a Workflow that boundary is publication: Workflow compilation resolves it from the then-active
  model and freezes the exact `resolved_model_ref` into each Revision node, so a later active-model
  change leaves every published Revision untouched and takes effect only through an explicit new
  publication. For a standalone non-Workflow AgentRun that boundary is its own run admission. Per-run
  re-resolution under one already published Revision is rejected because it would make executions of
  the same Revision non-reproducible. Workflow leaf preparation must consume the frozen ref and must
  not read the then-current active model.
- a `WorkflowRevision` has an opaque `workflow_revision_id`, a monotonic display revision within
  its Definition, full normalized source metadata (name/description/tags/origin), an optional
  `parent_workflow_revision_id`, frozen normalized `input_contract` and exact `required_outputs[]`.
  A `WorkflowRun` references the opaque ID of exactly one frozen
  Revision; Start never rereads the current desired Source contract.
- Stage 7 v1 accepts exactly one Workflow input contract, `TaskContract@1`, because Start has exactly
  one bounded TaskContract producer. Any other input kind/version is a compile error with the exact
  legal case beside it; multi-input or alternative Workflow input contracts wait for a consumer.
- successful publication atomically stores the immutable Revision and advances the Operational
  Store head under OCC. Failed compilation/publication stores nothing and does not move that head;
  the editable YAML source is never part of this SQLite transaction. A later publication preserves
  the current head enable flag; the first publication creates an enabled head unless its publish
  command explicitly requests disabled state.
- ordinary disable and emergency revocation are different mechanisms. Disabling a Workflow head
  rejects only new Workflow starts; disabling an Agent head rejects only new admissions — new
  standalone AgentRuns and new WorkflowRun Starts (Start verifies every referenced Agent head is
  enabled). An already-admitted WorkflowRun is frozen: ordinary disable never blocks its
  not-yet-started nodes, its running AgentRuns or its recovery, all of which continue from frozen
  exact-version evidence. Historical definitions, Revisions, Runs and Artifacts remain inspectable.
  Emergency revocation is the separate safety brake: an additive, audited, one-way revocation record
  per exact immutable AgentDefinitionVersion or WorkflowRevision (reason, timestamp and command
  provenance; never a mutation of the immutable row, and never reversible — publish a new version to
  supersede). Revocation is checked at Start, at every not-yet-started node admission and at
  recovery resume; a revoked exact version blocks all three, and an affected WorkflowRun closes as
  `cancelled(reason=policy_revoked)` with the root TaskRun delegated to the existing cancel command
  and the revocation evidence recorded. An ordinary `enabled=false` therefore never doubles as a
  security brake, and a revoked version can never silently re-enter through publication replay.
- stable `node_id` identifies a semantic node inside a Revision. Every execution record has a
  unique `node_run_id`, and `(workflow_run_id, node_id, attempt)` is unique. Stage 7 creates attempt
  1 only; a full explicit rerun creates a new WorkflowRun. Node-level attempt >1 remains deferred;
  the current Stage 8 direction also uses child/new WorkflowRuns so terminal parents stay immutable.
- the optional parent Revision ID records cheap lineage only. Stage 7 has no GraphPatch, diff,
  continuation-run or runtime-edit engine.

### 4.2 NodeRun is not AgentRun

`NodeRun` is the scheduler unit. An agent node creates an `AgentRun`; Stage 7 does not pretend that
future deterministic, approval or control nodes are Agents. The first runtime implements agent
nodes only. Parallel fan-in uses a Synthesizer Agent rather than inventing a generic Merge runtime.

### 4.3 Conversation ownership and context isolation

Session-owned `ConversationLog` remains the sole chat-history authority and writer boundary. Every
leaf AgentRun stores `conversation_session_id`; only that Session's AgentLoop appends.

Two conversation-scope modes exist, but they arrive in order and share one execution engine:

- `isolated` is the only mode Stage 7 implements first. Every Workflow node — including a one-node
  graph — uses a newly created empty standalone Session plus an internal leaf TaskRun whose purpose
  is `workflow_node`, linked through NodeRun to the WorkflowRun's root TaskRun. This preserves the
  existing invariant that a Turn and its TaskRun share one Session. Leaves receive only the root
  Task Contract and explicitly bound Artifacts. All isolated graphs are Scheduler-owned and use the
  atomic Workflow/root terminal path.
- `invoking_session` is added only by Subplan 7's Direct adapter, after the serial Scheduler is
  stable. It is legal only for a graph with exactly one node and no edges, preserving the ordinary
  root Session, root TaskRun and Direct chat parity; any multi-node Revision containing it is a
  compile error because its first STOP would transition the root before downstream nodes can run.
  It runs through the same Scheduler/committer/finalizer with a different Session-binding strategy
  — there is no separate Direct runner or second state machine. Subplans 1–3 do not create the
  `invoking_session` schema value, fixtures or placeholder gates before that consumer exists.

Because `needs_revision` closes the root TaskRun as `READY_FOR_ACCEPTANCE` (§4.6), no scope-specific
result-driving ReviewReport restriction is needed: the invoking-session shape may list a
result-driving ReviewReport slot, and Subplan 7 proves it beside ordinary Direct parity.

Current Session fork restores a parent transcript prefix and therefore is not the isolation
mechanism. Internal leaf TaskRuns may reach the existing `ready_for_acceptance` state as leaf-run
evidence, but are excluded from user acceptance and LearningReview entry points; only the root
TaskRun owns the user-visible TaskOutcome. Workflow/Task services store associations and Artifact
bindings; they never append, concatenate or copy another Agent's chat history.

Lifecycle ownership is selected by conversation scope, never by node count: “Direct” below means
the one-node/no-edge `invoking_session` graph added by Subplan 7 and its root TurnLifecycle; every
all-`isolated` graph, including a single isolated node, is Scheduler-owned and uses the atomic
Workflow/root terminal path. “Multi-Agent” is a product/topology description, not a root-terminal
branch condition.

`workflow_node` is also rejected by every ordinary user Task mutation (`accept`, `snapshot`,
`resume`, `cancel`, `fail`, `abandon`, `new`) and by ordinary Turn admission. Scheduler/Recovery use
one explicit internal leaf-lifecycle application boundary; knowing a leaf ID never grants mutation
authority. Workflow Query may show the leaf Session/Task references read-only. No separate
SessionPurpose is added because `Session.current_task_run_id -> TaskRunPurpose` is sufficient.

A `purpose=user` root Task may own at most one nonterminal WorkflowRun. While it does, ordinary
Task mutations/replacement and ordinary Turn admission reject with an actionable instruction to use
the owning foreground cancellation/recovery/abandon path; the one Direct Workflow Turn is admitted
only with its exact
WorkflowRun/root/client-message constraint. Workflow-owned root transitions use one explicit
application boundary. This is the minimum guard against a concurrent `task new` silently abandoning
the root or a Direct Turn attaching to a different current Task. After the Workflow is terminal,
ordinary Task/Turn behavior is unchanged.

The inverse is checked in the same authoritative store transaction: Workflow Start rejects a root
Session that already has an open Turn or nonterminal AgentRun. Ordinary Turn admission rejects an
active Workflow, while Workflow Start rejects ordinary work already in progress; no process-local
flag, global lock or second concurrency system is introduced.

### 4.4 Minimal graph and state model

Stage 7 v1 supports unconditional DAG edges and agent nodes with only the fields execution needs:

```text
node_id
agent_definition_ref (exact immutable AgentDefinitionVersion, never a mutable Head selector)
resolved_model_ref (Compiler-frozen exact ModelRef; not a source field)
task_contract
input_bindings[]:
  input_name (node-local unique)
  accepts: exact kind/version
  source: workflow_input literal `task` | node_output exact `node_id.slot`
output_contracts[]: stable slot + kind/version + required_for_node_completion
access_mode: read | write
conversation_scope: isolated (invoking_session arrives with the Subplan 7 Direct adapter)
optional node tool_requirements[] (restriction-only overlay on the Definition set)
resolved_tool_requirements[] (Compiler-frozen merged set; the source overlay remains hashed)
optional node max-agent-generation-request override
declared_node_max_agent_generation_requests (compiled, positive)
```

Tool requirements are declared, never inferred. An AgentDefinition declares its desired tool set as
`tool_requirements[]` entries of `name` + `requirement: required | optional | forbidden`; the
required/optional names form the desired set and `forbidden` is an explicit deny that always wins.
A WorkflowNode may only narrow that set further or mark an additional tool forbidden/required for
its own mechanism (for example, the Coder's capturable-sandbox bash); it can never name a tool
outside the Definition's declared set. SkillVersion envelopes declare no tool requirements in Stage 7 — the
existing Skill model has no such field, and skill-delivered tools remain subject to the Definition's
declared set and task policy. The Compiler merges Definition + node declarations and freezes the
result with diagnostics under fixed precedence:

```text
forbidden                                > required | optional (conflict is a compile error)
required + denied by policy/access_mode  => compile error (the definition contradicts itself)
required + absent from catalogs          => compile error (static fact)
required + runtime backend unavailable   => that node's preparation failure only (runtime fact)
optional + denied/absent/unavailable     => removed from the frozen evidence + diagnostic
unknown/opaque effect                    => legal serially; never admissible to a read-only ceiling
```

An output slot is unique within its node and is the stable name used by input bindings and the
deterministic `(node_run_id, output_slot)` Artifact identity. Multiple outputs are first-class; Stage
7 does not hide Patch + TestReport inside an untyped composite blob. Output necessity is exactly two
independent facts, not one overloaded flag: a slot's `required_for_node_completion` controls whether
a successful node must materialize it, and the Revision's `required_outputs[]` is the separate export
list of exact `node_id.slot` refs projected into the root Outcome. Every node-output input binding
and every exported output may reference only slots declared `required_for_node_completion=true`,
so anything a downstream non-nullable binding consumes is guaranteed to exist when the producer
completes; an exported-but-unbound or bound-but-unexported slot is legal and the two are distinct. Stage 7
adds no `on_missing` fallback/skip/default semantics to bindings — the producer's completion gate is
the only guarantee mechanism, and optional input behavior waits for a real consumer. A
`required_for_node_completion=false` slot is an inspectable-only observation: it is materialized when
produced, its absence never fails the node, and it can neither be bound downstream nor exported.
A binding's exact `accepts` ContractRef must equal its source contract. The sole Workflow input
source is the literal `task` carrying `TaskContract@1`; input names are unique within a node and a
binding has exactly one discriminated source form. Every exported result Artifact reference
projected into a root Outcome's `artifact_refs` must fit that existing TaskOutcome bound (currently 64);
Compiler rejects one over the limit. TaskContract uses the separate `goal_reference` and is not
duplicated into `artifact_refs`. Optional leaf evidence may be bounded with an explicit omission
fact, but an exported result ref is never silently dropped.

Result semantics reuse those exact Workflow `required_outputs`: only required output refs whose
contract kind is `ReviewReport` are result-driving. After every declared node completes, any bound
result-driving report with a blocking verdict yields `completed/needs_revision`; otherwise the Run
is `completed/succeeded`. Any other ReviewReport remains immutable evidence only. No “latest report”
heuristic or separate result-role schema is introduced.

Every cross-node input binding must have a declared edge in the same producer→consumer direction;
the Compiler rejects a missing edge and never infers or inserts one. An edge with no Artifact binding
is legal as a pure control dependency. Cycle/topological checks and Scheduler readiness use the
declared edge graph, while bindings add the required Artifact condition, so the visible DAG and
runtime dependency graph cannot diverge. A multi-node Revision must form a single weakly connected
component: a disconnected component is a compile error naming the unattached nodes, because silently
executing a typo-orphaned node spends model budget and may perform unintended writes; the fix is an
explicit control edge or removal, not a warning. A connected node whose outputs nobody consumes is
legal, stays execution-required and earns an actionable warning.

`WorkflowRun` stores `root_task_run_id`. `NodeRun` stores its `conversation_session_id` and
`leaf_task_run_id`; for Direct these equal the root Session/TaskRun, while isolated nodes reference
their internal pair. Workflow start also binds one bounded immutable `TaskContract` Artifact so
recovery never depends on an in-memory prompt or on copying the root transcript.

`queued`, `running`, `completed`, `failed`, `cancelled` and recovery-only `blocked` are persisted.
Readiness is derived from the graph and Artifact bindings rather than duplicated as durable state.
`blocked` is committed only after the Morrow-owned handler has returned/released its live handle, or
after restart Recovery observes that the former process handle no longer exists; the external effect
itself may remain unknown. A durable ToolExecution lacking a terminal fact is unknown evidence, not
proof that a controllable local handler is still live.
WorkflowRun has only one narrow pending terminal intent, nullable `user_cancel`, so a cancellation
that encounters an unknown Tool outcome cannot later be mistaken for an ordinary crash/resume.
Every node retained in the frozen Revision is execution-required even when it does not contribute to
a Workflow exported output: it is still scheduled and any failure applies the fixed whole-graph
failure mapping. The output-level `required_for_node_completion` flag controls
materialization/binding only. There is no
optional-node, skip/continue or fallback policy. There is also no conditional-expression language,
approval node, converter, recursive subgraph,
concurrency-group DSL, scheduler retry policy or configurable failure-policy matrix.

Every immutable Revision carries one normalized finite `WorkflowBudget`:
`max_agent_generation_requests`, `default_node_max_agent_generation_requests`, positive relative
`admission_timeout_seconds` and positive `max_concurrency`. For each node the Compiler freezes
`declared_node_max_agent_generation_requests = min(node override if present else Workflow node
default, AgentDefinition run ceiling if present else unbounded)`. The result must be positive. Start
copies the Workflow budget into the Run and admission freezes
`effective_node_generation_request_cap = min(declared node max, Workflow remaining)` on the
NodeRun. This is a fixed v1 schema, not a budget DSL.

### 4.5 Artifact collaboration

The existing immutable Artifact envelope/store remains authoritative. Stage 7 adds only the
contract kind/version, NodeRun producer/binding facts and the small built-in payload models required
by an active template. Artifact text is data and never changes ToolSet, permission or task policy.
The first payloads are Workflow input `TaskContract` and role-neutral `TextResult`; the latter stores
the durable final-Assistant reference/digest, bounded redacted excerpt and `content_complete`
without becoming a second chat authority. Later subplans add only contracts with an active producer
and consumer. No arbitrary schema registry or automatic version converter is introduced.

Output materialization never makes a second Provider/repair request. One optional, role-neutral
`NodeResultCommitter` is composed at the existing TurnLifecycle terminal transaction only for a
Workflow leaf; AgentLoop remains unchanged. After the final Assistant message has been durably
committed but before the Turn terminal/root Task transition, it verifies that every
`required_for_node_completion` slot is satisfied from durable facts, publishes or reuses output
Artifacts under IDs derived from `(node_run_id, output_slot)`, finalizes or reuses an existing
staging Artifact on replay, and contributes the available Artifact binding to that terminal
transaction. Only after every required binding exists may the leaf commit its Turn terminal;
NodeRun completion still waits for that actual AgentRun terminal. Ordinary Direct receives no
committer and keeps its current path.

Slot satisfaction has exactly two forms, chosen by contract kind. A free-text `TextResult` slot is
satisfied by wrapping the already committed final Assistant message itself: the whole message is the
payload (reference/digest plus bounded redacted excerpt), so it is immune to fence-parsing,
duplicate-candidate and missing-field failure modes by construction. Every structured contract
(EvidenceBundle, ReviewReport, PlanArtifact, SynthesisReport and any later structured payload) is
satisfied only through one authoritative submission: an internal `submit_node_result` mechanism tool
composed into the Workflow leaf's ToolSet by leaf composition. It is never granted by definitions,
prompts, Skills or Artifacts, never subject to definition allow/deny or approval prompts, and never
present in ordinary Direct; its only effect is validating and staging/publishing declared output
payloads through the existing ArtifactService and recording a durable submission fact on its own
ToolExecution row. Its contract:

```text
submit_node_result(schema_version, outputs{slot: payload}, summary, evidence_refs[])
1. the natural-language final message remains transcript; it is never parsed for structured data;
2. the tool validates each submitted slot payload against the frozen Revision output contract and
   rejects undeclared slots, wrong kind/version and schema violations with an in-loop error the
   model can correct;
3. exactly one valid submission exists per NodeRun: an identical replay is a no-op reuse and a
   conflicting second submission is refused without overwrite;
4. Artifact bytes are staged/published under deterministic `(node_run_id, output_slot)` identity
   through the existing four-case helper, so a crash between submission and terminal commit is
   replay-safe;
5. evidence_refs must name Artifacts/ToolExecutions already durable inside this node's own scope;
6. the submission call is an ordinary in-loop tool round — it adds no separate structured-completion
   or repair Provider request.
```

A proposed successful STOP with a missing or invalid required structured submission is one bounded
application error, and the existing AgentLoop error path closes the node as
`failed(reason=output_contract_unsatisfied)`; error/cancel terminals bypass output requirements so
failure closure cannot deadlock. Recovery completes the committer from the durable submission fact
and deterministic Artifact identity without re-executing the node or re-parsing any message. The
committer reads durable submission facts; it never calls the model and never modifies NodeRun state
outside the WorkflowTransitionService. Artifact publication/binding failure therefore can never
leave a Workflow root falsely `READY_FOR_ACCEPTANCE`; a crash/staging ambiguity uses existing
Artifact/Agent recovery and affects only that Workflow.

The committer enforces required outputs only for a proposed successful `STOP` terminal, and a known
Artifact failure becomes one bounded application error; AgentLoop's existing error path can then
commit the non-success terminal without invoking the committer again.

Direct and one-node isolated text leaves wrap the already committed visible final Assistant message
as their text result. Structured Multi-Agent contracts are validated submissions from
`submit_node_result` over durable Tool/Artifact facts; the final message may add rationale but is
never parsed for structured data. Writer leaves also enable one narrow `ChangeArtifactCapture` at
the existing durable
tool handler-completion boundary: while the authoritative mutation result/preflight data still
exists, it publishes an immutable complete unified diff when representable, otherwise an exact
structural manifest with before/after hashes, sizes and an explicit `content_complete=false`, and
attaches the reference to that ToolExecution. Existing Artifact bounds/redaction remain the sole
content owner; unsafe, binary or oversized complete content degrades to the truthful structural
manifest rather than blocking an otherwise legal mutation. At the same boundary a recognized `ValidationFact`
produces a small TestReport that references, rather than copies, the existing command-output
Artifact. Artifact IDs derive from the ToolExecution/role/schema version, so safe
reserve/publish/link gaps can replay; the system never reconstructs or guesses a patch later from
the current workspace. The terminal committer builds `ImplementationPatch`/aggregate `TestReport`
from those durable references; final Assistant text supplies rationale, never change or test facts.
A required capture/materialization failure preserves the Tool/leaf evidence and follows the fixed
ordinary-failure or unknown-side-effect mapping. Non-Workflow tools retain their current behavior
and are not made dependent on this capture. This is an injected persistence seam, not a generic
schema registry, new byte store, Workflow branch in ToolExecutor or second Agent loop.

Stage 7 durable Workflow payloads use the value-sensitive profile of the existing redaction owner:
Definition prose, TaskContract, TextResult, change/test outputs and metadata/excerpts. Ordinary
prose containing words such as `password`,
`credential` or `authorization` remains publishable. A high-confidence credential in Definition or
TaskContract input is rejected before immutable publication/Workflow creation. In outputs, actual
secret material or content that cannot safely be stored completely becomes a bounded redacted
manifest/reference with `content_complete=false` (or fails only when the declared schema genuinely
requires the exact unsafe bytes); raw secret bytes are never persisted. A conservative vocabulary
match must not turn legal work into a Workflow-wide availability failure. Existing non-Workflow
Artifact callers keep their current behavior.

Profile coverage is stated precisely, because "every text boundary" is wider than the two durable
envelopes that carry the persisted discriminator: tool error details, cancellation/approval reasons
and public diagnostics produced on a Workflow execution path, plus Workflow-path RecoveryReport
text, apply the same value-shaped detection (a Workflow node's command legitimately returns text
such as `401 authorization failed`, and a legacy hard refusal there must not corrupt error
persistence). Subplan 2 audits every remaining `refuse_secret_material` call site reachable from
Workflow execution and documents each decision; boundaries that never carry Workflow-controlled
text (learning preview, backup manifest, provider-runtime subtree, Session fork reason) keep their
current legacy behavior unchanged.

The Direct/Workflow divergence is recorded rather than hidden: ordinary Direct does not scan user
messages before ConversationLog persists them, so secret-shaped task text can run in Direct while
Workflow Start rejects it before the durable TaskContract Artifact exists; and where legacy Direct
hard-refuses an outcome containing benign vocabulary, the Workflow output path degrades to a
redacted incomplete projection instead. The Workflow rules are stricter on input and more tolerant
on output, deliberately: input rejection prevents durable secret storage, output degradation
preserves terminal truth. A task that legitimately must contain secret-shaped text (for example,
verifying that an API rejects a token) uses ordinary Direct, not a Workflow. Whether
ConversationLog itself gains a secret policy is out of Stage 7 scope and recorded as future work.

TestReport does not depend on command-output Artifact availability. When that optional Artifact is
available it is referenced; when the existing best-effort command-output publication is absent, the
report still preserves the durable ValidationFact/exit status with `output_ref=null`,
`content_complete=false` and a bounded omission reason. A missing copy of output text is not a
missing validation result and does not block an otherwise truthful node.

An `ImplementationPatch` contract may be declared complete only when every persistent workspace
write passes an existing capturable mutation boundary. The built-in Workflow Coder therefore uses
the current native sandbox for `bash`: command writes remain in its snapshot and reach the workspace
only through structured `promote_sandbox_changes`, which the same capture records. Structured
edit/write tools remain directly capturable. For a Workflow Coder, Host-mode `bash` is treated as an
uncapturable persistent-write path even if its current generic effect metadata says `NONE`; it
cannot satisfy a complete ImplementationPatch contract; compile/preparation rejects that pairing or
the node must declare only an incomplete structural result. Stage 7 never guesses a whole-workspace
diff after the fact. Ordinary Direct/other Workflows without that output claim keep their existing
legal Host behavior.

### 4.6 Execution and recovery

- the compiler performs deterministic structural/reference/contract/capability checks only;
- the compiler enforces the conversation-scope shape above: every Stage 7 graph is all-`isolated`;
  the one-node `invoking_session` Direct shape is added by the Subplan 7 adapter and is never legal
  in a multi-node graph;
- the scheduler admits nodes in stable order and calls the existing Agent preparation/loop path;
- one Stage 7 NodeRun owns one AgentRun/Turn. Workflow leaf composition does not attach the ordinary
  steering/follow-up queue; external control is cancel/recovery/abandon. AgentLoop may still replan
  locally across its normal model/tool rounds within the frozen Node Contract. Ordinary Direct
  steering remains unchanged, while graph-level steering/Replan belongs to Stage 8;
- scheduler-level automatic and node-level retry are both absent; existing model-layer retry remains
  owned by the existing Agent runtime. A user-triggered full rerun creates a new WorkflowRun against
  an explicitly selected immutable Revision; completed NodeRuns are never overwritten;
- cancel stops new admission and delegates active cancellation to existing Agent/Tool controls; it
  does not pretend to roll back completed side effects;
- Stage 7 execution is foreground. The process/caller that owns `workflow run` holds the live
  cancellation handle; Ctrl-C or same-process application cancellation drives the fixed cancel
  mapping. Stage 7 does not expose a standalone cross-process `workflow cancel` command that could
  claim success while a Tool still runs. Durable remote/background cancel requests are Stage 9;
- any declared-node failure marks the root TaskRun failed, fails the WorkflowRun and cancels every
  not-started NodeRun with an explicit `upstream_failed` or `workflow_failed` reason;
- a blocking verdict from a result-driving required ReviewReport is a successful graph execution
  with Workflow result `needs_revision`, not a failure: the WorkflowRun is completed, the root
  TaskRun reaches `READY_FOR_ACCEPTANCE` exactly like a succeeded run, and the Workflow result
  snapshot carries the ReviewReport reference plus the fixed `completion_basis` fact
  `workflow_result=needs_revision` so monitoring, SLO accounting and learning never read a negative
  business verdict as an execution failure. The user then either accepts the outcome or resumes the
  root `READY_FOR_ACCEPTANCE -> OPEN` and explicitly starts a new full WorkflowRun. It is not an
  exception route or a hidden repair loop;
- for an all-isolated Scheduler-owned Workflow, `completed/succeeded` and `completed/needs_revision`
  both move the root TaskRun to the existing `READY_FOR_ACCEPTANCE`; cancel/failure/abandon delegate
  to the corresponding existing root Task command. A blocked Workflow leaves the root Task open. The
  Subplan 7 Direct adapter lets the existing TurnLifecycle perform that root transition, so the
  Scheduler never writes it twice. Every completed Workflow finalization also writes one versioned
  root `TaskOutcome(trigger=SNAPSHOT)` carrying the bound TaskContract as goal evidence and the
  exported result Artifact refs. A typed `WORKFLOW_RUN` evidence ref with reserved role
  `workflow_result_snapshot` marks it, and an existing typed `TASK_TRANSITION` ref with reserved role
  `workflow_ready_transition` binds it to the exact transition that produced the current
  `READY_FOR_ACCEPTANCE`; this snapshot is not acceptance and cannot enqueue LearningReview;
- for a failed run, reusing the same root Task requires the existing explicit `FAILED -> OPEN` root
  Task resume before a new WorkflowRun can be created; a `needs_revision` run instead leaves the
  root READY, where the existing accept-or-resume choice applies. Workflow `resume` never means
  rerun of a terminal failed WorkflowRun;
- recovery never reruns completed nodes. `resume` may proceed only after the existing Recovery
  service has reconciled or resolved an unknown Tool outcome; it cannot clear `blocked` by itself. A
  blocked run with no pending terminal intent is the ordinary crash path and may then continue. A
  blocked run carrying `pending_terminal_intent=user_cancel` never resumes queued work: after outcome
  resolution, one idempotent transaction closes the active Node from durable facts (or cancelled if
  it did not complete), cancels queued Nodes, closes Workflow as `cancelled(reason=user_cancelled)`
  and delegates root `CANCELLED`. If outcome remains unknown it stays blocked or may be abandoned.
  recovery-only `abandon` accepts only an OCC-current durable `blocked` WorkflowRun. It preserves the
  blocked NodeRun, unknown evidence and side effects without requiring reconciliation, cancels queued
  nodes, closes the WorkflowRun as `cancelled(reason=abandoned)` and delegates the root TaskRun
  transition to the existing `ABANDONED` command. Running/queued work is rejected and directed to
  owning foreground cancellation. If this process still holds an exact live handle for the Run, it
  rejects; it never infers liveness from a missing terminal row or PID absence;
- within one Scheduler-managed WorkflowRun/frontier, Writer nodes are always serialized. Other
  processes, ordinary Direct Sessions and separate WorkflowRuns remain protected by existing file
  revision/conflict checks; Stage 7 does not claim a global workspace lease;
- `access_mode=read` is an enforceable capability ceiling, not a role label, and Stage 7 enforces it
  entirely serially: capability resolution removes Host `bash`, write/edit/config/promotion tools and
  unknown/opaque or undeclared MCP effects. A read leaf may retain `bash` only when preparation
  freezes the existing native sandbox, omits promotion, denies external effects through existing
  policy and discards snapshot mutations; this supports read/test commands without granting a
  workspace write. If that backend is unavailable, an optional bash declaration is removed and a
  required one fails only that node. Runtime ToolSet/effect/isolation drift is a target-node
  preparation failure, never silent execution with wider authority. Bounded read-only parallel
  admission, concurrency slots and the per-request budget-claim ledger are Stage 8 work with their
  own entry conditions; Stage 7 admits one node at a time in stable order and never runs a Writer
  concurrently. Writer worktrees and distributed leases are not Stage 7 work.

### 4.7 Start command and fixed terminal mapping

`StartWorkflowCommand` requires workspace ID, command ID, Workflow Definition ID, exact immutable
Revision ID, invoking active/healthy Session ID, the exact current `purpose=user` root TaskRun ID in
`OPEN` state with expected row version, one bounded TaskContract payload, and—once the Subplan 7
adapter exists and the graph uses `invoking_session`—a distinct client-message ID. The Revision must
belong to the Definition, the Workflow head must be enabled, every referenced AgentDefinition head
must be enabled, and neither the Revision nor any referenced exact AgentDefinitionVersion may carry
a revocation record. It never chooses an implicit current task, creates a Task, abandons a
different OPEN/READY task or resumes a failed Task. Callers use the existing explicit TaskService
command first; Start then verifies `session.current_task_run_id == root_task_run_id`. Direct passes
the same TaskContract text exactly once to existing TurnLifecycle under the supplied client-message
ID; command replay and Turn replay are tested independently.

The Start transaction also requires the invoking Session/root to be durably idle: no open Turn and
no nonterminal AgentRun may already own it. It rejects a second nonterminal WorkflowRun for the same
root Task. This check and ordinary Turn admission's active-Workflow check form a bidirectional
transactional exclusion; concurrent Start/ordinary Turn attempts have one winner. At Direct
leaf admission, TurnLifecycle rechecks in its own transaction that the exact root ID/version is
still current/open and that the supplied WorkflowRun/client-message association matches; it never
falls back to whatever Task became current after Start.

Start first probes a request-digest-bound command receipt. For a new request, the existing Artifact
publisher makes TaskContract available under a deterministic `(command_id, workflow_input)` identity;
the value-sensitive input projection runs before reserve/publish, so benign security vocabulary is
preserved and high-confidence credential material fails Start before any Workflow row is created.
matching AVAILABLE state reuses. For matching STAGING, the helper compares the deterministic
expected bytes/hash/provenance: matching final bytes finalize; missing final bytes are safely
rewritten through the existing Artifact filesystem owner and then finalized; conflicting metadata
or bytes report corruption/conflict without overwrite. The same command ID with different content
conflicts. One Operational Store transaction rechecks the receipt digest and every mutable Start
admission fact: Session health/current root; root purpose/status/expected row version; absence of an
open Turn, nonterminal AgentRun and nonterminal WorkflowRun; Revision-to-Definition membership; the
Workflow Head's current enabled gate; the enabled gate of every referenced AgentDefinition head; and
the absence of revocation records for the exact Revision and referenced Versions. It then freezes
`started_at` and
`admission_deadline_at` from the injected clock/Revision duration and records the receipt,
`WorkflowRun(status=running)`/input binding and one attempt-1 `queued` NodeRun for every frozen graph
node. A
publication failure can leave only an unbound immutable Artifact, never a runnable partial Workflow.
`conversation_session_id`, `leaf_task_run_id` and
`agent_run_id` are nullable while queued and are bound atomically when that existing NodeRun moves
to running; admission never creates a second NodeRun. Ordinary head disable never reaches an
admitted Run; node admission and recovery resume instead check the frozen exact
AgentDefinitionVersion and Revision against revocation records, and a revoked version closes the
Run through the fixed `policy_revoked` mapping rather than failing it as an ordinary execution
error.

| Trigger | Active/current NodeRun | Other queued NodeRuns | WorkflowRun | root TaskRun |
|---|---|---|---|---|
| current node required outputs valid | `completed` | continue/none | `completed/succeeded` only after every declared node completes + Workflow result snapshot | all-isolated: `READY_FOR_ACCEPTANCE`; invoking-session Direct (Subplan 7): existing TurnLifecycle result |
| result-driving required ReviewReport has blocking verdict | Reviewer `completed` | continue under normal dependencies | `completed/needs_revision` only after every declared node completes | `READY_FOR_ACCEPTANCE` + result snapshot referencing the ReviewReport with `workflow_result=needs_revision` |
| model/Provider/preparation/output-contract failure with no unknown side effect | `failed` | `cancelled` with explicit cause | `failed` | `FAILED` + partial evidence |
| zero agent-generation capacity or deadline exceeded before Node admission | not-started node `cancelled(reason=budget_exhausted\|deadline_exceeded)` | all `cancelled` | `failed` | `FAILED` + partial evidence |
| agent-generation capacity/deadline expires after Node start, before its next generation request | active node `failed(reason=budget_exhausted\|deadline_exceeded)` after any active Tool safely settles | all `cancelled` | `failed` | `FAILED` + partial evidence |
| revocation of the frozen Revision or a referenced exact Version before a node's admission | not-started node `cancelled(reason=policy_revoked)` | all `cancelled` | `cancelled(reason=policy_revoked)` | `CANCELLED` + revocation evidence |
| unresolved Tool outcome | `blocked` | remain `queued` | `blocked` | remain `OPEN` |
| explicit cancel, active Tool settles safely | active node `cancelled` | all `cancelled` | `cancelled(reason=user_cancelled)` | `CANCELLED` |
| cancel leaves unresolved Tool outcome | `blocked` | remain `queued` | `blocked` | remain `OPEN` |
| recovery resolves a blocked `pending_terminal_intent=user_cancel` | close from durable leaf fact or `cancelled` | all `cancelled` | `cancelled(reason=user_cancelled)` | `CANCELLED` |
| abandon blocked run | blocked evidence unchanged | all `cancelled` | `cancelled(reason=abandoned)` | `ABANDONED` |

Temporary Provider/MCP unavailability before side effects is a node failure, not `blocked`;
`blocked` is reserved for existing recovery classification with unresolved/unknown side effects.

Root/Workflow terminal publication must not release the root while the paired state is partial. For
All-isolated Scheduler-owned success, Node/Workflow terminal facts, root `READY_FOR_ACCEPTANCE` transition and the
Workflow result snapshot are one Operational Store application transaction; its failure/
cancel/abandon (and pre-Turn Direct failure) likewise commits the Node/Workflow terminal, root
terminal transition and existing terminal TaskOutcome in one transaction. Once a
Direct Turn is admitted, existing TurnLifecycle remains the sole root transition owner: its output
binding/root terminal commit lands first while the Workflow stays nonterminal (so the active-
Workflow guard still owns the root). For STOP/success only, an idempotent finalizer writes the marked
Workflow result snapshot and closes the Workflow in one transaction. For ERROR/CANCEL or output-
committer failure, it closes Node/Workflow from the durable Turn/Agent terminal and preserves the
existing terminal TaskOutcome without inventing a result snapshot whose required outputs do not
exist. Every TaskOutcome produced by this exact Workflow-bound Direct lifecycle (STOP, ERROR,
CANCEL or committer failure) uses the internal Workflow text-safety profile; ordinary Direct remains
legacy-strict. Recovery may
finish that finalizer from the durable root/Agent terminal without rerunning the node. Start-time
validation before any Workflow row is created leaves the root OPEN; preparation/admission failure
after creation but before the Direct Turn exists uses the Workflow lifecycle transaction above.

Acceptance gains one new, narrow evidence carry-forward input; this is new assembly logic, not a
tweak of an existing lookup. Today's acceptance assembler rebuilds solely from durable turns, tool
executions and transitions and never consults prior snapshots. The added input finds the root's
exact latest transition into
`READY_FOR_ACCEPTANCE`, then selects a prior SNAPSHOT containing both the typed
`WORKFLOW_RUN/workflow_result_snapshot` marker and a matching
`TASK_TRANSITION/workflow_ready_transition` ref. Only that snapshot's Artifact refs are merged into
the new accepted TaskOutcome. An intervening ordinary snapshot is ignored; if Workflow success is
followed by resume and an ordinary Direct answer, the newer READY transition prevents stale Workflow
evidence from leaking into acceptance/LearningReview. Acceptance preserves an existing Turn goal
when present; an all-isolated root with no Turn inherits the matching snapshot's TaskContract Artifact as its
goal. Only when this matching typed Workflow marker is inherited does the existing acceptance
assembler select `workflow_value_sensitive`; an ordinary acceptance with no matching marker remains
legacy-strict. TaskService does not query Workflow tables, parse summary text or introduce a second
outcome model.

For all-isolated Scheduler-owned leaves, root TaskOutcome assembly cannot rely on root turns/executions. The
Workflow lifecycle builds one bounded deterministic evidence projection in stable Node order from
the exact NodeRun -> leaf TaskRun/AgentRun/ToolExecution links, bound Artifacts and recovery facts,
then passes it to the existing TaskOutcome owner in the same terminal transaction. This supplies
goal/result/partial change, validation, side-effect and unresolved evidence for success,
`needs_revision`, failure, safe cancel and blocked-to-abandon without making TaskService scan
Workflow tables or adding an LLM/outcome subsystem. That projection uses the same Stage 7
value-sensitive text mode: ordinary security vocabulary remains legal, and actual credential values
are redacted/omitted with `completion_basis` fact `workflow_evidence_redacted=true` rather than
preventing root terminal closure.

## 5. Persistence and module ownership

| Boundary | Target ownership | Constraint |
|---|---|---|
| Agent contracts | `src/morrow/core/agent_definitions.py` or a focused package | no YAML, SQLite, Provider IO or AgentLoop logic |
| Workflow contracts | `src/morrow/core/workflows/` | immutable domain types and state transitions only |
| Definition source adapters | focused modules under `src/morrow/adapters/state/` | YAML/OCC desired source only; no published pointer or execution |
| Version/run persistence | focused Operational Store journals and sequential migrations | immutable Agent versions, Workflow revisions/heads/runs; no permanent dual readers |
| Compiler/publication | `src/morrow/application/workflows/compiler.py` plus a thin application service | pure compiler has no IO; service owns one transactional publication path |
| Execution | focused services under `src/morrow/application/workflows/` | compose existing AgentRun preparation/AgentLoop ports |
| Runtime leaf | existing `runtime/agent.py`, `runtime/session.py` | unchanged leaf loop and Session log ownership; no graph branches |
| Terminal output commit | existing TurnLifecycle application transaction + ArtifactService | optional role-neutral Workflow binding participant before terminal/root transition |
| Change output capture | existing mutation result + durable tool persistence + ArtifactService | optional required capture for Workflow Writers; deterministic Artifact identity, no second byte store or swallowed failure |
| Interfaces | focused Workflow CLI/query modules | call application services; never read SQL/YAML directly |
| Verification | focused `tests/test_stage7_*` plus existing regressions | scripted Providers and injected synchronization only |

Every durable datum has exactly one writer; components observe through read models and commands,
never by writing a peer's state:

| Data / state | Sole writer |
|---|---|
| AgentDefinitionVersion / Head | AgentDefinitionPublicationService |
| WorkflowRevision / Head | WorkflowCompilationService |
| Revocation records | the same publication services, as additive audited rows |
| WorkflowRun / NodeRun state | WorkflowTransitionService |
| Node typed results and result refs | NodeResultCommitter, acting on durable submission facts |
| Artifact bytes and digests | ArtifactService |
| AgentRun lifecycle | the existing AgentRun/AgentLoop chain |
| root TaskOutcome | WorkflowOutcomeFinalizer delegating to the existing TaskOutcome owner |
| Workspace change manifests | ChangeArtifactCapture at the tool handler-completion boundary |
| Budget admission/settlement | the Workflow budget admission seam over durable `purpose=agent` rows |
| CLI/query modules | call application services only; no repository or YAML access |

Cross-owner rules that keep the matrix honest: NodeResultCommitter never bypasses
WorkflowTransitionService to mutate NodeRun state; AgentLoop never learns about WorkflowRun;
WorkflowScheduler never writes Artifacts directly; ChangeArtifactCapture and `submit_node_result`
publish only through ArtifactService; CLI contains no business state machine.

Human-editable sources use existing versioned typed document patterns. Their two workspace YAML
documents are explicit current-backup bundle inventory and restore/doctor inputs, including
desired-ahead-of-published content. Full immutable Agent versions, Workflow revisions/published
heads, runs, attempts and Artifact bindings live in the Operational Store. Credentials remain in
the CredentialStore; Artifact bytes remain in the ArtifactStore. Backup and doctor extend their
current owners rather than introducing a Workflow backup subsystem.

Backup/verify/restore treat the two desired-source YAML files as bounded raw bytes plus path/hash.
Because the current parsed `BackupYamlEntry` cannot truthfully represent malformed source, Subplan 1
adds one additive `DEFINITION_SOURCE` file kind/reference for exact whitelisted workspace paths;
Subplan 2 reuses it for Workflow source. It has no schema/revision fields and is not a new backup
subsystem. These entries do not parse before copying or restoring. A malformed desired draft is reported by validate/
doctor but cannot prevent backup, restore, startup, use of the last valid published head or ordinary
Direct. The existing high-confidence raw credential-literal refusal may run without YAML parsing;
generic sensitive vocabulary/key names and syntax errors are not backup gates, while an actual
detected credential still fails that backup with a scoped actionable error and never affects runtime
availability. This is a small correction to the existing backup inventory, not a second source store.
When database/published-head integrity is intact, malformed unpublished desired source is a
definition-local doctor warning and overall health remains OK; only authoritative published
reference/hash/integrity damage is an error/needs-repair condition.

Two legacy consumers sit downstream of records that may now legally carry `workflow_value_sensitive`
content, and both keep their existing policy without gaining Workflow knowledge:

- the backup manifest's legacy raw-text refusal runs over the canonical manifest JSON. Manifest
  projections of Artifact/Outcome records carry identity/path/hash facts only and never embed
  Workflow-profile excerpt or payload text, so a benign security word stored under the Workflow
  profile cannot fail a backup, and a genuinely unsafe value is already absent from durable
  content before backup runs;
- an accepted root TaskOutcome produced by a Workflow still enters the existing LearningReview
  path. Learning keeps its own legacy safety classifier: it may truthfully skip or redact a
  candidate, but benign Workflow vocabulary in an accepted outcome must not raise NEEDS_REPAIR,
  crash review creation, or block acceptance.

## 6. Sequential subplans

The nine children form four gated phases: 7A contracts (1–3), 7B reliable serial execution (4–5),
7C Multi-Agent semantics (6), and 7D productization (7–8), each closing with its own gate before the
next phase starts.

| Order | Phase | Subplan | Result |
|---|---|---|---|
| 1 | 7A | Agent Definition Foundation | minimal versioned definitions with declared tool requirements, enable/disable plus emergency revocation semantics, AgentFactory composition and the isolated conversation-scope seam |
| 2 | 7A | Workflow Revision and Artifact Contracts | immutable Workflow/Node/Run domain, root/internal-leaf Task ownership, split output-necessity semantics, typed Artifact contracts, persistence, backup/doctor integrity |
| 3 | 7A | Deterministic Workflow Compiler | canonical pure compile path; pure validate versus explicit publish; tool-requirement merge; single-component and capability checks |
| 4 | 7B | Isolated Workflow Vertical Slice | one-node isolated WorkflowRun end to end through the single WorkflowScheduler/transition/committer/finalizer path, with cancellation and recovery evidence |
| 5 | 7B | Serial DAG Scheduler | stable multi-node dependency execution on the same Scheduler, aggregate budget, failure/cancel/resume/recovery/abandon semantics |
| 6 | 7C | Serial Multi-Agent Artifact Pipeline | ChangeArtifactCapture gate first, then the typed submission protocol and Explorer -> Coder -> Reviewer with truthful `needs_revision` |
| 7 | 7D | Direct Invoking-Session Adapter | opt-in one-node `invoking_session` shape on the same Scheduler with ordinary-Direct parity evidence |
| 8 | 7D | Workflow Management and Templates | application commands/queries, CLI, four built-in static templates, doctor completion and separately authorized additive events if approved |
| 9 | — | Stage 7 Acceptance and Closeout | deterministic integrated acceptance, Direct comparison, truthful promotion evidence and documentation sync |

Child contracts are in `.agent/subplans/1-*.md` through `9-*.md`. Subplan 1 is completed and
integrated; Subplan 2 is next but not active. Later children remain pending and may be corrected by
verified earlier implementation facts. Bounded
read-only parallelism is no longer a Stage 7 child; its design (per-request budget claim, entry
conditions) lives in the Stage 8 roadmap.

## 7. Checkpoints and fallback policy

- After Subplan 3 (phase 7A gate), the contract layer must stand alone: identical input produces an
  identical candidate digest, `validate` is provably write-free, publication is idempotent, an exact
  Revision is independently recoverable, and a missing required tool fails at compile time. Do not
  start execution work on an unproven contract base.
- After Subplan 5 (phase 7B gate), the single Scheduler path must be reliable before any role
  semantics: nodes are never re-executed, results are never double-committed, crash before/after
  Artifact publication recovers, downstream nodes see only fully committed results, and one-node and
  multi-node graphs share the same scheduler/committer/finalizer code path.
- After Subplan 6 (phase 7C gate), the serial Explorer -> Coder -> Reviewer path must be reliable,
  with a Coder's real workspace mutations matching its captured change evidence and a blocking
  Reviewer verdict never recorded as an execution failure.
- After Subplan 7, the Direct adapter must prove ordinary-Direct parity; if not, repair the adapter
  rather than adding compatibility layers. After Subplan 8, freeze the application projection needed
  by CLI; Stage 8 GUI must later reuse it.
- The fixed whole-graph failure mapping combined with full-rerun semantics is a deliberate but real
  cost: any declared-node failure fails the Workflow, and a user rerun creates a new WorkflowRun
  that re-executes every node from attempt 1 — a failure at node 9 of 10 discards the Provider cost
  of the 8 completed nodes. Templates therefore keep graphs small and upstream nodes cheap, Subplan
  9 records observed rerun cost in the comparison evidence, and the Stage 8 entry conditions keep
  child-run continuation (rerun-from-failure without re-executing completed work) as the
  highest-priority orchestration follow-up, ahead of read-only parallelism.
- A malformed Workflow never disables the application or existing Direct path.
- Lack of model/provider quality benefit prevents a template from becoming recommended/default; it
  does not invalidate a correctly functioning static Runtime.
- Lack of Live-test authorization does not block Subplans 1–8 or deterministic engineering
  closeout. It leaves real-provider promotion evidence explicitly unavailable.

## 8. Validation strategy

Every production subplan runs focused deterministic tests plus:

```bash
uv run ruff format --check .
uv run ruff check .
uv run python -m compileall -q src tests
git diff --check
```

Run `uv run pytest -m 'not live'` at the persistence/runtime integration checkpoints in Subplans 2,
4, 5, 6, 7 and 9. Subplan 8 also runs the relevant CLI help smoke commands. A child may narrow its
iteration loop to touched tests but cannot claim completion without its declared gate.

Use Scripted/Fake Providers, deterministic cancellation and barriers/events. Do not assert
parallelism or recovery with wall-clock sleeps. Live evaluation, if separately authorized, is
reported independently from offline correctness.

The value-sensitive detection rule (§3) is calibrated by one dedicated focused test set owned by
Subplan 2 and extended by later consumers: benign security vocabulary positives (for example
`password_validation.py`, `authorization test passed`, credential-free prose) and actual credential
negatives (recognized token literals and explicit non-placeholder secret assignments) across input
rejection, output redaction/`content_complete=false`, rehydration and profile round-trip. Per-subplan
positive/negative cases remain required; this set exists so detection regressions are caught in one
place rather than inferred from scattered Workflow tests.

Final engineering gate:

```bash
uv run pytest -m 'not live'
uv run ruff format --check .
uv run ruff check .
uv run python -m compileall -q src tests
uv run morrow --help
uv run morrow agent --help
uv run morrow workflow --help
git diff --check
```

## 9. Git, recovery and publication discipline

- Create every production child branch from the latest verified `main` with the branch named in its
  file.
- Commit coherent verified progress; no `wip:` commit remains in merged history.
- Close a child only after its declared validation, execution-state update, fast-forward merge,
  topic ancestry verification and clean branch/worktree removal.
- The outgoing child-plan sequence 36–100 remains archived and is never renumbered or reactivated.
- Local `main` currently has commits not published to `origin/main`. The attempted push was rejected
  because explicit authorization for the exact GitHub remote/default branch was absent. Local Stage
  7 work may proceed, but remote completion must remain a recorded blocker until the user explicitly
  authorizes pushing `main` to `https://github.com/Maxuix/morrow-agent.git`.

## 10. Cross-cutting invariants

1. Session-owned `ConversationLog` remains the only chat-history authority; only the owning leaf
   `AgentLoop` appends.
2. `AgentLoop.run_task()` remains the ordinary leaf loop and contains no Workflow role/DAG branch.
3. Workflow definitions, prompts, Skills and Artifacts cannot grant capabilities or permissions.
4. Every AgentRun freezes its exact Definition, Provider, Model, Skill, ToolSet, Preference,
   Context and permission evidence.
5. Every WorkflowRun freezes one immutable revision; later definition edits do not change it.
6. Nodes collaborate through explicit Artifact contracts, not shared hidden chat state.
7. Existing ToolExecutor approval, budget, cancellation, audit and recovery semantics remain the
   only tool-execution path.
8. Completed NodeRuns and immutable Artifacts are never overwritten or silently relabelled.
9. Writers are serialized within each Stage 7 Scheduler-managed WorkflowRun; existing file
   revision/conflict handling remains responsible for external processes and other runs. Stage 7 has
   no concurrent admission at all; parallel admission is Stage 8 work requiring a provably read-only
   frozen effective ToolSet.
10. No secret, reasoning, complete tool arguments/results, raw SDK object or traceback enters
    definitions, revisions, events, logs, YAML or terminal diagnostics.
11. Usage/cost unavailability and model-quality failure remain truthful outcome facts, not safety
    failures. A blocking review verdict is a successful `needs_revision` result, never an execution
    failure.
12. Valid Direct and unrelated definitions remain runnable when one Workflow definition/run is
    invalid, unavailable or blocked.
13. A structured node result is authoritative only as a durable `submit_node_result` submission
    validated against the frozen Revision contract; the final Assistant message is transcript and is
    never parsed for structured data.
14. Ordinary head disable never mutates or blocks an already-admitted WorkflowRun; emergency
    revocation is the only mid-run stop, and it is additive, audited and one-way.
15. `validate` is always write-free; only explicit `publish` (or an explicit `--ensure-published`
    run) creates a Version/Revision or advances a head.

## 11. Non-goals

- Task-specific GraphPlanner, automatic template selection, GraphPatch, runtime revision change,
  Artifact invalidation propagation, pause/drain Replan protocol or nested dynamic subgraphs.
- GUI, HTTP/WebSocket server, background/periodic Worker or cross-instance execution.
- Cross-process cancellation of an active foreground Workflow; use the owning run process in Stage
  7, and add durable remote cancellation with background execution in Stage 9.
- Conditional/loop graph DSL, approval nodes, generic converters, automatic Reviewer -> Coder loop
  or scheduler-owned model reasoning.
- Steering/follow-up turns inside one Workflow NodeRun; Stage 7 Workflow control is cancel,
  recovery, abandon or a new full Run.
- Any concurrent node admission. Bounded read-only parallelism (fan-out frontier, per-request budget
  claim, visibility barriers) is deferred to Stage 8 with its own entry conditions; Stage 7
  parallelism of any kind — including parallel Writers, Git worktree orchestration, distributed
  locks, compensation transactions or transparent retry after side effects — is out.
- Generic policy/schema/plugin/event/tracing platforms, dependency injection framework rewrites or
  a second chat-history store.
- Default Multi-Agent routing before comparative evidence demonstrates value for a task class.

## 12. Completion condition

Stage 7 engineering is complete only when Subplans 1–9 are verified and integrated, Direct remains
available and behaviorally stable, static serial Workflows survive cancel/recovery without rerunning
completed work, the opt-in Direct invoking-session adapter proves parity, CLI/query projections are
complete, the full offline gate passes, and architecture/roadmap/acceptance documents describe only
implemented facts. Bounded read-only parallelism is a Stage 8 entry item and is not part of this
completion condition.

Comparative evaluation must be run and recorded with the evidence available. A Workflow template
may become recommended/default only after it demonstrates clear value for its target task class.
No observed benefit or unavailable Live/cost evidence keeps that promotion disabled; it does not
misclassify a correct Runtime implementation as unsafe or unusable.
