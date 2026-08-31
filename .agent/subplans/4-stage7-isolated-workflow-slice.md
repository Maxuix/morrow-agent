# Subplan 4 — Stage 7 Isolated Workflow Vertical Slice

> Status: pending
> Branch: `feat/stage7-isolated-workflow-slice`
> Prerequisite: Subplan 3 completed, verified and integrated
> Revised 2026-08-31 per the conditional-GO plan review: the first execution slice is a one-node
> isolated graph on the single unified Scheduler — no Direct-specific runner and no TurnLifecycle
> surgery before the serial path is stable.

## Objective

Run one compiled isolated node end to end through WorkflowRun -> NodeRun -> AgentFactory -> the
existing AgentRun/AgentLoop -> Artifact/TaskOutcome, on the single WorkflowScheduler /
WorkflowTransitionService / NodeResultCommitter / outcome-finalizer path that every later graph
shape reuses. Keep this path opt-in; do not replace ordinary chat and do not build a temporary
single-node engine that later diverges.

## Ownership

- the Workflow application execution services under `src/morrow/application/workflows/`:
  WorkflowScheduler, WorkflowTransitionService and the Workflow outcome finalizer;
- thin composition hooks in bootstrap/application boundaries;
- Workflow/Node read projections needed by the vertical slice;
- one-node cancellation/recovery integration through existing Agent/Tool services;
- one optional role-neutral terminal-result committer seam used only by Workflow leaves;
- one narrow idempotent Artifact publish/reuse helper over the existing ArtifactService;
- Workflow result-snapshot production and the narrow accepted-Outcome evidence carry-forward seam;
- focused isolated-slice integration tests and execution state.

## Tasks

1. Add one idempotent `StartWorkflowCommand` requiring workspace/Definition IDs, command ID, exact
   compiled Revision ID, invoking active/healthy Session ID, its exact current `purpose=user` root
   TaskRun ID in `OPEN` with expected row version, and a bounded TaskContract. Check that the
   Workflow head is enabled, every referenced AgentDefinition head is enabled, neither the Revision
   nor any referenced exact AgentDefinitionVersion is revoked, no other nonterminal WorkflowRun owns
   the root, and the Session/root has no open Turn or nonterminal AgentRun, and that
   Revision/Definition/root/Session associations match. This check and ordinary Turn admission's
   active-Workflow check are one bidirectional transactional exclusion, so concurrent Start/ordinary
   Turn attempts have one winner. It never implicitly selects,
   creates, abandons or resumes a root Task; callers use existing TaskService first. Probe a
   request-digest-bound receipt, then publish TaskContract through the existing Artifact owner under
   the Stage 7 value-sensitive input projection: benign security vocabulary remains unchanged,
   while a high-confidence credential fails before reserve/Workflow creation. Then use
   deterministic `(command_id, workflow_input)` identity; matching AVAILABLE reuses. For matching
   STAGING, compare the known deterministic bytes/hash/provenance: matching final bytes finalize,
   missing final bytes are rewritten through the existing Artifact filesystem owner and finalized,
   and conflicting bytes/metadata report corruption without overwrite. Different content conflicts.
   In the final transaction recheck the receipt digest plus every mutable Start fact: Session
   health/current root; root purpose/status/expected row version; no open Turn, nonterminal AgentRun
   or nonterminal WorkflowRun; Revision-to-Definition membership; the Workflow Head's current
   enabled gate; referenced Agent head gates; and revocation absence. Then use an injected clock to
   freeze `started_at` and
   `admission_deadline_at = started_at + revision.admission_timeout_seconds`, and transactionally
   create receipt, `WorkflowRun(status=running)`/input binding and the graph's attempt-1 queued
   NodeRun(s) in stable order — one for this slice's single node.
2. Bind/admit that existing queued NodeRun through AgentFactory and the existing preparation path,
   creating its fresh empty standalone Session plus matching internal `workflow_node` TaskRun, then
   call the same `AgentLoop.run_task()` used by ordinary execution through the internal
   leaf-lifecycle application boundary. Do not add role or Workflow branches in AgentLoop/
   ToolExecutor. Workflow leaf composition supplies the Revision node's exact frozen
   `resolved_model_ref` and never re-resolves the Definition selector or reads the current active
   model; standalone non-Workflow AgentFactory proof remains the only admission-time
   `invoking_active` resolution. Compose the AgentLoop leaf without the ordinary
   steering/follow-up queue so one NodeRun remains one AgentRun/Turn; cancel remains available and
   ordinary Direct steering is unchanged.
   At Node admission persist
   `effective_node_generation_request_cap = min(declared_node_max_agent_generation_requests,
   workflow_remaining_agent_generation_requests)` and enforce it plus the frozen
   `admission_deadline_at` at the existing durable `purpose=agent` request-admission seam. A positive
   cap runs; exhaustion or deadline expiry before a next request follows the fixed failure mapping.
   Do not interrupt an active Tool: after safe settlement the next request admission fails, while an
   outcome-unknown Tool remains blocked. Automatic compaction requests remain explicitly excluded/
   unavailable rather than falsely counted. This is the single-node seam that Subplan 5 extends
   across multiple nodes, not a second budget pipeline.
   Node admission and recovery resume check the frozen exact AgentDefinitionVersion and Revision
   against revocation records; a revoked version closes the Run through the fixed
   `cancelled(reason=policy_revoked)` mapping with audit evidence, never as an ordinary execution
   failure.
3. Pass the bound TaskContract text to the isolated leaf. Compose one optional, role-neutral
   `NodeResultCommitter` with the leaf's existing TurnSubmissionCoordinator terminal path; do not
   modify AgentLoop. After the final Assistant message is durably committed but before the leaf
   terminal transaction applies, the committer verifies every `required_for_node_completion` slot
   is satisfied from durable facts. This slice's only contract is free-text `TextResult`, satisfied
   by wrapping the committed final Assistant message (reference/digest, bounded redacted excerpt,
   `content_complete`) — the whole message is the payload, so no parsing is involved and no
   structured-submission tool exists yet. The committer publishes the Artifact under an ID derived
   from `(node_run_id, output_slot)`, finalizes/reuses staging state on replay and contributes the
   Node binding to that same terminal transaction. Only then may the leaf terminal commit. Do not
   call `complete_structured`, a repair Provider or any second model request. Publication/binding
   failure follows ordinary failure or existing Artifact/recovery classification; it can never leave
   a root READY without the exported output. Ordinary Direct has no committer. Only the root
   TaskRun produces the user-visible TaskOutcome.
   The narrow Artifact helper has four fixed cases: absent ID publishes; matching AVAILABLE
   ID/kind/hash/scope/provenance reuses; matching STAGING with verifiable final bytes uses existing
   finalize; matching STAGING with missing final bytes safely republishes the already-known expected
   bytes through the existing filesystem owner and finalizes; any identity/content mismatch or
   conflicting final bytes report corruption without overwrite. It creates no second store.
   Output projection uses that same existing value-sensitive owner rather than adding a keyword
   scanner: ordinary narrative containing words such as `password` remains legal, while actual
   secret or otherwise unsafe complete content becomes a bounded redacted manifest/reference with
   `content_complete=false` unless the declared schema genuinely requires exact bytes. Raw secret
   material is never stored, but a conservative word match cannot fail a legal terminal.
   Wire the Subplan 2 profile dispatch into the Workflow execution path: tool error details,
   cancellation/approval reasons and public diagnostics produced for a Workflow leaf select the
   value-shaped detection, so a command returning text such as `401 authorization failed` cannot
   corrupt Workflow error persistence, while ordinary Direct envelopes remain legacy-strict.
   Required-output work runs only for proposed STOP. Error/cancel terminals bypass it; a known
   Artifact failure is translated to one bounded application error so the existing AgentLoop error
   path can close the leaf as failed rather than recursively invoking the output committer.
4. Finalize NodeRun and WorkflowRun idempotently from the actual AgentRun terminal through
   WorkflowTransitionService — the sole writer of WorkflowRun/NodeRun state. Completed nodes are
   never run again on duplicate command/restart. For this all-isolated slice the Scheduler owns the
   root terminal: Node/Workflow terminal facts, the root `READY_FOR_ACCEPTANCE` transition and the
   Workflow result snapshot land in one Operational Store application transaction; failure/cancel
   likewise commit the Node/Workflow terminal, the root terminal transition and the existing
   terminal TaskOutcome in one transaction. On success and on `needs_revision` (no producer yet; the
   path is built and tested with Subplan 6's ReviewReport) the snapshot is a versioned root
   `TaskOutcome(trigger=SNAPSHOT)` containing the TaskContract as its separate `goal_reference`,
   exported result `artifact_refs`, the typed `WORKFLOW_RUN/workflow_result_snapshot` marker and a
   `TASK_TRANSITION/workflow_ready_transition` ref to the exact root transition committed by this
   transaction. The snapshot is completion evidence, not user acceptance or a LearningReview
   trigger.
   Because an isolated root has no root Turn, build the root outcome facts through a bounded
   deterministic Workflow evidence projection in stable Node order from the exact NodeRun -> leaf
   TaskRun/AgentRun/ToolExecution links, bound Artifacts and recovery facts, then pass it to the
   existing TaskOutcome owner. Apply the Stage 7 value-sensitive text mode: benign security-named
   paths/facts pass and actual credential values are redacted/omitted with fixed `completion_basis`
   fact `workflow_evidence_redacted=true` without blocking terminal closure. TaskService never scans
   Workflow tables; recovery may finish the finalizer from durable state without rerunning the node.
   Add the narrow evidence carry-forward input to accepted TaskOutcome assembly: find the root's
   exact latest transition into READY, merge refs only from a marked Workflow snapshot carrying that
   matching transition ref, inherit the snapshot's TaskContract goal only when no Turn goal exists,
   ignore an intervening ordinary snapshot, and never reuse an older Workflow snapshot after resume
   + ordinary Direct produces a newer READY transition. Only a matching typed Workflow marker makes
   the existing acceptance assembler choose `workflow_value_sensitive`; ordinary acceptance remains
   legacy-strict.
   Start validation failure before Workflow creation leaves the root OPEN; a preparation/admission
   failure after creation uses the same single lifecycle transaction to fail Node/Workflow/root and
   write TaskOutcome.
5. Reuse current cancellation and AgentRun recovery for the active leaf. A running node with an
   unresolved side effect becomes Workflow-blocked rather than being transparently retried. If that
   unknown arose from explicit cancel, persist `pending_terminal_intent=user_cancel`; after Recovery
   resolves the Tool, idempotently finish Node/Workflow/root cancellation instead of treating it as a
   crash-resume or successful result. A crash-classified block has no intent and follows ordinary
   recovery. Still-unknown remains blocked. Recovery-only abandon is implemented in Subplan 5 under
   the master handle-release invariant; this slice must not close a Run while it still owns an exact
   live handler handle.
6. Expose the minimum repository/query projection needed to inspect Revision, NodeRun, AgentRun,
   terminal Artifact and usage availability. Do not add ApplicationEvent or change the public
   AgentEvent lifecycle in this slice.
7. Keep the existing ordinary Direct command/path unchanged and default. Add an explicit opt-in
   application/test entry for the Workflow slice until all evidence passes.
8. Add Scripted Provider end-to-end tests for normal completion, model/tool failure, cancellation,
   crash/recovery at Artifact reserve/publish/bind/terminal boundaries (including reserve then
   missing final bytes), command replay, second active Workflow, pre-existing open Turn/nonterminal
   AgentRun rejection, concurrent Start-versus-ordinary-Turn and root replacement,
   invalid/implicit root selection, disabled Agent/Workflow head rejection at Start,
   revoked Version/Revision rejection at Start and at node admission with the `policy_revoked`
   mapping, admitted-Run immunity to ordinary disable (a disabled head after Start never blocks the
   frozen node), Provider/preparation failure before leaf admission,
   Workflow disable/root replacement during TaskContract publication,
   root purpose/status/version, definition drift, generic sensitive words
   in TaskContract/output versus actual-secret input rejection/output redaction, terminal/finalizer
   crash ordering and completed-node no-rerun,
   accepted-Outcome inheritance of the typed-marked Workflow result snapshot with TaskContract goal
   for a root with no Turn (including an intervening ordinary Task snapshot), rejection of stale
   Workflow refs after Workflow success -> resume -> ordinary Direct success, two-Workflow selection
   of only the current READY transition, ERROR/CANCEL no-result-snapshot behavior plus benign
   security-named path/fact terminal closure, accepted Workflow benign path/fact closure versus
   ordinary acceptance legacy behavior, cancel-unknown intent persistence/resolution versus ordinary
   crash recovery, positive/shrunken request cap, cap/deadline rejection before the next request,
   deadline expiry around a safely settled Tool, compaction exclusion truth, and proof that Workflow
   execution neither consumes nor creates an ordinary steering/follow-up turn, and
   publish-with-active-model-A followed by switching active to B still runs the old Revision with A.

## Proportionality decisions

Required now are only truthful lifecycle correlation, idempotent terminal state and reuse of current
recovery on one unified Scheduler path. This subplan must not add:

- a Direct-specific runner, a second state machine or a second Tool/permission/budget pipeline;
- the `invoking_session` conversation scope or any TurnLifecycle change (Subplan 7);
- the `submit_node_result` mechanism tool (Subplan 6 has the first structured consumers);
- public event redesign, GUI/API server, Workflow default migration or compatibility facade;
- automatic retry or compensation after side effects;
- recovery-only abandon (Subplan 5) or multi-node dependency logic (Subplan 5).

A Workflow-specific failure affects only the opt-in run. Existing Direct chat, its TaskOutcome truth
and its recovery semantics remain untouched.

## Validation

```bash
uv run pytest -q tests/test_stage7_isolated_workflow_slice.py
uv run pytest -q tests/test_agent_run_preparation.py tests/test_stage4_recovery_crash.py
uv run pytest -m 'not live'
uv run ruff format --check .
uv run ruff check .
uv run python -m compileall -q src tests
uv run morrow --help
git diff --check
```

## Exit criteria

- The opt-in isolated Workflow slice uses the same AgentLoop, ToolExecutor, permissions and
  completion truth as ordinary Direct with no additional model request, and its Scheduler,
  transition service, committer and finalizer are the single path that Subplan 5 extends — there is
  no single-node-only engine.
- Start never attaches to a non-current/non-open/internal root Task or leaves a runnable partial
  Run; it also rejects existing ordinary in-flight work, disabled heads and revoked versions;
  queued NodeRun identity is created once and admission only binds/transitions it.
- Required output is durably published/bound before the root terminal makes the root ready;
  deterministic replay repairs safe staging/binding gaps without a second model request.
- Normal, failure, cancel and crash/recovery outcomes are attributable through WorkflowRun,
  NodeRun, AgentRun and Artifacts, and the root Outcome reports leaf evidence accurately despite the
  root having no Turn of its own.
- Completed work is not rerun; unknown side effects block only the affected Workflow.
- An admitted Run is immune to ordinary head disable; revocation closes it through the audited
  `policy_revoked` mapping.
- Existing Direct remains default and its focused/full offline regressions pass.
- No multi-node dependency logic or runtime-default behavior exists yet.
