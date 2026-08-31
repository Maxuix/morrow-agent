# Subplan 4 — Stage 7 Direct Workflow Vertical Slice

> Status: pending
> Branch: `feat/stage7-direct-workflow`
> Prerequisite: Subplan 3 completed, verified and integrated

## Objective

Run one compiled Direct node end to end through WorkflowRun -> NodeRun -> AgentFactory -> the
existing AgentRun/AgentLoop -> Artifact/TaskOutcome. Keep this path opt-in until parity is proved;
do not replace ordinary chat or build a temporary Direct-specific scheduler that later diverges.
In this plan, Direct means exactly the one-node/no-edge `invoking_session` shape. A one-node
`isolated` graph is not Direct and becomes Scheduler/root-terminal work in Subplan 5.

## Ownership

- minimal Workflow application execution service under `src/morrow/application/workflows/`;
- thin composition hooks in bootstrap/application boundaries;
- Workflow/Node read projections needed by the vertical slice;
- one-node cancellation/recovery integration through existing Agent/Tool services;
- one optional role-neutral terminal-result committer seam used only by Workflow leaves;
- one narrow idempotent Artifact publish/reuse helper over the existing ArtifactService;
- Workflow result-snapshot production and the narrow accepted-Outcome evidence carry-forward seam;
- focused Direct Workflow integration tests and execution state.

## Tasks

1. Add one idempotent `StartWorkflowCommand` requiring workspace/Definition IDs, command ID, exact
   compiled Revision ID, invoking active/healthy Session ID, its exact current `purpose=user` root
   TaskRun ID in `OPEN` with expected row version, bounded TaskContract and a Direct
   client-message ID. Check that the Workflow head is enabled, no other nonterminal WorkflowRun owns
   the root, the Session/root has no open Turn or nonterminal AgentRun, and Revision/Definition/root/
   Session associations match. This check and ordinary Turn admission's active-Workflow check are
   one bidirectional transactional exclusion, so concurrent Start/ordinary Turn attempts have one
   winner. It never implicitly selects,
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
   or nonterminal WorkflowRun; Revision-to-Definition membership; and the Workflow Head's current
   enabled gate. Then use an injected clock to
   freeze `started_at` and
   `admission_deadline_at = started_at + revision.admission_timeout_seconds`, and transactionally
   create receipt, `WorkflowRun(status=running)`/input binding and its one queued NodeRun. For Direct, leaf
   Task/Session refs equal the root pair.
2. Bind/admit that existing queued NodeRun through AgentFactory and the existing preparation path,
   then call the same
   `AgentLoop.run_task()` used by ordinary Direct execution. Do not add role or Workflow branches in
   AgentLoop/ToolExecutor. Workflow leaf composition supplies the Revision node's exact
   `resolved_model_ref` and never re-resolves the Definition selector or reads the current active
   model; standalone non-Workflow AgentFactory proof remains the only admission-time
   `invoking_active` resolution. Direct one-node Workflow uses the invoking root Session as its sole leaf,
   passes a distinct client-message ID through existing TurnLifecycle, and tests Workflow command
   replay separately from Turn replay so ConversationLog behavior remains identical. Turn admission
   rechecks the exact WorkflowRun/root ID and expected root version in its transaction; it cannot
   attach to a concurrently selected Task. Compose the same AgentLoop leaf without the ordinary
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
3. Pass the bound TaskContract text exactly once to the ordinary Direct leaf. Compose one optional,
   role-neutral `NodeResultCommitter` with the existing TurnSubmissionCoordinator terminal path;
   do not modify AgentLoop. After the final Assistant message is durably committed but before the
   terminal transaction applies the root transition, it deterministically publishes the text-result
   Artifact under an ID derived from `(node_run_id, output_slot)`, finalizes/reuses staging state on
   replay and contributes its Node binding to that same terminal transaction. Only then may the Turn
   terminal and root transition commit. Do not call `complete_structured`, a repair Provider or any
   second model request. Publication/binding failure follows ordinary failure or existing Artifact/
   recovery classification; it can never leave root READY without the required output. Ordinary
   Direct has no committer. Only the root TaskRun produces the user-visible TaskOutcome.
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
   Required-output work runs only for proposed STOP. Error/cancel terminals bypass it; known parse/
   Artifact failure is translated to one bounded application error so the existing AgentLoop error
   path can close the Turn/root as failed rather than recursively invoking the output committer.
4. Finalize NodeRun and WorkflowRun idempotently from the actual AgentRun terminal. Completed nodes
   are never run again on duplicate command/restart. Once the Direct Turn is admitted, existing
   TurnLifecycle remains the sole owner of the output binding/root Task transition: it commits those
   facts while WorkflowRun is still nonterminal, so the active-Workflow guard continues to own the
   root. For STOP/success only, an idempotent finalizer then writes one root
   `TaskOutcome(trigger=SNAPSHOT)` containing the TaskContract as its separate `goal_reference`,
   required result `artifact_refs` and the typed
   `WORKFLOW_RUN/workflow_result_snapshot` evidence marker and a
   `TASK_TRANSITION/workflow_ready_transition` ref to the exact TurnLifecycle transition that made
   the root READY, and closes WorkflowRun in the same transaction. The snapshot is completion evidence, not user acceptance or a LearningReview
   trigger. ERROR/CANCEL/output-committer failure closes Node/Workflow from the durable terminal and
   keeps TurnLifecycle's existing terminal Outcome; it does not fabricate a result snapshot without
   required outputs. Recovery may finish either finalizer from durable state without rerunning the
   node. For the exact Workflow-bound Direct Turn only, the internal lifecycle selects
   `TextSafetyProfile.WORKFLOW_VALUE_SENSITIVE` for every TaskOutcome it already produces, including
   STOP snapshot, ERROR/CANCEL and output-committer failure; ordinary Direct stays legacy-strict.
   Add one new evidence carry-forward input to accepted TaskOutcome assembly — this is new assembly
   logic, since the current assembler rebuilds solely from durable turns/tool executions/transitions
   and never consults prior snapshots. The input finds the exact latest root transition into
   READY and merges refs only from a marked Workflow snapshot carrying that transition ref, while
   preserving the existing Turn goal. It must ignore an intervening ordinary Task snapshot and must
   not reuse an older Workflow snapshot after resume + ordinary Direct produces a newer READY
   transition. Only a matching typed Workflow marker makes the existing acceptance assembler choose
   `WORKFLOW_VALUE_SENSITIVE`; ordinary acceptance without one remains legacy-strict. It queries no
   Workflow table and parses no summary text. Start
   validation before Workflow creation leaves
   root OPEN; a preparation/admission failure after creation but before any Direct Turn exists uses
   one Workflow lifecycle transaction to fail Node/Workflow/root and write TaskOutcome.
5. Reuse current cancellation and AgentRun recovery for the active leaf. A running node with an
   unresolved side effect becomes Workflow-blocked rather than being transparently retried. If that
   unknown arose from explicit cancel, persist `pending_terminal_intent=user_cancel`; after Recovery
   resolves the Tool, idempotently finish Node/Workflow/root cancellation instead of treating it as a
   crash-resume or successful result. A crash-classified block has no intent and follows ordinary
   recovery. Still-unknown remains blocked. Recovery-only abandon is implemented in Subplan 5 under
   the master handle-release invariant; this Direct slice must not close a Run while it still owns
   an exact live handler handle.
6. Expose the minimum repository/query projection needed to inspect Revision, NodeRun, AgentRun,
   terminal Artifact and usage availability. Do not add ApplicationEvent or change the public
   AgentEvent lifecycle in this slice.
7. Keep the existing ordinary Direct command/path unchanged and default. Add an explicit opt-in
   application/test entry for Direct Workflow until all parity evidence passes.
8. Add Scripted Provider end-to-end tests for normal completion, model/tool failure, cancellation,
   crash/recovery at Artifact reserve/publish/bind/terminal boundaries (including reserve then
   missing final bytes), command/client-message replay, second active Workflow, pre-existing open
   Turn/nonterminal AgentRun rejection, concurrent Start-versus-ordinary-Turn and root replacement,
   invalid/implicit root selection, disabled Agent/Workflow head or Provider/preparation failure
   before Turn admission, Workflow disable/root replacement/Turn admission during TaskContract
   publication, root purpose/status/version, definition drift, generic sensitive words
   in TaskContract/output versus actual-secret input rejection/output redaction, terminal/finalizer
   crash ordering and completed-node no-rerun,
   accepted-Outcome inheritance of the typed-marked Workflow result snapshot without replacing the
   existing Turn goal (including an intervening ordinary Task snapshot), rejection of stale Workflow
   refs after Workflow success -> resume -> ordinary Direct success, two-Workflow selection of only
   the current READY transition, ERROR/CANCEL no-result-snapshot behavior plus benign security-named
   path/fact terminal closure, accepted Workflow benign path/fact closure versus ordinary acceptance
   legacy behavior, cancel-unknown intent persistence/resolution versus ordinary crash recovery,
   positive/shrunken Direct request cap, cap/deadline rejection before the next request, deadline
   expiry around a safely settled Tool, compaction exclusion truth, and proof that Workflow
   execution neither consumes nor creates an ordinary steering/follow-up turn, and
   compile-with-active-model-A followed by switching active to B still runs the old Revision with A.

## Proportionality decisions

Required now are only truthful lifecycle correlation, idempotent terminal state and reuse of current
recovery. This subplan must not add:

- a generic Scheduler/worker, multi-node dependency logic or background lease;
- a second Tool/permission/budget pipeline;
- public event redesign, GUI/API server, Workflow default migration or compatibility facade;
- automatic retry or compensation after side effects.

A Workflow-specific failure affects only the opt-in run. If the wrapper prevents a legal task that
ordinary Direct can complete with identical inputs/evidence, parity has failed and the wrapper must
be repaired rather than defended as safer.

## Validation

```bash
uv run pytest -q tests/test_stage7_direct_workflow.py
uv run pytest -q tests/test_agent_run_preparation.py tests/test_stage4_recovery_crash.py
uv run pytest -m 'not live'
uv run ruff format --check .
uv run ruff check .
uv run python -m compileall -q src tests
uv run morrow --help
git diff --check
```

## Exit criteria

- The opt-in Direct Workflow uses the same AgentLoop, ToolExecutor, permissions and completion truth
  as ordinary Direct with no additional model request.
- Start never attaches to a non-current/non-open/internal root Task or leaves a runnable partial Run;
  it also rejects existing ordinary in-flight work, queued NodeRun identity is created once and
  admission only binds/transitions it.
- Required output is durably published/bound before the Direct terminal makes the root ready;
  deterministic replay repairs safe staging/binding gaps without a second model request.
- Normal, failure, cancel and crash/recovery outcomes are attributable through WorkflowRun,
  NodeRun, AgentRun and Artifacts.
- Completed work is not rerun; unknown side effects block only the affected Workflow.
- Existing Direct remains default and its focused/full offline regressions pass.
- No multi-node/runtime-default behavior exists yet.
