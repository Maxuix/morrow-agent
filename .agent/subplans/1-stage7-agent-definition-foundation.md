# Subplan 1 — Stage 7 Agent Definition Foundation

> Status: verified; local integration pending (authorized 2026-08-31)
> Branch: `feat/stage7-agent-definitions`
> Activation base: `main@cb8fcc8` (clean tree; 1301 offline tests passed)
> Prerequisite: Stage 7 entry GO and this master plan approved
> Revised 2026-08-31 per the conditional-GO plan review: declared tool requirements and
> disable-versus-revoke semantics are now part of the foundation contract.

## Objective

Add the minimum versioned AgentDefinition and AgentFactory composition needed to create distinct
leaf Agents through the existing prepared AgentRun path. Lock the declared tool-requirement model
and the disable-versus-revoke operational contract without creating a second chat-history writer or
modifying AgentLoop into an orchestrator; durable Workflow/root-versus-leaf Task ownership arrives
in Subplan 2.

## Ownership

- focused AgentDefinition contracts under `src/morrow/core/`;
- a typed, revision-checked adapter for workspace `agent-definitions.yaml` under
  `src/morrow/adapters/state/`, using existing YAML patterns for editable source only;
- immutable full AgentDefinitionVersion/head repository rows, additive revocation records, the next
  sequential Operational Store migration, and current backup/doctor coverage;
- focused composition under `src/morrow/application/agent_definitions/` or
  `src/morrow/application/agent_runs/`;
- the smallest caller-supplied Session/TaskRun conversation-scope seam needed by a later isolated
  Workflow leaf;
- built-in Direct and Explorer definition fixtures;
- focused tests and `.agent/` execution state.

Large existing composition/runtime modules receive only thin calls or injected protocols. This
subplan does not own Workflow domain types, a scheduler or user-facing Workflow commands.

## Tasks

1. At activation, record the exact current seams for `PreparedAgentRunSpec`, AgentRun snapshots,
   Provider/Model/Skill/Tool selection, permission snapshots, prompt assembly, fresh Session
   creation and Session-owned ConversationLog restoration. Record explicitly that current
   `SessionForkService` imports parent transcript and is not an isolation mechanism.
2. Define a strict minimal editable `AgentDefinitionSource` and immutable full
   `AgentDefinitionVersion` containing only fields consumed in Stage 7: ID, name/description,
   bounded role prompt, exact `skill_version_ids[]`, `tool_requirements[]`,
   `access_mode_ceiling: read|write`, optional positive `max_agent_generation_requests` and a
   two-case `model_selection`: existing exact `ModelRef`, or literal `invoking_active`. There is no
   ModelPolicy/CapabilityPolicy/ContextPolicy reference, registry, model fallback, context-selection
   DSL or AgentDefinition token/tool/deadline budget. `invoking_active` has one explicit
   resolution-point contract: it is resolved exactly once at the consuming artifact's own freeze
   boundary — at Workflow publication for a Revision node (publish-time freeze, frozen as
   `resolved_model_ref`), at its own run admission for a standalone AgentRun — and never
   re-resolved afterwards.
   `tool_requirements[]` entries carry `name` plus `requirement: required | optional | forbidden`.
   The required/optional names form the definition's desired tool set (an allow-list), and
   `forbidden` is an explicit deny that always wins over any other declaration. The composer and
   later the Workflow Compiler apply the master plan's fixed precedence: forbidden conflicts are
   errors; required denied by policy or absent from catalogs is a compile/publication error;
   optional denied/absent/unavailable is removed with a diagnostic; required whose backend is
   unavailable at runtime fails only that node's preparation. SkillVersion envelopes have no tool-requirement field, but current Skill manifests already
   declare `required_tools`; reuse their dependency checks without treating them as grants.
   Skill-delivered tools remain subject to the definition's declared set and task policy.
3. Reuse the current typed YAML/OCC adapter pattern for editable desired source. The source has no
   operational `enabled` field. Store complete
   normalized immutable Versions plus an `AgentDefinitionHead` in the Operational Store through one
   publication service/transaction. `source_revision` is document-envelope/OCC load metadata and
   `source_hash` is computed per definition ID from its canonical normalized loaded body; neither
   hash nor per-definition revision is
   a user-editable body field. The head records that exact loaded revision/hash and an
   OCC-protected operational `enabled` gate; a later YAML edit is merely desired state ahead of
   published state, not a cross-store partial failure. Publishing a later Version preserves the
   head gate; desired-ahead comparison uses the per-definition body hash rather than document
   revision alone, so an unrelated entry edit does not mark all heads stale. Enable/disable updates
   only the head and creates no Version. First publish is enabled
   unless its command explicitly requests disabled. Bad or unpublished source is isolated and
   cannot replace the prior published Version or prevent Direct.
   This subplan provides the OCC application/repository operation for the gate; Subplan 8 only adds
   the user-facing CLI command.
   Add the separate emergency-revocation contract: an additive, audited, one-way
   `AgentDefinitionRevocation` record keyed by exact immutable version ID, carrying reason,
   timestamp and command provenance. The immutable Version row itself is never mutated and a
   revocation is never reversed — supersede by publishing a new version. The publication service
   owns revocation writes; revocation never republishes or rehashes anything.
   At validate/publish, reuse one value-sensitive mode in the existing redaction/refusal owner:
   benign words such as `password`/`authorization` in role prose remain legal, while recognized
   high-confidence credential tokens or explicit non-placeholder secret values reject only that
   Definition publication. `validate` is pure and write-free: it parses and checks the desired
   source and returns diagnostics without creating a Version, moving a head or touching any
   Operational Store row. Do not add a prompt scanner or change legacy non-Workflow callers.
4. Extend current backup/doctor owners for both immutable Version/head rows, revocation records and
   the explicit workspace `agent-definitions.yaml` bundle inventory. Prove create/verify/restore
   and doctor for a
   desired-ahead-of-published source; old Versions remain loadable after desired-source edits and
   must not be reconstructed from the current YAML body. Add the minimum current-manifest extension
   needed because parsed `BackupYamlEntry` cannot own malformed source: one additive
   `DEFINITION_SOURCE` file kind/reference restricted to exact workspace definition-source paths,
   carrying ordinary file path/hash/size and no schema/revision fields. This is not a new format or
   backup subsystem. Backup/verify/restore copy the bounded
   source as exact raw bytes with path/hash and do not require it to parse; a malformed desired draft
   remains backup/restore-able, while validate/doctor reports it and the last valid published head
   plus ordinary Direct remain usable. For these raw-byte entries, reuse the existing raw-text
   credential refusal exactly as implemented today (high-confidence token literals and explicit
   secret assignments, no parsing); the parse-dependent recursive key-name scan does not apply to
   unparsed bytes, so generic sensitive vocabulary or key names alone are not backup gates, while an
   actual detected credential still fails that backup without disabling runtime.
   Doctor classifies malformed unpublished desired source as a definition-local warning while the
   published head/database is intact; it must not set overall store health to needs-repair. Richer
   repair workflows and doctor UX polish are deferred to Subplan 8's management surface.
5. Add an AgentFactory/application composer that resolves a published Version into the existing
   preparation request and `PreparedAgentRunSpec`. New standalone admission requires the
   Definition head enabled and the exact Version free of any revocation record. An admitted run is
   frozen: a later ordinary disable never blocks a running AgentRun, a not-yet-started node of an
   already-admitted WorkflowRun, or recovery from the frozen snapshot — only revocation does, and
   only at defined admission/resume boundaries. The composer may restrict current task policy but
   can never grant a Provider, Skill, Tool or permission that the current preparation path would
   deny.
   It reuses the current PromptAssembler/ContextBuilder and current Preference/Knowledge selection;
   role prompt, exact Skill versions and explicit bound Artifacts are the only Definition/node
   context inputs added here. Actual prompt/context/permission evidence remains frozen on AgentRun.
6. Freeze the exact Definition ID/version/hash in AgentRun evidence without duplicating the existing
   model, Skill, ToolSet, Preference, context, permission or run-policy snapshots.
7. Define and prove the factory seam for one caller-supplied conversation scope without a
   second writer: `isolated` requires a distinct empty standalone Session and matching TaskRun pair,
   then passes context only through Task Contract and bound Artifacts. The AgentRun stores
   `conversation_session_id`; only that Session-owned ConversationLog and leaf AgentLoop may append.
   Do not use transcript fork or make WorkflowStore/AgentRun a writer. The `invoking_session` value
   is deliberately not created here: it arrives with its first consumer, the Subplan 7 Direct
   adapter. This subplan does not yet add the durable `workflow_node` TaskRun purpose or create
   Workflow/Node associations.
8. Publish minimal built-in Direct and Explorer Versions sufficient to prove exact-ModelRef and
   `invoking_active` resolution both freeze an exact model with two distinct configurations, and to
   exercise required/optional/forbidden tool declarations. Defer Coder, Reviewer, Planner and
   Synthesizer behavior until their consuming subplans.
9. Add focused source parsing/OCC/per-definition hash (including unrelated-entry edit), publication
   idempotency, validate-write-free proof, enable/disable admission and recovery, revocation
   admission/resume blocking plus audit fields and one-way refusal,
   required-denied/required-absent/optional-removed/forbidden-precedence declaration tests,
   immutable old-Version rehydration, desired-source plus database migration/backup/
   restore/doctor (including malformed desired raw-byte round trip with a valid published head,
   generic sensitive vocabulary and actual-token refusal),
   benign-sensitive-vocabulary versus actual-token publication, factory freeze/no-escalation and
   conversation-isolation tests.

## Proportionality decisions

Required now:

- immutable Definition version evidence because later edits must not drift an AgentRun;
- complete old-Version storage because WorkflowRevision references must remain executable after
  desired-source edits;
- declared tool requirements because the compiler and read-ceiling enforcement already distinguish
  required from optional and need a single declaration owner;
- disable/revoke separation because an admitted WorkflowRun must stay frozen while a genuine
  emergency brake still exists;
- capability intersection because a Definition is lower authority than task/runtime policy;
- an explicit isolation seam because multi-Agent nodes must not share hidden chat state; its durable
  root/internal-leaf ownership is intentionally left to the Workflow domain subplan.

Explicitly deferred:

- generic role/plugin inheritance, learned routing, model fallback, policy DSL, prompt scanning,
  per-field privacy DSL, Agent marketplace/import signing and a new log storage system;
- full Agent management CLI, revoke CLI and Workflow references;
- the `invoking_session` conversation scope and its TurnLifecycle integration (Subplan 7);
- any guard already enforced by AgentRun preparation, CapabilityPolicy or ToolExecutor.

Every new rejection must include the closest valid acceptance case. Definition errors affect only
that definition; legal Direct operation must remain available.

## Validation

```bash
uv run pytest -q tests/test_stage7_agent_definitions.py
uv run pytest -q tests/test_agent_run_preparation.py tests/test_stage4_durable_log.py tests/test_operational_store.py
uv run pytest -q tests/test_stage4_backup.py tests/test_stage4_doctor.py
uv run pytest -m 'not live'
uv run ruff format --check .
uv run ruff check .
uv run python -m compileall -q src tests
git diff --check
```

Use actual final test paths if implementation splits the focused file. No Live Provider/network or
credential test is permitted.

## Exit criteria

- Two Definitions produce distinct, accurate and immutable prepared run evidence through the same
  existing preparation path.
- Full published Versions and their heads survive desired-source edits, migration, backup/restore
  and doctor checks; unpublished or malformed desired edits also survive raw-byte backup/restore
  without blocking the valid published head, and AgentFactory never reconstructs an old Version
  from current YAML.
- Head enable/disable has one meaning — it gates new admissions — while historical inspection,
  admitted WorkflowRuns and already-running AgentRun recovery continue from frozen evidence;
  revocation is additive, audited, one-way and blocks new admission and resume of the exact version.
- `tool_requirements` precedence is deterministic: forbidden always wins, required violations are
  publication/compile errors, optional removal is a diagnostic, and no declaration path can expand
  task/permission capability.
- Editing current desired state does not change an already prepared or rehydrated AgentRun.
- `validate` performs no write of any kind.
- The factory accepts a distinct empty standalone Session/matching TaskRun pair that receives no
  parent transcript. Session-owned ConversationLog remains the sole writer authority, and no
  Workflow persistence is claimed yet.
- Existing ordinary Direct chat and AgentLoop semantics are unchanged.
- All declared focused, full-offline and static validation passes, and coherent progress is
  committed before root integration.
