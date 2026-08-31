# Subplan 6 — Stage 7 Serial Multi-Agent Artifact Pipeline

> Status: pending
> Branch: `feat/stage7-multi-agent-pipeline`
> Prerequisite: Subplan 5 completed, verified and integrated
> Revised 2026-08-31 per the conditional-GO plan review: ChangeArtifactCapture is a front-loaded
> gate, structured results arrive through the authoritative `submit_node_result` submission
> protocol, and `needs_revision` is never recorded as an execution failure.

## Objective

Deliver the first real multi-Agent Workflow, Explorer -> Coder -> Reviewer, over the serial
Scheduler. Agents collaborate through typed Artifacts and isolated leaf Sessions, not through a
shared conversation or an automatic review loop.

## Ownership

- the `ChangeArtifactCapture` gate at the existing durable tool completion boundary;
- the internal `submit_node_result` submission tool and its durable submission facts;
- built-in Explorer, Coder and Reviewer AgentDefinition versions;
- only the Artifact payload contracts consumed by this pipeline: EvidenceBundle,
  ImplementationPatch/change reference, TestReport and ReviewReport;
- an Artifact-only renderer in the existing workspace mutation/sandbox-promotion owners; ordinary
  model-facing MutationResult envelopes keep their current bounds;
- bounded Artifact-to-node context rendering/application composition;
- the built-in Explore-Implement-Verify WorkflowDefinition/revision fixture;
- focused role/capability/context/Artifact/end-to-end tests and execution state.

## Tasks

1. **ChangeArtifactCapture gate (complete and prove this cluster before any pipeline assembly).**
   For Workflow Writer leaves, inject a narrow `ChangeArtifactCapture` into the existing durable
   tool handler-completion path. Add an Artifact-only renderer to the existing mutation/promotion
   owner so it can use authoritative preflight/before/after data before cleanup; do not enlarge the
   model-facing 4-KiB MutationResult diff. Publish a complete unified diff when representable;
   otherwise publish an exact structural manifest with paths, operation, before/after hashes/sizes
   and `content_complete=false`. Existing Artifact redaction/size rules remain authoritative;
   content that cannot be completely and safely represented degrades to the structural manifest
   instead of blocking a legal mutation. At the same boundary,
   recognized `ValidationFact` produces a small TestReport that references the existing durable
   command-output Artifact rather than copying output. That command-output Artifact is best-effort
   today: if it is absent, retain the authoritative ValidationFact/exit status with
   `output_ref=null`, `content_complete=false` and a bounded omission reason; absence of copied text
   is not a validation/capture failure. Use deterministic IDs derived from
   `(tool_execution_id, role, schema_version)` and attach the references to that durable execution.
   Never guess a missing diff later from the current workspace and never take an after-the-fact
   whole-workspace before/after diff as a fallback: Stage 7 holds no workspace-global lease, so such
   a diff would misattribute changes made by other processes or Sessions to this node. The native
   sandbox is the authoritative capture boundary for bash instead (task 5), and Host-mode bash is
   rejected for complete-patch contracts. Never swallow a required Workflow
   capture failure; ordinary non-Workflow tools retain their current behavior.
   Reuse the same value-sensitive mode in the current Artifact redaction/refusal owner rather than
   adding a keyword scanner. Ordinary prose mentioning `password`, `credential` or `authorization`
   must remain legal; actual secret or otherwise unsafe complete content is represented by a bounded
   redacted manifest/reference with `content_complete=false` unless the declared contract genuinely
   requires exact bytes. Raw secret material is never stored, but conservative vocabulary matching
   cannot block an otherwise valid leaf.
   Gate: deterministic write/edit/sandbox-promotion capture, replay, degradation and
   non-Workflow-unaffected tests pass before task 2 starts.
2. Add the internal `submit_node_result` mechanism tool per the master plan §4.5 contract. Leaf
   composition injects it only for Workflow leaves whose frozen Revision node declares structured
   output contracts; it is never granted by definitions/prompts/Skills, never appears in ordinary
   Direct, is not subject to definition tool declarations or approval prompts, and its only effect
   is validating and staging/publishing declared output payloads through the existing
   ArtifactService while recording a durable submission fact on its own ToolExecution row. Validate
   each submitted slot payload against the frozen Revision output contract; reject undeclared slots,
   wrong kind/version and schema violations with an in-loop tool error the model can correct.
   Exactly one valid submission exists per NodeRun: an identical replay is a no-op reuse and a
   conflicting second submission is refused without overwrite. `evidence_refs` must name
   Artifacts/ToolExecutions already durable inside this node's own scope. The submission call is an
   ordinary in-loop tool round; no separate structured-completion or repair Provider request is
   made. The natural-language final message remains transcript and is never parsed for structured
   data.
   Extend the Subplan 4 committer: a proposed successful STOP verifies every
   `required_for_node_completion` structured slot has exactly one valid durable submission, and a
   missing/invalid one becomes one bounded application error so the existing AgentLoop error path
   closes the node `failed(reason=output_contract_unsatisfied)`. Recovery completes the committer
   from the durable submission fact and deterministic `(node_run_id, output_slot)` identity without
   re-executing the node. ImplementationPatch/aggregate TestReport slots are satisfied from durable
   ChangeArtifactCapture/ValidationFact references rather than from model text; final Assistant text
   may add rationale/notes but cannot overwrite change/test facts.
3. Reuse the published built-in Explorer Version from Subplan 1 and add immutable Coder and Reviewer
   Versions through the same mechanism (or publish an explicitly changed Explorer Version if its
   actual consumer contract requires it). Declare their `tool_requirements[]` explicitly: Explorer
   and Reviewer declare read-only requirements (required read/search tools, forbidden writes), Coder
   declares its write/edit tools plus native-sandbox bash as required for its mechanism. Access is
   enforced from these declared capability facts, never inferred from role names in runtime code.
4. Create a fresh empty standalone Session and matching internal `workflow_node` TaskRun for each
   multi-Agent leaf, associated through WorkflowRun/NodeRun rather than transcript lineage. Render
   input from its Node Task Contract plus the explicitly bound root `TaskContract` and other
   Artifact summaries/bounded content. Do not include another leaf's ConversationLog or unbound
   root history. Leaf TaskRuns cannot be accepted by the user or enqueue LearningReview.
5. Run Explorer read-only to produce EvidenceBundle (via `submit_node_result`); run Coder as the
   sole Writer to produce change/test evidence; run Reviewer read-only against task, diff/change
   and tests to produce ReviewReport (via `submit_node_result`).
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
   slot is listed in the Revision's exported `required_outputs`. The root TaskRun reaches
   `READY_FOR_ACCEPTANCE` exactly like a succeeded run, and the result snapshot references the
   ReviewReport plus the fixed `completion_basis` fact `workflow_result=needs_revision`; the
   WorkflowRun `result_status` is the machine-readable distinction. No consumer (learning,
   statistics, doctor, CLI) may present it as an execution failure, and no first-class TaskRun
   status or TaskOutcome field is added. The user accepts the outcome or resumes
   `READY_FOR_ACCEPTANCE -> OPEN` and explicitly starts a new full WorkflowRun. It does not fail
   the Reviewer node, create a hidden Reviewer -> Coder loop or mutate the graph. If multiple
   exported ReviewReport refs exist, any blocking verdict drives `needs_revision`; a ReviewReport
   not in exported outputs is evidence only. Do not choose "latest".
7. Reject a node completion only when its required contract cannot be validated or no valid
   submission exists. Optional narrative detail/quality remains model outcome evidence and does not
   trigger a new safety subsystem. A materialization/submission failure marks the Node/root Workflow
   failed after preserving the internal leaf Task/Turn evidence when the side effect is known; a
   crash with unresolved effect uses the existing blocked recovery mapping. It cannot relabel a
   successful Direct root Turn.
8. Prove Artifact content cannot expand the downstream ToolSet/permission and Reviewer cannot write
   even if upstream text asks it to; prove `submit_node_result` cannot be smuggled in through a
   definition, prompt or Artifact and cannot publish undeclared slots or out-of-scope evidence refs.
9. Add a controlled offline workspace end-to-end test plus isolation tests showing distinct
   Session-owned logs, exact bindings, read/write boundaries, durable actual change capture,
   truncated/structural evidence truth, sandbox-promotion capture, Host-bash rejection with legal
   read/test-only sandbox positive case, generic-sensitive-word success versus actual-secret
   redaction, missing command-output with truthful ValidationFact/TestReport, staging/replay
   behavior and truthful review outcome, including a blocking result-driving Reviewer verdict while
   an independent declared node remains pending and is still executed, plus two reports proving only
   exact exported refs drive the deterministic any-block rule. Cover the submission protocol:
   schema-violation correction in-loop, duplicate/conflicting submission refusal, replay no-op,
   crash between submission and terminal commit completing from durable facts, and
   `output_contract_unsatisfied` closure when a required structured submission never arrives.
   Include a real Coder write followed by downstream failure and prove
   the root Outcome preserves captured Patch/Test/side-effect evidence from exact leaf links.

## Proportionality decisions

Required now are explicit handoff, an authoritative submission protocol, role capability boundaries
and conversation isolation. Deferred:

- agent-to-agent chat room, full ContextPolicy DSL, content keyword filters and a second prompt
  safety layer;
- Planner/PlanArtifact, Synthesizer/SynthesisReport, automatic reviewer repair, GraphPatch and
  model-selected roles;
- after-the-fact whole-workspace diffing (unsound without a workspace-global lease);
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
- Structured results exist only as durable validated `submit_node_result` submissions; the final
  message is never parsed, a missing required submission closes the node as
  `output_contract_unsatisfied`, and crash recovery completes the commit without re-execution.
- Required typed Artifacts and TaskOutcome accurately distinguish accepted work, model failure and
  review-needs-revision, and a blocking verdict never lands the root in `FAILED`.
- Coder patch/test outputs are derived from immutable ToolExecution-linked change/validation
  evidence captured before process-local facts disappear; no recovery path invents a diff, and no
  after-the-fact workspace scan is ever used.
- Host writes cannot falsely claim complete patch capture, while legal no-change validation commands
  do not fail merely because there is no mutation to capture.
- No automatic loop, shared chat history or dynamic plan mutation exists.
