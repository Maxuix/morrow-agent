# Subplan 42 — Context Checkpoints and Conversation Fork

> Status: pending
> Prerequisite: Subplan 41 accepted
> Owns: deterministic long-context projections and immutable conversation branching
> Schema: v7 ContextCheckpoint and immutable Session lineage

## Objective

Let long foreground tasks continue within model budgets and branch from prior history while keeping
raw records immutable, complete ToolCycles intact, and project files untouched.

## In scope

- ContextCheckpoint model/store with immutable source record IDs/ranges, method/version, budget
  facts, created-at/source run, and Artifact references.
- Deterministic compaction tiers for repeatable tool output, old bounded artifacts, complete Turns,
  and task state.
- ContextBuilder/assembler integration that selects checkpoints plus recent complete records without
  splitting a ToolCycle or open recovery context.
- Checkpoint invalidation/regeneration rules based on immutable provenance, not mutable copies.
- Session/conversation fork from a legal Turn boundary or checkpoint with parent provenance and
  shared immutable Artifact references.
- Parent/child query isolation, independent future TaskRuns, and explicit non-inheritance of Session
  Preferences, approvals, grants, and permission snapshots.

## Out of scope

- LLM summary as a Stage 4 completion requirement.
- Long-term memory or project fact extraction.
- Duplicated retained-tail JSON as another conversation authority.
- Workspace/code rewind, patch reversal, restoration, or deletion of project files.

## Tasks

- [ ] S4.42.1 Define checkpoint/provenance/fork models and legal complete-cycle source boundaries.
- [ ] S4.42.2 Implement deterministic compaction from durable records/Artifacts with typed omitted-
  content reasons and exact budgets.
- [ ] S4.42.3 Integrate ContextBuilder selection of fixed boundary, resolved run snapshot, active
  task state, checkpoint projection, recent complete cycles, Artifact excerpts, and current input.
- [ ] S4.42.4 Implement fork creation from a legal Turn/checkpoint in one transaction, with immutable
  parent-prefix links, exact included record IDs/cut position, reference-only Artifact sharing, and
  no copied Session Preferences, TaskRun, Approval, or CapabilityGrant.
- [ ] S4.42.5 Add interruption, regeneration, corrupt/missing Artifact, boundary, budget, and parent/
  child isolation tests.
- [ ] S4.42.6 Prove a long scripted task continues after multiple checkpoints and that source records
  remain queryable and unchanged.
- [ ] S4.42.7 Document the distinction among raw history, context projection, Artifact, TaskOutcome,
  and future Stage 5 memory; run gates.

## Locked contracts

- A checkpoint is a reproducible projection over immutable records, not a new chat-history writer.
- Current task goal/constraints, unresolved items, recent failures, open approval/recovery state, and
  complete recent ToolCycles cannot be compacted away.
- Every omitted section has a source range and reason. Every summary/reference can trace back to raw
  records under retention policy.
- Checkpoints reference record IDs/ranges and complete-cycle boundaries; they never store a retained-
  tail JSON transcript or persist the current `OMITTED_TOOL_RESULT` marker as history.
- Fork never changes the parent and never applies, deletes, or restores workspace files.
- Fork is legal only at a closed Turn terminal position or a checkpoint ending at one; the child
  references that immutable parent prefix and shares Artifacts read-only.
- Optional future LLM summaries must be additive projections with provenance and cannot replace the
  deterministic path or become project facts.

## Tests and faults

- exact token/character/record budgets at complete-cycle boundaries;
- crash during checkpoint creation before/after commit;
- deterministic regeneration and old checkpoint version compatibility;
- incomplete/open ToolCycle, approval, and recovery context preservation;
- fork at legal/illegal boundaries, exact cut inclusion, cross-workspace references, no inherited
  Preferences/Task/grants, read-only shared Artifacts, and parent/child mutations;
- missing/corrupt Artifact fallback without fabricating content;
- no raw/reasoning/secret payload crosses into checkpoint text.

## Completion gate

A scripted long task stays within configured context limits through deterministic checkpoints,
retains source provenance and recovery context, and can fork into an independent Session without
mutating parent history or project files.

## Deliverables

- ContextCheckpoint/fork models, stores, and application services.
- ContextBuilder integration and deterministic compaction policy.
- Long-context and branch-isolation acceptance evidence.
