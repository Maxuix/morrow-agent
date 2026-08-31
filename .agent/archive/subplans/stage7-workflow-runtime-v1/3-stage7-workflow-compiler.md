# Subplan 3 — Stage 7 Deterministic Workflow Compiler

> Status: pending
> Branch: `feat/stage7-workflow-compiler`
> Prerequisite: Subplan 2 completed, verified and integrated

## Objective

Compile a typed static WorkflowDefinitionSource into one canonical WorkflowRevision candidate using
only the checks required to make Stage 7 execution deterministic and non-escalating. Keep the pure
compiler IO-free; one thin application command publishes a valid candidate and its SQLite head
transactionally. Neither path performs network/process IO or runs a Workflow.

## Ownership

- pure compiler contracts under `src/morrow/application/workflows/compiler.py` and one thin
  `WorkflowCompilationService`/command in the application layer;
- transactional publication through the Subplan 2 repository port and existing command-receipt/OCC
  patterns, without direct SQL in the service;
- typed definition adapters only where compilation input requires them;
- actionable compile diagnostics and read-only validation projection;
- Direct and minimal serial structural compile fixtures using only the already-published Direct/
  Explorer refs plus Subplan 2 TaskContract/TextResult contracts;
- focused compiler tests and execution state.

## Tasks

1. Make the pure Compiler the only normalization/validation/hash path. It accepts one exact desired
   document revision plus the selected definition's canonical body hash, exact published
   AgentDefinitionVersions, immutable capability catalogs, current active ModelRef snapshot and
   compiler version, then returns a typed candidate and diagnostics without allocating IDs or doing
   IO. Candidate canonical `content_hash` includes full normalized source metadata
   (name/description/tags/origin) and every execution-affecting compiled field: normalized graph/
   contracts/budget, every exact immutable Agent/Skill/Tool/Provider ref, exact `resolved_model_ref`
   and compiler version. It excludes allocated IDs/display revision, timestamps, parent lineage,
   source OCC revision/hash and operational Head state; source hash is not a substitute. Source
   `agent_definition_ref` is an exact immutable Version ref, never a mutable Head selector.
2. Make `WorkflowCompilationService` the only publication path. After a valid candidate it allocates
   the opaque Revision ID/display revision and transactionally stores the immutable Revision plus
   SQLite `WorkflowDefinitionHead` under OCC/command idempotency. The head records the compiled YAML
   source document revision/per-definition body hash; a later edit to that body appears as
   desired-ahead-of-published, while an unrelated entry edit does not. Probe the command receipt
   before compilation; same-command replay returns the same Revision even if configuration moved.
   For a new command, always compile against current inputs and only then compare candidate canonical
   compiled `content_hash` to the current published Revision. Exact match is a no-op; source-hash
   equality alone is not. Failure stores
   nothing. Publication preserves an existing head's operational enable flag; first publication is
   enabled unless that command explicitly requests disabled. Enable/disable is not compilation and
   creates no Revision.
3. Validate node ID uniqueness, edge endpoints, acyclicity over the complete declared edge graph,
   entry existence and a producer path for every declared required output. Every cross-node input
   binding must have a same-direction explicit edge; reject a missing edge instead of inferring one.
   An edge without a binding is a legal pure control dependency. Disconnected/unconsumed components that do not affect required
   outputs are actionable warnings rather than errors, but every node retained in the Revision is
   execution-required and will still be scheduled; any such node failure uses the fixed whole-graph
   failure mapping. Do not infer optional-node, skip or continue semantics from connectivity.
4. Allow `conversation_scope=invoking_session` only when the entire graph has exactly one node and
   no edges. Reject every multi-node graph containing it; all multi-node leaves must be `isolated`.
   Include one-node invoking-session Direct TextResult, one-node isolated TextResult and the closest
   two-node-invoking rejection. Record that terminal ownership is chosen by scope, not node count.
   The contract-specific Direct result-driving ReviewReport gate is added in Subplan 6 only after the
   real ReviewReport payload/producer exists; do not create a placeholder to test it here.
5. Validate exact required Artifact kind/version bindings and Workflow required output production.
   Accept exactly `TaskContract@1` as the Stage 7 Workflow input contract; reject any other
   kind/version because Start has no producer for it. Include that exact positive and adjacent
   other-kind/version rejections; do not add conversion or multi-input plumbing.
   Require node-local unique `input_name`, a strict one-source discriminator, literal `task` for the
   sole Workflow-input source, and exact equality between each binding's accepted ContractRef and
   the Workflow input or producer output contract.
   Every node-output input binding and Workflow required output must reference a producer slot
   declared `required=true`; the Workflow-input form is governed by the exact `task:TaskContract@1`
   check above. A `required=false` slot may remain an unbound observation output but cannot affect
   readiness. Include the closest legal unbound-optional case and rejection cases for both forbidden
   reference positions, plus missing-edge rejection and a control-only-edge positive case; do not add
   optional input, fallback or skip behavior.
   Also require all required result refs to fit the existing TaskOutcome `artifact_refs` capacity
   (currently 64), with exact-boundary acceptance and one-over rejection. TaskContract uses the
   separate `goal_reference`. Do not auto-insert Converter nodes, negotiate arbitrary versions,
   silently truncate required refs or invent a manifest indirection.
6. Resolve referenced immutable stored AgentDefinitionVersions and declared Provider/Model/Skill/Tool
   capabilities from current catalogs/snapshots. Resolve the two-case Definition model selector:
   preserve an exact ModelRef, or resolve literal `invoking_active` from current configuration, then
   freeze the resulting exact `resolved_model_ref` in every candidate Revision node. Missing active
   model is a scoped compile error; no ModelPolicy registry or fallback chain is introduced. Check
   reference/config legality only; do not perform Provider/MCP health probes.
7. Prove requested access/capabilities are an intersection of Workflow task policy and
   AgentDefinition ceilings. Prompts and Artifact contents are never inputs to authorization.
8. Require a finite effective Workflow run budget only for authoritative Stage 7 dimensions:
   primary `agent_generation_request_count` from existing durable `purpose=agent` admissions, a
   positive relative `admission_timeout_seconds` duration and concurrency.
   Normalize source values into Revision
   `max_agent_generation_requests`, `default_node_max_agent_generation_requests`,
   `admission_timeout_seconds` and `max_concurrency`. Freeze each positive
   `declared_node_max_agent_generation_requests = min(node override if present else Workflow node
   default, AgentDefinition run ceiling if present else unbounded)`. The Subplan 2 domain/repository
   owns these fields; this subplan only resolves and validates them and adds no budget DSL.
   Revisions never store an absolute wall-clock deadline; Start uses its injected clock to freeze
   `WorkflowRun.admission_deadline_at = started_at + duration`. Static DAG size/unique NodeRun
   identity bounds Node admissions without a second counter. Node generation-request maxima larger than the
   Workflow total are legal because runtime freezes a shrunken effective cap. Provider token/cost
   and tool-call estimates are observations/warnings, not compile gates; unavailable usage/cost
   metadata is never a failure. Automatic compaction Provider calls are explicitly outside this
   Stage 7 aggregate because the existing seam does not admit them; the schema/diagnostics must not
   call the field total model requests.
9. Enforce `access_mode=read` as a capability ceiling: remove Host `bash`, write/edit/config/
   promotion, unknown/opaque and undeclared MCP effects. Native-sandbox bash is legal only with
   promotion absent, existing policy denying external effects and snapshot mutations discarded; do
   not add command parsing or a path predictor. The Compiler checks only declared isolation/config
   evidence and does no backend availability probe. If the backend is unavailable at leaf
   preparation, optional bash is removed and required bash fails only that NodeRun; runtime
   availability never makes the immutable Definition/Revision invalid. Independent `write` nodes are legal because
   the Scheduler serializes them; emit an actionable warning. Read-only fan-out is compiled but not
   executed until Subplan 7, where frozen runtime facts must still match the compile evidence.
   The concrete complete-ImplementationPatch/Coder sandbox pairing is checked when that contract and
   Definition first exist in Subplan 6; do not invent placeholder schemas or role fixtures here.
10. Return typed diagnostics that distinguish errors from warnings. One broken definition does not
   prevent compiling/running Direct or another definition.
11. Add one rejection case and one closest legal case for every current hard gate, plus positive
   fixtures for Direct and a minimal serial isolated graph built only from Subplan 1/2 published
   refs/contracts. Prove same source compiled with active model A, then with active model B under a
   new command, produces a new B-frozen Revision while the old Revision remains A; a subsequent new
   compile with the same B-resolved candidate is a no-op, and same-command replay remains the prior
   result. Also prove moving a mutable AgentDefinition Head does not drift an exact Version ref; the
   source must explicitly select a new Version before compiled content changes.
   A name/description/tag edit produces a new metadata-preserving Revision and advances the
   published source hash; repeating that exact candidate is then a no-op.
   Explorer-Coder-Reviewer and Parallel Research compile positives belong to
   Subplans 6 and 7 after their real Definitions/contracts exist.

## Proportionality decisions

Hard errors are limited to graph indefiniteness, invalid invoking-session ownership,
required-contract mismatch, missing/corrupt immutable reference, OCC-stale update and
capability/access-mode escalation. Old immutable
Revisions remain legal to inspect and explicitly run. Disconnected/unconsumed work (which still
executes), independent Writers, temporary availability, missing cost and lack of performance benefit
are warnings/runtime facts.

Explicitly deferred:

- condition/loop language, recursive graph grammar, dynamic fan-out, automatic repair, Converter
  framework, policy DSL, cost optimizer, remote catalog refresh and generic linter/plugin systems;
- Scheduler, AgentLoop calls, GraphPatch or GUI-oriented edit commands.

## Validation

```bash
uv run pytest -q tests/test_stage7_workflow_compiler.py
uv run ruff format --check .
uv run ruff check .
uv run python -m compileall -q src tests
git diff --check
```

## Exit criteria

- Supported definitions normalize to the same canonical content/hash; command replay or unchanged
  current content does not allocate duplicate Revision identity.
- One successful publication transaction stores one opaque Revision and advances its SQLite head;
  a failed or OCC-stale publication produces no runnable Revision. YAML edits are never claimed as
  part of that transaction.
- Each hard error protects a declared Stage 7 invariant and has an adjacent legal acceptance case.
- Temporary Provider/MCP availability and unavailable cost cannot make a structurally valid
  definition un-compilable.
- Pure Compiler code performs no network, process, SQLite mutation or Agent execution; the thin
  publication service performs only repository transaction/receipt work and never duplicates
  compiler checks.
- No general-purpose graph/policy/schema subsystem was introduced.
