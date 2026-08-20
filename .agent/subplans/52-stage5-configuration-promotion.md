# Subplan 52 — Profile/Preferences Promotion Saga

> Status: planned
> Branch: `feat/stage5-config-promotion`
> Prerequisite: Subplan 51 complete and merged into verified `main` (Operational Store v11)
> Owns: prepared configuration contract, cross-store Saga/recovery/undo, config candidate promotion
> Schema: no new version; uses v11 `promotion_operations` and `configuration_activations`

## Objective

Make Preference/Profile Candidate acceptance as safe and recoverable as Project Knowledge despite
crossing SQLite and versioned YAML. Preserve YAML as the only Active configuration authority, never
overwrite a concurrent user edit, and retain a truthful provenance/undo trail.

## Public prepared configuration contract

Replace private `_OperationPlan` coupling with a public immutable DTO in the configuration
application boundary:

```text
PreparedConfigurationChange
- command: ConfigurationCommand
- expected_revision
- before_presence
- before_digest
- after_digest
- expected_applied_revision
- inverse_command (bounded type-safe command needed for explicit undo)
- changed
- preview_lines or typed preview data
- preparation_version
```

`ConfigPatchService` exposes:

```python
def prepare(command: ConfigurationCommand) -> PreparedConfigurationChange: ...


def apply_prepared(
    prepared: PreparedConfigurationChange,
    *,
    operation_id: str,
) -> ConfigurationChangeResult: ...
```

Contract:

- `prepare()` reads once, validates the existing command/path/value rules, and computes canonical
  presence-aware before/after digests.
- `apply_prepared()` reloads the target. If revision + before digest match, it recomputes the change,
  verifies after digest, then writes with expected revision.
- If the current target matches both the exact expected applied revision and after digest, it
  returns an idempotent applied result without a second write. Matching content at a later revision
  is drift, not proof that this operation applied it.
- Any other current revision/digest is a typed conflict. It never prepares a new value silently.
- `apply_command()` is retained for existing tools/commands and delegates through the same prepared
  logic without requiring a durable Learning operation.
- Session scope remains supported for explicit existing configuration commands but is not a valid
  long-term Learning promotion target.

The prepared DTO stores no credentials and must pass a bounded canonical payload/digest check. The
inverse command contains only the one affected field/item and is persisted with activation
provenance so undo never depends on a mutable YAML backup or reconstructs an old whole document.
Profile/Preferences themselves remain loaded/written only through existing YAML stores.

## Session revision correctness

Fix the current in-process projection as part of the prepared-contract work:

- Add `global_preferences_revision` to Session.
- Every successful global/workspace Profile/Preferences write updates both the Session value and its
  matching revision.
- Reset/tombstone writes update revision and presence truthfully.
- `build_session_application()` initializes all three revisions from actual load results.
- `build_agent_run_snapshot()` records the global config, workspace Profile, and workspace
  Preferences source revisions/digests even when a layer currently holds a default/cleared value
  where needed to prove the resolved snapshot.
- Existing explicit `update_configuration` behavior and approvals remain unchanged.

## Promotion eligibility

Preference Candidate whitelist is exactly the current configuration schema:

- `language`: set/unset bounded language string.
- `response_detail`: set/unset `concise | balanced | detailed`.
- `instructions`: append/remove one bounded interaction instruction.

Project commands, paths, environment facts, test invocations, architecture decisions, and general
repository content are rejected as Preference instructions and belong to Project Knowledge.

Profile Candidate whitelist is the current Profile schema:

- scalar `name`, `summary`;
- list `goals`, `tech_stack`, `constraints`, `conventions` through append/remove.

Profile requires explicit user Evidence. Reviewer output cannot create inferred identity/personality
fields. Workspace is the only Profile scope. Reviewer proposals default Preference to workspace;
global requires an explicit user scope edit in the final preview. No Candidate promotes to session.

## Saga state machine

### Prepare

Outside SQLite transaction:

1. Load Candidate through authorized service and build type-specific `ConfigurationCommand`.
2. Call `ConfigPatchService.prepare()` and render the complete final preview.
3. Obtain explicit UI confirmation before beginning the write Saga.

Inside short SQLite transaction A:

1. Re-read Candidate/expected row version/status/safety/conflicts/suppression.
2. Ensure command ID is not finalized and no conflicting in-progress operation exists.
3. Write `PromotionOperation(state=prepared)` with command/request digest, Candidate version,
   prepared DTO/digests, and operation ID.
4. Transition Candidate `proposed → promoting` and increment its row version.
5. Commit. No YAML write occurs inside this transaction.

### Apply YAML

Call `ConfigPatchService.apply_prepared()` outside SQLite transaction. It either:

- applies once from the exact before state;
- detects the exact after state and reports idempotent success; or
- returns a typed mismatch/failure without overwriting another revision.

### Finalize

Inside short SQLite transaction B:

1. Re-read operation and verify prepared Candidate ownership.
2. Verify the target's current digest is exactly the prepared after digest.
3. Write immutable accept/edit decision and `ConfigurationActivation` provenance.
4. Transition Candidate to accepted/edited-and-accepted.
5. Mark operation finalized with applied revision.
6. Emit `learning.candidate_accepted` and `memory.record_activated` events.
7. Store the application command receipt.
8. Commit atomically.

The generic receipt is written only at finalization. An in-progress operation is found by unique
command ID/request digest and resumed, never mistaken for a new command.

## Recovery and conflict resolution

Reconciliation reads the current YAML target and classifies:

| Durable operation + YAML state | Safe action |
|---|---|
| operation finalized | replay existing result |
| prepared; YAML matches exact before revision/digest | retry apply only on explicit command replay/retry, or cancel |
| prepared; YAML matches exact expected applied revision/after digest | finalize SQLite safely |
| prepared; YAML matches neither, has a later revision, or is future/read-only/corrupt | mark `needs_resolution`; do not write YAML |

Add foreground query/command surfaces:

- list/show unresolved Promotion operations;
- retry matching-before operation;
- finalize matching-after operation;
- cancel matching-before operation and return Candidate to proposed;
- for unknown/drifted state, keep current YAML and abort the old operation; a later acceptance must
  build a new preview/operation against the new revision.

There is no “force overwrite” recovery action. If the user wants the Candidate value after drift,
they explicitly start a new promotion against current state.

Interactive bootstrap may detect and notify about unresolved operations but must not launch hidden
background reconciliation. Safe reconciliation can run when the same command is replayed or when
the user invokes the foreground recovery command.

## Undo and supersession

`ConfigurationActivation` is provenance, not an Active value authority.

- `undo_activation` loads the activation's validated inverse command, reads current YAML, and
  creates a reverse `PreparedConfigurationChange` only when current revision/digest equals the
  activation's applied revision/after digest.
- It shows a complete inverse preview and requires `y/N`.
- Undo uses a new PromotionOperation/decision/activation with `reverses_activation_id`; it never
  deletes the original history.
- If YAML changed later, undo returns conflict and offers no automatic merge/overwrite.
- Accepting a later Candidate for the same target records `supersedes_activation_id` when the prior
  activation is still the current matching provenance.
- Direct non-Learning config edits remain valid YAML authority. Queries mark old Learning activation
  provenance as no longer current when digests/revisions differ; they do not rewrite it.

## Application/UI integration

- `LearningPromotionService` dispatches Preference/Profile types to the Saga and Project Knowledge
  to its existing SQLite path. Review handlers and UI never call YAML stores.
- `/learn accept` and `/learn edit` previews now support Preference/Profile candidates and clearly
  label workspace/global effects.
- `/learn undo <activation-id>` and mirrored top-level CLI use preview + confirmation.
- `/learn promotions` shows unresolved operations and safe recovery choices.
- Acceptance result says exactly “Active Preference” or “Active Profile field,” target scope,
  revision, and activation ID; no generic “memory saved” claim.

## Crash/fault injection matrix

Named test-only fault points cover:

1. before/after SQLite prepare operation;
2. after Candidate becomes promoting but before transaction commit;
3. before YAML write, during temp/fsync/replace behavior already supplied by YAML fixtures, and
   immediately after YAML success;
4. before/after SQLite finalization decision/activation/events/receipt;
5. command replay at every boundary;
6. concurrent direct YAML edit before apply, after apply, and before finalize;
7. YAML future/corrupt/read-only state;
8. cancellation/KeyboardInterrupt around the external YAML phase;
9. undo before/after a later direct edit;
10. global/workspace two-process revision conflict.

Required proofs:

- no operation/decision says accepted when YAML stayed at before state;
- YAML at after state is always safely finalizable after restart;
- unknown current YAML is never overwritten;
- Candidate cannot be rejected/expired while `promoting`;
- replay never applies YAML twice or creates duplicate activation/event/receipt;
- failures never expose full values, paths, SQL, or tracebacks in events/errors.

## Tasks

### S52.1 Prepared configuration API

- Promote the internal plan to a bounded public DTO; implement prepare/apply-prepared/idempotent
  digest behavior and keep existing apply/preflight/tool compatibility.
- Correct Session value/revision projections and AgentRun source revision inputs.

### S52.2 Promotion operation repository/service

- Implement v11 Saga repository methods, candidate promoting lock, command replay discovery,
  prepare/apply/finalize states, error mapping, and fault points.

### S52.3 Preference/Profile validation and promotion

- Implement strict type/path/scope/authority whitelist and conversion to ConfigurationCommand.
- Route accepted and edited Candidate values through the Saga; add activation/supersession
  provenance and sanitized events.

### S52.4 Recovery

- Implement before/after/other digest reconciliation, list/show/retry/finalize/cancel/abort commands,
  restart tests, and non-destructive conflict UX.

### S52.5 Undo

- Implement inverse prepared changes, activation reversal records, stale-current refusal, preview
  and confirmation flows.

### S52.6 Closeout

- Reconcile configuration/architecture/roadmap docs, run focused/full gates, merge verified work,
  and prepare Subplan 53.

## Planned tests and gate

New tests:

- `tests/test_stage5_configuration_promotion.py`
- `tests/test_stage5_promotion_recovery.py`

Regression set includes:

```text
tests/test_configuration_tool.py
tests/test_state_and_workspace.py
tests/test_preferences_and_orchestration.py
tests/test_stage4_recovery_crash.py
tests/test_stage4_application_api.py
tests/test_stage5_learning_application.py
tests/test_stage5_project_knowledge.py
tests/test_terminal.py
tests/test_stage5_learning_cli.py
tests/test_architecture_boundaries.py
```

Finish with full non-live, Ruff format/check, compileall, learning/memory/main CLI help, and
`git diff --check` because configuration behavior and crash recovery are high risk.

## Completion gate

A Preference/Profile Candidate can be previewed, explicitly confirmed, applied exactly once through
the existing configuration authority, finalized with provenance, recovered after a crash at every
cross-store boundary, and undone only when safe. Concurrent/newer YAML is never overwritten;
Session revisions are truthful; direct config behavior still passes; and verified work is merged
into `main`.

## Out of scope

- Moving Active Profile/Preferences values into SQLite.
- Auto-accept/`explicit_auto`.
- Multi-target atomic configuration patches from one Candidate.
- Automatic merge/force-overwrite of concurrent YAML.
- Memory selection/context injection or production Reviewer.
