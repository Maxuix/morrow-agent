# Stage 4 Artifact, Context Checkpoint, and Fork decision

## Status

Accepted for Stage 4 implementation planning by S4.35.5. No Artifact Store, checkpoint, or fork is
implemented by this decision.

## Artifact authority and layout

Artifact metadata is operational truth in SQLite. Artifact bytes are managed files under
`{data_root}/artifacts/`; conversation records and TaskOutcome contain references and bounded
excerpts, not duplicate large payloads.

Paths are derived only from opaque `artifact_id` and managed suffix/kind metadata. The content hash
verifies integrity but does not determine the path and does not imply deduplication. Symlinks,
hardlink surprises, traversal, user-provided absolute paths, and paths outside the validated data
root are rejected.

## Initial kinds and budgets

Initial kinds are:

- `command_output` containing only the existing bounded, fully redacted command result;
- `patch` / `diff`;
- `test_report` / `diagnostic_report`;
- deterministic `task_summary` / `context_summary` payloads when too large for an inline row.

No repository snapshot, credential export, Provider reasoning, SDK object, full environment, raw
traceback, or full/raw process stream is an Artifact kind in Stage 4.

| Artifact field | Maximum |
|---|---:|
| metadata JSON | 32 KiB |
| inline excerpt | 8 KiB |
| one Artifact file | 64 MiB |
| new Artifact bytes attributed to one TaskRun | 256 MiB |

Existing tighter command/diff/tool limits win. Crossing an aggregate cap returns a typed
`artifact_budget` result; it does not delete old evidence or bypass redaction.

The current 8 KiB bounded command output may be persisted after its existing full-buffer redaction.
A streaming redactor is required only before any future full/raw stream retention.

## Publication state machine

Filesystem and SQLite cannot commit atomically, so partial states are explicit:

```text
reserve metadata row: staging
→ create validated user-only temp file
→ bounded write + file fsync
→ compute/verify hash and size
→ atomic rename into final managed path
→ parent-directory fsync
→ mark metadata available in a short transaction
```

Crash outcomes:

- no metadata/no file: no Artifact exists;
- `staging` plus temp file: managed orphan candidate;
- `staging` plus final file: verifier may finish publication only after ID/path/hash/size match;
- `available` plus missing or mismatched bytes: explicit `missing` or `corrupt`, never silently
  recreated;
- unreferenced managed temp/final file: doctor reports an orphan candidate.

Stage 4 does not automatically delete conversation records or Artifact bytes. Archive does not
change retention. Doctor/cleanup defaults to dry-run and may remove only exact, validated,
unreferenced managed temp/orphan targets after an explicit command. Referenced or pinned Artifacts
are never cleanup candidates.

The YAML document publisher's `os.replace` protocol is not reused for SQLite. It may be shared only
as a conceptual byte-file publication pattern for Artifact bytes.

## ContextCheckpoint

A checkpoint is an immutable, reproducible prompt projection—not a conversation writer or memory.
It stores:

- `checkpoint_id`, Session/Task IDs, creation run/time, codec/method version;
- inclusive/exclusive source `conversation_record_id` and `conversation_position` ranges;
- retained record IDs and complete-cycle boundaries;
- Artifact references and bounded deterministic summary sections;
- exact input/output byte and request-estimate facts;
- omitted-section reasons and regeneration compatibility version.

It does not store a second `retained_tail_json` transcript. Raw conversation records remain the
only history and are not automatically deleted in Stage 4. The current process-local
`OMITTED_TOOL_RESULT` marker is a prompt projection detail and is never persisted as original tool
history.

Context selection order is:

```text
fixed system boundary
→ frozen non-secret AgentRun/task context
→ latest applicable deterministic checkpoint
→ complete records after that checkpoint
→ referenced bounded Artifact excerpts
→ current accepted user input/open recovery context
→ frozen Tool definitions
```

Compaction cannot split a ToolCycle or remove the current task goal, constraints, unresolved items,
recent failures, open approval/recovery context, or complete recent cycles. LLM summaries are not a
Stage 4 completion condition; any future model summary is an additive, provenance-linked projection.

## Fork cut points and lineage

Fork creates a child Session in the same workspace from either:

1. a closed Turn's terminal `conversation_position`; or
2. a checkpoint whose source range ends at a closed Turn boundary.

The child stores parent Session ID, cut record ID/position, optional checkpoint ID, and creation
reason. It reads the immutable parent prefix plus its own local records as a projection; it does not
copy or edit parent rows. Referenced parent Artifacts are shared read-only by ID/reference count.

The child starts with no session-scoped Preferences and no current TaskRun unless the fork command
explicitly creates a new open TaskRun using a bounded source-goal reference. It never inherits an
active Approval or CapabilityGrant.

Fork from an open Turn, unresolved ToolCycle, pending Approval, or unresolved RecoveryReport is
rejected. A checkpoint fork and a Turn-boundary fork must resolve to the same explicit source prefix;
the checkpoint's summary never replaces missing source rows.

## No workspace rewind

Conversation fork never reads, modifies, restores, deletes, or reverses workspace files. Morrow
does not create `WorkspaceCheckpoint`, lazy before-state rewind, or managed project restore in Stage
4. File conflict-safe mutation and historical Artifact evidence remain separate capabilities.

## Backup interaction

Subplan 43 backs up SQLite first through online backup and then copies Artifact bytes using a
metadata manifest. A concurrent Artifact publication may make the snapshot temporarily report a
referenced file as absent. Restore verification exposes that missing reference; it never invents or
silently drops bytes. A future fully coordinated bundle requires a separate freeze protocol, not an
Outbox or background worker.

## Rejected alternatives

- content-addressed storage or deduplication as a completion requirement;
- full/raw command streams before streaming-redactor proof;
- automatic deletion/retention heuristics in Stage 4;
- persisting current ContextBuilder omission markers as history;
- self-contained checkpoints that duplicate a second transcript;
- fork that mutates its parent, copies grants, or rewinds project files.
