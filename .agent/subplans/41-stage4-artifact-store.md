# Subplan 41 — Artifact Store and Durable Payload Budgets

> Status: pending
> Prerequisite: Subplan 40 accepted
> Owns: large durable payload publication, integrity, provenance, and retention safety

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
- Deterministic orphan cleanup candidates and retention/reference checks.

## Out of scope

- Content-addressed deduplication, FTS, embeddings, cloud/object storage, sync, or sharing.
- Full/raw process streams without a proven streaming redactor.
- Automatic deletion of referenced artifacts or complete Stage 10 export/delete UX.
- File snapshots of entire repositories.

## Tasks

- [ ] S4.41.1 Define Artifact/budget models, allowed kinds, sensitivity/retention states, and
  canonical metadata with strict validation.
- [ ] S4.41.2 Implement Artifact metadata storage and opaque path layout confined under the validated
  state root.
- [ ] S4.41.3 Implement temp-write/fsync/atomic-rename/parent-fsync/metadata publication and safe
  rollback/orphan discovery at every fault point.
- [ ] S4.41.4 Add hash/size verification, bounded streaming reads, excerpt generation, and visible
  missing/corrupt states.
- [ ] S4.41.5 Integrate bounded redacted command output, diff/report production, ToolExecution links,
  and TaskOutcome references without duplicating complete bytes in conversation rows.
- [ ] S4.41.6 Implement pin/reference-aware retention checks and read-only orphan candidate reports.
- [ ] S4.41.7 Add redaction, symlink/path-escape, permission, disk failure, crash, and payload exact-
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

## Tests and faults

- file and metadata crash points for no row/orphan/temp/published states;
- hash mismatch, truncation, missing file, wrong permissions, symlink, traversal, and ID collision;
- exact budget boundaries and multibyte UTF-8 accounting;
- redaction split across chunks for the bounded streaming writer;
- referenced/pinned/unreferenced retention decisions;
- backup manifest compatibility required by Subplan 43.

## Completion gate

Large supported evidence survives restart as bounded, redacted, verified Artifacts with traceable
provenance. Every partial-publication state is deterministic, and no referenced Artifact is removed
by automatic cleanup.

## Deliverables

- Artifact Core model, metadata port, and filesystem adapter.
- Durable payload budget/redaction policy.
- Tool/TaskOutcome integration and fault evidence.

