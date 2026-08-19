# Progress Tracker

## Current status

Stage 4 Durable Task, Session, Artifact, and Recovery planning is active. The three research
documents have been reconciled against the Stage 3 code baseline. The accepted route is one
data-root SQLite operational database plus filesystem Artifacts, durable ConversationLog and tool
journal boundaries, deterministic recovery, foreground TaskRun outcomes, context checkpoints,
conversation-only fork, auditable grants, and Full Access Manual.

No Stage 4 production adapter, schema, public event change, or Full Access behavior has been
implemented.

## Last completed task

S4.35.2 accepted `docs/decisions/stage-4-operational-store.md` and proved the locked `sqlite3`
settings in `tests/test_stage4_operational_store_spike.py`: WAL + `synchronous=FULL`,
`BEGIN IMMEDIATE`, crash commit/rollback, typed busy retry, global maintenance lock, future/foreign
refusal without rewrite, and online backup during writes. Spike tests passed `15 passed`. No
production adapter, schema, public event, policy default, or Full Access behavior changed.

## Active task

S4.35.3 — write the domain/ownership ADR: identifiers, Session lifecycle versus health, TaskRun
continuation rules, AgentRun snapshots, ConversationLog single-writer protocol, and targeted
command idempotency.

## Next action

Inspect current Session, ConversationLog, AgentLoop, and command/id types, then write the
domain/ownership ADR without adding production persistence.

## Blockers

None for planning. Production implementation remains gated on acceptance of all Subplan 35 ADRs
and explicit activation of Subplan 36.

## Active boundary

- Only planning, ADRs, reference provenance, and disposable design spikes are authorized in
  Subplan 35.
- ConversationLog remains the sole chat-history authority; ordinary chat stays on
  `AgentLoop.run_task()`.
- Current YAML/CredentialStore authorities and Stage 3 runtime/security behavior remain unchanged.
- Full Access Manual is planned; Controlled Full Access Auto, raw auto, code rewind, background
  work, outbox workers, automatic repair, in-flight steering, FTS/embeddings, and Stage 5 learning
  are deferred.
- Public event lifecycle and bundled policy-default changes remain explicit hold points.
