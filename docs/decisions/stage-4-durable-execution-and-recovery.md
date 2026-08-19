# Stage 4 Durable Tool Execution and Recovery decision

## Status

Accepted for Stage 4 implementation planning by S4.35.4. No production journal or recovery path is
implemented by this decision.

This decision closes the persist-before-effect, approval, evidence, Host/sandbox recovery, payload,
and fault-injection blockers found in
[`docs/reviews/stage-4-plan-review.md`](../reviews/stage-4-plan-review.md).

## Current-code facts

- Tool intent, approval, execution facts, ChangeSet, and sandbox promotion state are process-local.
- `OperationIntent.effect` is a Stage 3 policy hint. In particular, `run_command` currently reports
  `ToolEffect.NONE` even though an approved Host process may affect files, network, credentials, or
  other Host state.
- Host and native-sandbox subprocess adapters do not persist PID/PGID or a death proof.
- Native sandbox snapshots are task-private and cleaned in `finally`; current-run promotion state is
  not recoverable after process death.
- file mutation preflight already has `desired_raw`, so before/expected-after evidence can be
  computed before publish, but only the actual after revision is currently retained after write.
- mutation executes in `asyncio.to_thread` under `asyncio.shield`; cancellation does not prove that a
  write did not finish.

No recovery classifier may infer safety from the current `ToolEffect` or from the word “sandbox.”

## Execution records and state

Each provider ToolCall has one immutable ToolExecution intent. The v1 recovery surface does not expose
retry until it can create and execute a linked attempt atomically; a future linked attempt will use the
`retry_of_execution_id` field.

```text
prepared
→ awaiting_approval → executing
→ executing
→ handler_completed
→ closed
```

An orthogonal disposition records `pending | denied | succeeded | failed | cancelled | interrupted |
unknown`. `handler_completed` means a bounded result/failure and structured facts committed; `closed`
means the ordered ToolMessage also committed through ConversationLog. Denial and pre-start policy
failure still produce a legal ToolMessage and close the call without running the handler.

State changes use optimistic row versions. Old rows are immutable audit evidence; any future retry must
create a new ToolExecution linked by `retry_of_execution_id`.

Provider-controlled ToolCall IDs are not written verbatim to durable state. Conversation records,
ToolExecution intents, ToolMessages, and terminal interrupted-call lists use the same deterministic
`call_<sha256>` opaque projection. This keeps the Assistant/ToolMessage/ToolExecution correlation
stable across restore and retry without allowing an arbitrary provider ID to leak into the
Operational Store; the in-process ConversationLog may retain the wire ID until its committed
projection is replaced.

## Prepare and persist before effect

Tool execution uses this order:

```text
validate Provider ToolCall and schema
→ run pure/local preflight and build PreparedIntent
→ compute recovery evidence for structured writes
→ ConversationLog validates Assistant ToolCall candidate
→ BEGIN IMMEDIATE
→ write Assistant record + ordered ToolExecutions + AgentRun state
→ COMMIT
→ update ConversationLog projection from committed rows
→ create/resolve approval when required
→ atomically consume approval and mark executing
→ call handler
→ commit handler_completed evidence
→ append ordered ToolMessage through ConversationLog and mark closed
```

Preflight may read and validate current state but cannot perform the intended mutation, launch a
process, publish an Artifact, or retain a database connection for a handler. The handler cannot be
called unless a fresh connection can observe its committed intent and, when required, consumed
approval.

SQLite access remains synchronous on the owning event-loop thread. Filesystem/thread work and
subprocess adapters receive typed immutable intent data, never a live SQLite connection.

`application_events` are not part of the Subplan 38 transaction because their schema and projection
belong to Subplan 43. Subplan 43 may later append an audit event in these same business transactions
without changing the execution state machine.

## Approval contract

The durable Approval contains:

- opaque `approval_id` and `tool_execution_id`;
- canonical intent hash and Tool Schema digest;
- effective Stage 3 permission-context digest; Subplan 44 later adds a PermissionSnapshot FK;
- requested capability/effect and granted subset;
- bounded redacted preview digest/content;
- `row_version`, `created_at`, `expires_at`, resolution, `resolved_at`, and `consumed_at`;
- the local interface command receipt that resolved it.

Resolution and transition to `executing` occur atomically. Expired, denied, mismatched, stale, or
consumed approvals cannot start a handler. Terminal wait that crosses expiry resolves to denial or
expired—not execution. An injected Clock is used by production and tests. A separate approval nonce
is not part of the local Stage 4 design.

Subplan 38 updates the terminal approval adapter to display and resolve the durable approval ID.
That is not a change to public AgentEvent lifecycle.

## Durable effect and recovery declarations

Every production tool has an explicit declaration written by Morrow; none is derived from
`ToolEffect`, name matching, approval mode, or current adapter.

| Tool | Effect class | Missing `handler_completed` after restart |
|---|---|---|
| `list_directory` | bounded read | classified safe for a future linked retry; v1 requires explicit close/quarantine |
| `read_file` | bounded read | classified safe for a future linked retry; v1 requires explicit close/quarantine |
| `find_files` | bounded read | classified safe for a future linked retry; v1 requires explicit close/quarantine |
| `search_text` | bounded read | classified safe for a future linked retry; v1 requires explicit close/quarantine |
| `show_changes` | durable-state read after Subplan 38 facts exist | classified safe for a future linked retry; v1 requires explicit close/quarantine |
| `git_status` | bounded external read | classified safe only with frozen confinement; v1 requires explicit close/quarantine |
| `git_diff` | bounded external read | classified safe only with frozen confinement; v1 requires explicit close/quarantine |
| `calculate` / fixture-only pure tools | pure | classified safe for a future linked retry; never production-enables the fixture |
| `update_configuration` | reconcileable structured state write | requires revision/value reconciliation; never blind retry |
| `apply_patch` | reconcileable file write | requires file/parent evidence reconciliation |
| `write_file` | reconcileable file write | requires file/parent evidence reconciliation |
| `promote_sandbox_changes` | reconcileable file writes | reconcile each promoted file; never retry the old sandbox |
| `run_command` on Host | unconfined external effect | `outcome_unknown`; never automatically replay |
| `run_command` in native sandbox | process effect with non-durable process/snapshot evidence | `outcome_unknown`; never automatically replay in Stage 4 v1 |

Production composition fails if any registered tool lacks a declaration. The declaration table is
tested against the exact frozen production inventory, including conditional sandbox promotion.

## Structured mutation evidence

Before a file handler starts, the intent transaction stores, per target:

- normalized workspace-relative path and operation;
- before existence and `before_sha256` when representable;
- `expected_after_sha256` computed from preflight `desired_raw`;
- `expected_size`;
- expected target kind and parent/auxiliary directory conditions;
- protected-resource/policy version and conflict input digest;
- bounded changed-line/byte counts and whether preview evidence was truncated.

After the handler, `handler_completed` stores actual after hash/size, operation/status, and bounded
structured ChangeToolFact-equivalent evidence. Full diff bytes wait for Subplan 41 Artifacts.

Files outside the current 8 MiB revision/preflight contract are rejected before effect. They are not
pretended reconcileable. `asyncio.shield` means an executing mutation with no completion row must be
reconciled from hashes; cancellation alone is not proof it did not write.

Configuration writes store the source document revision, canonical requested scalar/list operation,
expected resulting non-secret fields/digest where derivable, and post-write revision/digest. Missing
completion requires read/revision reconciliation.

## Process and sandbox recovery

Stage 4 v1 intentionally does not add a PID reaper or claim cross-process liveness proof.

- Host intent committed without `handler_completed` is always `outcome_unknown`.
- Native-sandbox intent committed without `handler_completed` is also `outcome_unknown`, regardless
  of isolation guarantees during a normal run.
- An orphan temporary directory, cached in-memory change set, absent PID, or elapsed timeout is not
  proof that the process ended without effect.
- Sandbox promotion is reconciled as a sequence of structured file writes. The original sandbox is
  never resumed or re-promoted after restart.
- A future stage may introduce durable process identity only through a new ADR and real current-host
  crash evidence; it cannot silently change v1 recovery policy.

## Recovery classifier

The classifier is pure over durable state and current observations:

```text
never_started
safe_to_retry
requires_reconciliation
outcome_unknown
completed
```

| Durable/observed condition | Classification/action |
|---|---|
| no committed execution row | no tool attempt exists |
| intent committed; handler-entry fault point not crossed | `never_started`; linked retry is reserved for a future v1 extension |
| bounded read executing without completion | `safe_to_retry` classification; current v1 requires an explicit close/quarantine decision |
| structured file/config write executing without completion | `requires_reconciliation` from expected evidence |
| actual state matches expected after | record reconciliation result; close with truthful recovered-completed evidence, never synthesize original handler payload |
| actual state matches before | eligible for a future linked retry after the linked-attempt path exists |
| actual state matches neither or evidence missing | `outcome_unknown` |
| Host/sandbox process lacks completion | `outcome_unknown` |
| handler_completed but ToolMessage missing | append a recovery interrupted/error ToolMessage explaining lost result delivery; never invent original output or synthesize success |

For strict safety, ConversationLog's recovery API only emits interrupted/error envelopes in Stage 4
v1. A proven completed side effect remains visible in RecoveryReport/TaskOutcome facts while the
ToolCycle closes with a recovery-interrupted envelope explaining that original result delivery was
lost.

Recovery must close/quarantine the open ToolCycle before ContextBuilder starts a new model request.
User resolution commands are idempotent. Recovery never edits prior rows.

When recovery creates a new AgentRun, all prior run-bound CapabilityGrants expire for that purpose;
the user must explicitly grant again if elevated work is still required.

## Durable payload budgets

Budgets count canonical UTF-8 bytes after redaction and before database insertion. Exceeding a row
budget fails closed or moves eligible content to a Subplan 41 Artifact; it never truncates a hash,
identifier, state, or recovery field.

| Payload | Initial maximum |
|---|---:|
| canonical conversation record payload | 256 KiB |
| ToolExecution prepared-intent JSON | 32 KiB |
| canonical ToolCall arguments retained inside Assistant record | 128 KiB per call |
| ToolMessage/result envelope | 16 KiB per call |
| structured durable tool facts | 32 KiB per execution |
| Approval record plus redacted preview | 16 KiB |
| error message/details | 4 KiB |
| AgentRun non-secret snapshot | 64 KiB |
| RecoveryReport | 64 KiB |
| TaskOutcome row payload | 64 KiB |
| application-event payload (Subplan 43) | 8 KiB |

Existing tighter Stage 3 result/tool limits continue to win. These are storage ceilings, not a
reason to enlarge Provider-visible or automatic-mutation limits. Any change to an existing bundled
policy default remains a separate hold point.

## Fault injection contract

Subplan 38 introduces a narrow injected `FaultInjector`/fault-point port. Production uses a no-op;
tests trigger one named point once. At minimum:

```text
conversation.before_commit / conversation.after_commit
execution.intent_after_commit
approval.after_create / approval.after_consume
handler.before_enter / handler.after_return
execution.after_handler_completed
conversation.before_tool_message_commit / conversation.after_tool_message_commit
turn.before_terminal_commit / turn.after_terminal_commit
```

Subplan 39 uses the same points for logical exceptions and subprocess `os._exit` with barriers/IPC.
No timing sleep establishes whether a boundary was crossed.

## Rejected alternatives

- deriving recovery safety from current `ToolEffect.NONE`;
- retrying Host or native sandbox commands after restart because no process is visible;
- retaining only an actual after hash written after the side effect;
- persisting complete raw command streams or unbounded tool arguments/results;
- putting application-event projection into Subplan 38;
- using approval text, a nonce, elapsed time, or a stale PID as authority;
- mutating ConversationLog before the intent transaction commits.
