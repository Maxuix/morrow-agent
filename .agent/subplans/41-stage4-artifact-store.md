# Subplan 41 — Artifact Store and Durable Payload Budgets

> Status: completed
> Prerequisite: Subplan 40 accepted
> Owns: large durable payload publication, integrity, provenance, and retention safety
> Schema: v6 Artifact metadata and references

## Objective

Move useful large task evidence out of conversation rows into an integrity-checked Artifact Store
without creating a secret archive, a second message authority, or an enterprise content platform.

## In scope

- Artifact identity, kind, sensitivity, provenance, integrity, retention, pin/reference, and state
  models.
- Artifact metadata port/schema and data-root Artifact filesystem adapter.
- Opaque ID-addressed paths, restrictive permissions, atomic publication, hash verification, and
  bounded reads/excerpts.
- First artifact kinds: bounded redacted command output, patch/diff, test/diagnostic report, and
  deterministic task/context summary material.
- Tool/TaskOutcome links and missing/corrupt/orphan states.
- Explicit durable byte/record budgets for arguments, results, errors, events, snapshots, metadata,
  excerpts, and Artifact content.
- Read-only orphan candidate and retention/reference reports; Stage 4 performs no automatic
  Artifact deletion.

## Out of scope

- Content-addressed deduplication, FTS, embeddings, cloud/object storage, sync, or sharing.
- Full/raw process streams without a proven streaming redactor.
- Automatic deletion of referenced artifacts or complete Stage 10 export/delete UX.
- File snapshots of entire repositories.

## Tasks

- [x] S4.41.1 Define Artifact/budget models, allowed kinds, sensitivity/retention states, and
  canonical metadata with strict validation.
- [x] S4.41.2 Implement Artifact metadata storage and opaque path layout confined under the validated
  state root.
- [x] S4.41.3 Implement temp-write/fsync/atomic-rename/parent-fsync/metadata publication and safe
  rollback/orphan discovery at every fault point.
- [x] S4.41.4 Add hash/size verification, bounded streaming reads, excerpt generation, and visible
  missing/corrupt states.
- [x] S4.41.5 Integrate bounded redacted command output, diff/report production, ToolExecution links,
  and TaskOutcome references without duplicating complete bytes in conversation rows.
- [x] S4.41.6 Implement pin/reference-aware retention checks and read-only orphan candidate reports;
  do not implement automatic deletion or heuristic retention.
- [x] S4.41.7 Add redaction, symlink/path-escape, permission, disk failure, crash, and payload exact-
  boundary tests; update storage/security docs.

## Locked contracts

- Artifact ID determines the managed path; content hash verifies integrity but need not determine
  the filename or deduplicate content.
- Metadata never claims `available` until bytes are atomically published and verified under the
  accepted transaction protocol.
- Conversation/TaskOutcome stores only bounded excerpts and references for Artifact-sized content.
- Missing/corrupt bytes are explicit facts. The system does not silently drop the reference or
  recreate evidence from an unrelated source.
- Current credential/sensitive-resource redaction applies before persistence, not only on display.
- The initial `command_output` Artifact may contain only the existing bounded, fully redacted command
  result. Full/raw process streams remain unsupported until a separate streaming-redactor contract
  exists; Subplan 41 does not impose that future capability as a gate.
- Locked limits are metadata 32 KiB, excerpt 8 KiB, one Artifact 64 MiB, and one TaskRun 256 MiB,
  alongside the row-level budgets in the durable-execution ADR.

## Tests and faults

- file and metadata crash points for no row/orphan/temp/published states;
- hash mismatch, truncation, missing file, wrong permissions, symlink, traversal, and ID collision;
- exact budget boundaries and multibyte UTF-8 accounting;
- existing bounded command-result redaction, exact limits, and multibyte truncation semantics;
- referenced/pinned/unreferenced retention decisions;
- backup manifest compatibility required by Subplan 43.

## Completion gate

Large supported evidence survives restart as bounded, redacted, verified Artifacts with traceable
provenance. Every partial-publication state is deterministic, and no referenced Artifact is removed
by automatic cleanup.

## Deliverables

- Artifact Core model, metadata port, and filesystem adapter.
- v6 migration that adds Artifact references to ToolExecution/TaskOutcome without changing prior
  outcome evidence.
- Durable payload budget/redaction policy.
- Tool/TaskOutcome integration and fault evidence.
