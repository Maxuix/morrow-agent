# Stage 4 Mature-reference adoption decision

## Status

Accepted for Stage 4 implementation planning by S4.35.7.

## Decision

Stage 4 adopts **semantic and failure-mode references only**. It copies no upstream source code,
schema, fixture, or asset from Pi, Hermes Agent, OpenAI Codex, or Claude Code.

Consequences:

- no upstream commit is a build or implementation dependency;
- no direct-code adoption log or `THIRD_PARTY_NOTICES.md` is required for the current Stage 4 work;
- the local Hermes snapshot without Git provenance is not an eligible code source;
- source links in the research review support design history only and cannot override Morrow's
  accepted ADRs;
- if a later subplan wants to copy any code/fixture/asset, it must stop, pin repository + commit +
  source path, verify the license and Morrow's own distribution terms, add notices/adoption log as
  required, and obtain a new explicit decision before the copy.

## Allowed semantic inputs

- Pi: immutable lineage, complete-cycle cut points, deterministic compaction, and common-ancestor
  reasoning.
- Hermes Agent: WAL/transaction/migration discipline and failure cases, reimplemented with Morrow
  types and stdlib `sqlite3`.
- OpenAI Codex: stable client command IDs, optimistic concurrency, and explicit lifecycle concepts.
- Claude Code public behavior: conversation recovery and workspace recovery must remain separate,
  and dangerous Host authority must be runtime-enforced and visibly isolated from ordinary modes.
- Morrow research: upstream failure reports may become named regression scenarios without copying
  implementation or copyrighted test text.

## Superseded research proposals

The three `docs/research/morrow-stage4-*.md` files are decision input, not implementation specs.
The active plan explicitly rejects or defers their proposals for:

- Controlled Full Access Auto or raw auto;
- managed `WorkspaceCheckpoint`, file rewind, or workspace restoration;
- delivery `EventOutbox`, acknowledgements, or worker;
- durable `RunClaim`/lease/heartbeat;
- approval nonce;
- FTS5/semantic retrieval as a Stage 4 dependency;
- automatic history repair;
- their old Subplan 35–48 numbering and missing `docs/implementation/` plan path.

Their headers carry this status so an implementation agent cannot mistake them for current
authority.

## Rejected alternatives

- importing an upstream agent runtime or Session database;
- using the local Hermes checkout as provenance;
- pinning arbitrary current commits while no code is reused, which would imply a false dependency;
- requiring third-party notices for ideas or public behavior alone.
