# Subplan 6 — Stage 7 Serial Multi-Agent Artifact Pipeline

> Status: pending
> Branch: `feat/stage7-multi-agent-pipeline`
> Prerequisite: Subplan 5 completed, verified and integrated

## Objective

Deliver the first real multi-Agent Workflow, Explorer -> Coder -> Reviewer, over the serial
Scheduler. Agents collaborate through typed Artifacts and isolated leaf Sessions, not through a
shared conversation or an automatic review loop.

## Ownership

- built-in Explorer, Coder and Reviewer AgentDefinition versions;
- only the Artifact payload contracts consumed by this pipeline: EvidenceBundle,
  ImplementationPatch/change reference, TestReport and ReviewReport;
- a narrow optional change-output capture hook at the existing durable tool completion boundary;
- an Artifact-only renderer in the existing workspace mutation/sandbox-promotion owners; ordinary
  model-facing MutationResult envelopes keep their current bounds;
- bounded Artifact-to-node context rendering/application composition;
- the built-in Explore-Implement-Verify WorkflowDefinition/revision fixture;
- the narrow WorkflowCompiler extension required by the real ReviewReport terminal contract;
- focused role/capability/context/Artifact/end-to-end tests and execution state.

## Tasks

1. Reuse the published built-in Explorer Version from Subplan 1 and add immutable Coder and Reviewer
   Versions through the same mechanism (or publish an explicitly changed Explorer Version if its
   actual consumer contract requires it). Access is declared by capability facts, never inferred
   from role names in runtime code.
2. Add the minimum typed payloads and versioned bindings required by this pipeline. Reuse existing
   patch/diff/test Artifact kinds and bytes instead of duplicating content stores. For Workflow
   Writer leaves, inject a narrow `ChangeArtifactCapture` into the existing durable tool
   handler-completion path. Add an Artifact-only renderer to the existing mutation/promotion owner
   so it can use authoritative preflight/before/after data before cleanup; do not enlarge the
   model-facing 4-KiB MutationResult diff. Publish a complete unified diff when representable;
   otherwise publish an exact structural manifest with paths, operation, before/after hashes/sizes
   and `content_complete=false`. Existing Artifact redaction/size rules remain authoritative; content
   that cannot be completely and safely represented degrades to the structural manifest instead of
   blocking a legal mutation. At the same boundary,
   recognized `ValidationFact` produces a small TestReport that references the existing durable
   command-output Artifact rather than copying output. That command-output Artifact is best-effort
   today: if it is absent, retain the authoritative ValidationFact/exit status with
   `output_ref=null`, `content_complete=false` and a bounded omission reason; absence of copied text
   is not a validation/capture failure. Use deterministic IDs derived from
   `(tool_execution_id, role, schema_version)` and attach the references to that durable execution.
   Never guess a missing diff later from the current workspace or swallow a required Workflow
   capture failure; ordinary non-Workflow tools retain their current behavior.
   Reuse the same value-sensitive mode in the current Artifact redaction/refusal owner rather than
   adding a keyword scanner.
   Ordinary prose mentioning `password`, `credential` or `authorization` must remain legal; actual
   secret or otherwise unsafe complete content is represented by a bounded redacted manifest/
   reference with `content_complete=false` unless the declared contract genuinely requires exact
   bytes. Raw secret material is never stored, but conservative vocabulary matching cannot block an
   otherwise valid leaf.
3. Use the Subplan 4 `NodeResultCommitter` before the leaf Turn terminal to parse the same final
   Assistant message and deterministically build/bind EvidenceBundle, ImplementationPatch,
   aggregate TestReport or ReviewReport from already durable change/test/output references. Final
   Assistant text may add rationale/notes but cannot overwrite those facts.
   Publication is idempotent by `(node_run_id, output_slot)` and never makes a separate structured/
   repair model request or creates a generic schema registry.
4. Create a fresh empty standalone Session and matching internal `workflow_node` TaskRun for each
   multi-Agent leaf, associated through WorkflowRun/NodeRun rather than transcript lineage. Render
   input from its Node Task Contract plus the explicitly bound root `TaskContract` and other
   Artifact summaries/bounded content. Do not include another leaf's ConversationLog or unbound
   root history. Leaf TaskRuns cannot be accepted by the user or enqueue LearningReview.
5. Run Explorer read-only to produce EvidenceBundle; run Coder as the sole Writer to produce change/
   test evidence; run Reviewer read-only against task, diff/change and tests to produce ReviewReport.
   Freeze the built-in Workflow Coder's `bash` to the existing native sandbox: bash writes stay in
   the snapshot and become authoritative workspace writes only through captured
   `promote_sandbox_changes`. Direct structured edit/write remains capturable. Host-mode persistent
   bash cannot produce a complete ImplementationPatch and is rejected before handler execution for
   this contract even if current generic bash effect metadata says `NONE`; do not derive a
   replacement diff by scanning the workspace afterward. Read/test-only sandbox bash that produces
   no change fact is legal and may still emit ValidationFact/TestReport; absence of a change is not
   a capture failure.
   Compile the real Explorer -> Coder -> Reviewer definition here and prove the complete
   ImplementationPatch/Coder sandbox pairing has one legal positive case and rejects uncaptured
   Host-bash writes; Subplan 3 deliberately did not create placeholder role/contracts for this test.
6. Treat ReviewReport verdict as task/product evidence. A blocking review still completes the
   Reviewer node, does not cancel or skip another execution-required node, and only after the full
   declared graph completes sets WorkflowRun result `needs_revision` when that exact ReviewReport
   slot is listed in the Revision's Workflow `required_outputs`, transitions the root TaskRun to `failed` and
   produces a root TaskOutcome referencing the ReviewReport. It does not fail the Reviewer node,
   create a hidden Reviewer -> Coder loop or mutate the graph. A user correction is a new full
   WorkflowRun in Stage 7. If multiple required ReviewReport refs exist, any blocking verdict drives
   `needs_revision`; a ReviewReport not in required outputs is evidence only. Do not choose “latest”.
   Now that the real contract exists, extend Compiler to reject an `invoking_session` graph that
   lists a ReviewReport slot in Workflow `required_outputs`: Direct TurnLifecycle owns STOP→READY and
   cannot reinterpret it as needs-revision→FAILED. Keep non-result-driving ReviewReport evidence
   legal. Prove invoking-session Direct TextResult, one-node isolated result-driving ReviewReport and
   non-result-driving Direct report positives beside the exact forbidden case; terminal ownership is
   based on scope, not node count.
7. Reject a node completion only when its required contract cannot be validated. Optional narrative
   detail/quality remains model outcome evidence and does not trigger a new safety subsystem. A
   materialization failure marks the Node/root Workflow failed after preserving the internal leaf
   Task/Turn evidence when the side effect is known; a crash with unresolved effect uses the existing
   blocked recovery mapping. It cannot relabel a successful Direct root Turn.
8. Prove Artifact content cannot expand the downstream ToolSet/permission and Reviewer cannot write
   even if upstream text asks it to.
9. Add a controlled offline workspace end-to-end test plus isolation tests showing distinct
   Session-owned logs, exact bindings, read/write boundaries, durable actual change capture,
   truncated/structural evidence truth, sandbox-promotion capture, Host-bash rejection with legal
   read/test-only sandbox positive case, generic-sensitive-word success versus actual-secret
   redaction, missing command-output with truthful ValidationFact/TestReport, staging/replay behavior
   and truthful review outcome, including a blocking result-driving Reviewer verdict while an
   independent declared node remains pending and is still executed, plus two reports proving only
   exact required refs drive the deterministic any-block rule. Include a real Coder write followed by downstream failure and prove
   the root Outcome preserves captured Patch/Test/side-effect evidence from exact leaf links.

## Proportionality decisions

Required now are explicit handoff, role capability boundaries and conversation isolation. Deferred:

- agent-to-agent chat room, full ContextPolicy DSL, content keyword filters and a second prompt
  safety layer;
- Planner/PlanArtifact, Synthesizer, automatic reviewer repair, GraphPatch and model-selected roles;
- statistical quality thresholds as an engineering gate.

One malformed pipeline Artifact blocks only the consuming node/run. Other definitions and Direct
remain usable.

## Validation

```bash
uv run pytest -q tests/test_stage7_multi_agent_pipeline.py
uv run pytest -q tests/test_stage7_agent_definitions.py tests/test_stage7_serial_scheduler.py
uv run pytest -q tests/test_local_files.py tests/test_sandbox.py tests/test_process.py tests/test_stage4_artifacts.py
uv run pytest -m 'not live'
uv run ruff format --check .
uv run ruff check .
uv run python -m compileall -q src tests
git diff --check
```

## Exit criteria

- Explorer -> Coder -> Reviewer completes through the generic compiled serial runtime with no
  template-name branch.
- Each leaf has a distinct initially empty Session/matching internal TaskRun pair and sees only its
  contract/bindings; no parent transcript fork is used and leaf TaskRuns stay outside acceptance/
  learning.
- Explorer/Reviewer are actually read-only and Coder is the only Writer.
- Required typed Artifacts and TaskOutcome accurately distinguish accepted work, model failure and
  review-needs-revision.
- Coder patch/test outputs are derived from immutable ToolExecution-linked change/validation
  evidence captured before process-local facts disappear; no recovery path invents a diff.
- Host writes cannot falsely claim complete patch capture, while legal no-change validation commands
  do not fail merely because there is no mutation to capture.
- No automatic loop, shared chat history or dynamic plan mutation exists.
