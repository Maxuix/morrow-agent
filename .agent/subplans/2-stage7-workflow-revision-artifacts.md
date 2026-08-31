# Subplan 2 — Stage 7 Workflow Revision and Artifact Contracts

> Status: pending
> Branch: `feat/stage7-workflow-domain`
> Prerequisite: Subplan 1 completed, verified and integrated

## Objective

Add the minimal immutable Workflow/Node/Run domain and persistence needed by a static DAG, plus
typed contract metadata on the existing Artifact authority. This subplan creates no execution path.

## Ownership

- focused Workflow domain modules under `src/morrow/core/workflows/`;
- a typed, revision-checked adapter for workspace `workflow-definitions.yaml` using existing
  YAML/OCC patterns, with no published pointer or operational enable switch in YAML;
- minimal Stage 7 Artifact contract payloads/extensions in `src/morrow/core/artifacts.py` or a focused
  adjacent module;
- Workflow Revision/published-head repository ports and one focused SQLite journal;
- the minimal additive `TaskRunPurpose`/root-versus-internal-leaf ownership fields and acceptance
  filtering required by the existing Turn-to-Task Session invariant;
- the next sequential Operational Store migration and thin registration;
- current backup/doctor reference and integrity checks for the added records;
- focused domain/journal/migration/backup/doctor tests and execution state.

## Tasks

1. Define editable `WorkflowDefinitionSource` identity separately from immutable
   `WorkflowRevision` and SQLite `WorkflowDefinitionHead`.
   The typed Source owns the Stage 7 graph input: origin, input contract, exact required
   `node_id.slot` outputs, finite four-field default Workflow budget, source-form nodes and
   unconditional `from_node_id`/`to_node_id` edges. Entry/terminal sets, normalized budget and each
   node's declared generation cap are Compiler-derived Revision fields, not duplicated user inputs.
   The Revision model contains an opaque `workflow_revision_id`, a monotonic display revision within
   its Definition, full normalized source metadata (`name`, `description`, `tags`, `origin`), frozen
   normalized `input_contract` and exact `required_outputs[]`, stable node IDs, unconditional edges,
   entry/terminal facts, canonical content hash, creator, optional `parent_workflow_revision_id`, compiler version and one
   normalized finite `WorkflowBudget` with positive `max_agent_generation_requests`,
   `default_node_max_agent_generation_requests`, relative `admission_timeout_seconds` and
   `max_concurrency`. This
   subplan defines representation/persistence only; Subplan 3's pure Compiler creates a candidate
   and its `WorkflowCompilationService` is the sole publisher of runnable Revisions/heads.
   The v1 typed shape permits exactly one Workflow input ContractRef, `TaskContract@1`, matching the
   only Start-time input producer defined below. It does not model alternate/multiple Workflow input
   contracts before a consumer exists.
2. Define the Stage 7 v1 agent-node shape only: AgentDefinition version ref, task contract, explicit
   input bindings and `output_contracts[]`. `InputBinding` is a strict discriminated union with one
   node-local-unique `input_name`, exact accepted ContractRef kind/version and exactly one source:
   literal sole `workflow_input=task`, or exact node-output `node_id.slot`. Every output has a stable
   node-local `slot`, exact kind/version and required flag. Node-output input bindings and Workflow
   required outputs reference exact `node_id.slot` values and may reference only slots declared
   `required=true`; the Workflow-input form instead follows the sole `task:TaskContract@1` rule. `required=false`
   slots are unbound observation outputs only. `(node_run_id, output_slot)` is the deterministic
   publication identity. Every node retained in a Stage 7 Revision is execution-required even when
   disconnected from final required outputs; any node failure uses the fixed whole-graph failure
   mapping. The output flag controls materialization/binding, not optional-node execution.
   Represent the graph rule that every cross-node input binding requires a same-direction declared
   producer→consumer edge; an edge without a binding is a legal pure control dependency. Workflow
   input bindings are exempt. The Compiler rejects inconsistency rather than auto-inserting edges.
   Also include `access_mode: read|write`,
   `conversation_scope: invoking_session|isolated`, an optional node
   `max_agent_generation_requests` override and a compiled positive
   `declared_node_max_agent_generation_requests`. The latter is represented here and resolved only by
   Subplan 3 from the node override or Workflow node default intersected with the referenced
   AgentDefinition run ceiling; do not create a generic budget expression.
   A compiled Revision node also stores exact `resolved_model_ref`; source nodes do not. Subplan 3
   resolves either the Definition's exact ModelRef or its literal `invoking_active` selector during
   compilation so later active-model edits cannot drift an already published Revision.
   The domain states the shape invariant consumed by Compiler: `invoking_session` is legal only for
   a graph with exactly one node and no edges. Every multi-node graph uses isolated leaves.
   Root terminal ownership is scope-based: only the invoking-session shape is Direct/TurnLifecycle-
   owned; every all-isolated graph, including one isolated node, is Scheduler-owned. The
   ReviewReport-specific Direct terminal conflict cannot exist until that real contract arrives and
   is therefore enforced/tested with its producer in Subplan 6, not with a placeholder here.
   Do not add optional input/fallback/skip semantics, conditions, approval/deterministic/merge kinds,
   retry/failure policy matrices, recursion or concurrency groups.
3. Define `WorkflowRun` and `NodeRun`/attempt with only the persisted states needed now:
   `queued`, `running`, `completed`, `failed`, `cancelled`, plus recovery-only `blocked`. Readiness is
   derived later and is not duplicated in durable state. WorkflowRun stores the frozen
   `workflow_revision_id`, `root_task_run_id` and input Artifact bindings; NodeRun stores
   nullable-until-admission `conversation_session_id`, `leaf_task_run_id` and `agent_run_id`, unique
   `node_run_id` and a unique
   `(workflow_run_id, node_id, attempt)`. Stage 7 creates attempt 1 only. WorkflowRun
   `result_status` is null unless status is completed, then is exactly `succeeded|needs_revision`.
   WorkflowRun stores an immutable copy of the Revision budget plus the absolute
   `admission_deadline_at` frozen at Start from the Revision's relative admission duration and an
   injected clock. NodeRun stores nullable-until-admission
   `effective_node_generation_request_cap`, later frozen as the smaller of its compiled declared max
   and Workflow remaining capacity;
   WorkflowRun also has one nullable bounded `pending_terminal_intent`, whose only Stage 7 value is
   `user_cancel`. It exists solely to distinguish cancel-with-unknown-effect from an ordinary crash;
   do not add a general intent queue or cancellation watcher.
   terminal NodeRun rows are immutable. Workflow start pre-creates one attempt-1 queued NodeRun per
   frozen node; admission binds the leaf references and transitions that same row to running.
4. Add the smallest TaskRun discriminator `purpose: user|workflow_node`, migrating existing rows to
   `user`. A Direct node reuses the root Session/TaskRun. An isolated node owns a fresh standalone
   Session and matching internal `workflow_node` TaskRun, preserving the existing requirement that
   Turn.session_id equals TaskRun.session_id. Internal leaf TaskRuns are omitted from ordinary user
   task lists and cannot enqueue LearningReview. Every ordinary user Task mutation (`accept`,
   `snapshot`, `resume`, `cancel`, `fail`, `abandon`, `new`) and ordinary Turn admission rejects
   `workflow_node`; Scheduler/Recovery receive one explicit internal leaf-lifecycle application
   boundary instead of bypassing guards by ID. Workflow Query may expose leaf references read-only.
   No SessionPurpose is added. Only the Workflow root TaskRun owns user acceptance and the
   authoritative TaskOutcome.
   Also define the root-ownership invariant: one `purpose=user` root Task may have at most one
   nonterminal WorkflowRun. While associated, ordinary Task mutation/replacement and ordinary Turn
   admission reject; Workflow-owned root transitions and the exact bound Direct Turn use explicit
   application boundaries. Once the Workflow is terminal, ordinary behavior resumes.
5. Extend `DurableAgentRun` by reference with Definition/Workflow/Node/attempt evidence required for
   attribution. Preserve current snapshots and current-format migration policy; do not retain a
   permanent old/new dual runtime path.
   Add one typed `TaskOutcomeEvidenceKind.WORKFLOW_RUN` reference with the WorkflowRun ID prefix; its
   reserved role `workflow_result_snapshot` is the non-text marker later used to distinguish a
   Workflow result snapshot from an ordinary Task snapshot. Pair it with the existing typed
   `TASK_TRANSITION` evidence ref under reserved role `workflow_ready_transition`, pointing at the
   exact root transition that produced the relevant `READY_FOR_ACCEPTANCE` epoch. Do not parse
   summary/completion-basis strings or add a parallel outcome type.
6. Reuse the existing Artifact envelope and bytes store. Add contract kind/version and NodeRun
   producer/input-binding facts. Implement only two bounded payload contracts with immediate
   consumers: Workflow-input `TaskContract`, bound on WorkflowRun, and role-neutral `TextResult`,
   used by the Direct slice and minimal compiler fixtures. `TextResult` records the durable final-
   Assistant reference/digest, bounded redacted excerpt and `content_complete`; it never duplicates
   ConversationLog authority. This makes recovery independent of in-memory prompt text or a copied
   root transcript. Later templates add their own built-in payloads only when they gain a real
   producer and consumer. These typed payload validators use the existing value-sensitive text-
   safety mode: generic security vocabulary is legal, while raw high-confidence credential values
   are never accepted as durable payload bytes.
   Extend that same explicit Workflow mode to root `TaskOutcome` construction/projection; do not add
   another scanner or change legacy non-Workflow callers. Benign paths/facts containing words such as
   `password` or `authorization` must persist, while an actual credential value is redacted/omitted
   and the existing `completion_basis` receives fixed fact `workflow_evidence_redacted=true` so legal
   terminal transitions still close. Do not invent a TaskOutcome `content_complete` field or type.
   Expose the existing TaskOutcome Artifact-reference capacity as a shared domain bound used by the
   Compiler: all Workflow required result refs stored in `artifact_refs` must fit (currently 64).
   TaskContract occupies the separate `goal_reference` and is not duplicated into that tuple.
   Optional projected leaf detail may be bounded with a deterministic omission fact; required refs
   cannot be truncated or hidden in an untyped blob.
   Since current Artifact and TaskOutcome model validators run again on rehydration, add one minimal
   persisted internal `TextSafetyProfile: legacy_strict|workflow_value_sensitive` discriminator to
   those two envelopes. Migrate/default existing rows and all generic APIs to `legacy_strict`; only
   focused typed Workflow Artifact/Outcome application seams may select `workflow_value_sensitive`.
   Definition YAML, prompts and public commands cannot select the profile. Both paths call the same
   existing redaction/refusal owner.
   State the downstream legacy-consumer rule for records carrying the Workflow profile: an accepted
   root TaskOutcome produced by a Workflow still enters the existing LearningReview path, where
   learning keeps its own legacy safety classifier and may truthfully skip or redact a candidate,
   but benign Workflow vocabulary must not raise NEEDS_REPAIR, crash review creation or block
   acceptance. Learning gains no Workflow-specific branch.
7. Add repository/journal operations for immutable WorkflowRevisions, SQLite published heads,
   WorkflowRuns, root nonterminal uniqueness, NodeRun attempts and Artifact bindings with legal
   transitions and idempotent
   terminal writes. A head records the exact YAML document revision plus that definition's canonical
   normalized body hash used by compilation and an OCC-protected operational `enabled` admission
   gate; desired-ahead compares the body hash, so an unrelated entry edit does not mark every head
   unpublished. Enable/disable changes only the head through one OCC repository operation;
   Subplan 8 later exposes its application/CLI command. AgentDefinition remains owned by Subplan 1
   and is referenced by immutable stored ID/version only. The repository accepts a Compiler
   candidate but cannot normalize/hash or expose another publication path.
8. Extend backup and doctor through their existing composition seams for both database records and
   the workspace `workflow-definitions.yaml` bundle inventory. Missing references, hash drift,
   future schema versions, unrelated-entry document revision changes and desired-ahead-of-published
   restore are diagnosed without a new
   Workflow backup format. Reuse Subplan 1's exact-path `DEFINITION_SOURCE` file kind/reference;
   do not add another manifest type. Backup/verify/restore handle that bounded source as exact raw bytes plus
   path/hash without parsing it first. A malformed desired draft remains faithfully backup/
   restore-able; validate/doctor reports it while valid published Revisions and ordinary Direct stay
   runnable. Reuse only the existing high-confidence raw credential-literal refusal: generic
   sensitive vocabulary/key names do not block backup, while an actual detected credential still
   yields a scoped backup error without affecting runtime.
   Preserve the Subplan 1 doctor severity rule: malformed unpublished desired source is warning/
   overall-OK when authoritative rows are intact; published reference/hash corruption is an error.
   Keep the backup manifest's legacy raw-text refusal safe against Workflow-profile content: the
   manifest projection of Artifact/Outcome records carries identity/path/hash facts only and never
   embeds Workflow-profile excerpt or payload text. Prove a benign security word stored under the
   Workflow profile cannot fail backup, and that a genuinely unsafe value is already absent from
   durable content before backup runs.
9. Add canonical representation/digest utility, immutable repository round-trip, state-transition,
   root/leaf Session-Task ownership and read-only visibility, root active-Workflow concurrency,
   rejection at every user mutation/Turn entry, exact Direct admission, internal lifecycle
   authority, conversation-scope shape examples, learning exclusion, workspace/reference isolation,
   multi-output slot uniqueness/binding, Workflow/Node budget repository and migration round trips,
   post-terminal ordinary Task/Turn acceptance, migration,
   WorkflowRun outcome-evidence reference validation, database/source backup/restore (including
   a malformed desired raw-byte round trip beside a valid published head) and doctor tamper tests.
   Include positive Workflow TaskOutcome cases for `password_validation.py` and
   `authorization test passed`, plus an actual credential-value omission/redaction case carrying
   `workflow_evidence_redacted=true` that still reaches the intended terminal state.
   Prove profile round-trip/rehydration, unchanged legacy rejection and inability for source fields or
   generic publish APIs to opt into the Workflow profile.
   Own the dedicated value-sensitive calibration set required by the master plan §8: benign security
   vocabulary positives and actual credential negatives across input rejection, output redaction/
   `content_complete=false`, rehydration and profile round-trip, kept in one focused place so later
   consumers extend rather than rediscover it.
   Cover exactly-at-capacity and one-over-capacity required-ref fixtures without duplicating the
   numeric bound in another validation owner.
   Do not add a non-Compiler API that publishes a runnable Revision.

## Proportionality decisions

Required now:

- immutable revision/hash and run reference, because definition drift would make recovery false;
- a SQLite published head beside immutable Revisions, because pretending to atomically update a
  YAML pointer and SQLite rows would require an unnecessary cross-store saga;
- stable node/attempt identity and Artifact producer binding, because later scheduling/recovery need
  exact attribution;
- a one-field TaskRun purpose and explicit root/leaf references, because reusing the root TaskRun
  across isolated Sessions violates an existing durable invariant and exposing internal leaf tasks
  to user acceptance would create false outcomes;
- backup/doctor coverage, because adding authoritative rows without recovery/integrity support
  would create unrecoverable state.

Explicitly deferred:

- GraphPatch, revision diff, CAS runtime editing, continuation runs and Artifact invalidation;
- arbitrary schema registry, automatic Converter/version negotiation, signature infrastructure,
  generic graph grammar and pre-created fields for unknown node kinds;
- execution, compiler behavior and public events.

An invalid Workflow record must not make unrelated definitions or existing Direct sessions
unreadable. Each persisted constraint gets a valid round-trip case, not an exhaustive field matrix.

## Validation

```bash
uv run pytest -q tests/test_stage7_workflow_domain.py
uv run pytest -q tests/test_stage7_workflow_store.py tests/test_operational_store.py
uv run pytest -q tests/test_stage4_backup.py tests/test_stage4_doctor.py tests/test_stage6_backup.py
uv run pytest -m 'not live'
uv run ruff format --check .
uv run ruff check .
uv run python -m compileall -q src tests
git diff --check
```

## Exit criteria

- The repository accepts only immutable compiled WorkflowRevisions, preserves old Revision/Run
  content exactly and exposes no second creation path; AgentDefinition persistence is not
  reimplemented here.
- Direct root ownership and isolated Session/internal-TaskRun ownership round-trip without changing
  existing user TaskRun behavior; internal leaf tasks cannot enter ordinary Turn/Task mutations,
  user acceptance or learning, while the internal Scheduler/Recovery boundary remains executable.
- Domain/state transitions reject only impossible or corrupt transitions and accept adjacent legal
  cases.
- Artifact contract/provenance facts round-trip without duplicating Artifact bytes or granting
  authority.
- Fresh migration, previous-current migration, future-version refusal, database plus desired-source
  backup/restore and doctor reference checks pass, including unpublished edits.
- No Workflow execution or speculative Stage 8 patch system exists.
