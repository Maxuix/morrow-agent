# Progress Tracker

## Current status

Stage 4 Durable Task, Session, Artifact, and Recovery implementation is active at Subplan 36. The
three research documents have been demoted to superseded decision input and reconciled against the
Stage 3 code baseline. The accepted route is one
data-root SQLite operational database plus filesystem Artifacts, durable ConversationLog and tool
journal boundaries, deterministic recovery, foreground TaskRun outcomes, context checkpoints,
conversation-only fork, auditable grants, and Full Access Manual.

The conditional plan review was confirmed against current code. Its five P0 findings and associated
P1 ownership/evidence findings are real; accepted ADRs now close them, and Subplans 36–45 have been
rewritten to consume those contracts without moving ownership forward or backward.

No Stage 4 production adapter/schema has landed at activation, and no public event or Full Access
behavior has changed.

## Last completed task

S4.35.8 closed the conditional plan review. All five P0 findings and material P1/P2 corrections are
represented in accepted ADRs, the v1–v9 schema map, the fault matrix, and revised Subplans 36–45.
Planning validation passed, the user accepted the remediation, and commit `20fb43e` preserves the
complete contract-review change.

## Active task

S4.36.1 — add typed Operational Store paths, open modes, health classifications, and sanitized
storage errors.

## Next action

Inspect current DataRoot/bootstrap/error/port ownership, then implement only S4.36.1 with focused
tests before moving to connection behavior.

## Blockers

None. Later business persistence remains gated by Subplans 37–45.

## Active boundary

- Only the v1 Operational Store foundation in Subplan 36 is active. Conversation, Task, tool,
  Artifact, event, grant, and Full Access schemas/behavior remain inactive.
- ConversationLog remains the sole chat-history authority; ordinary chat stays on
  `AgentLoop.run_task()`.
- Current YAML/CredentialStore authorities and Stage 3 runtime/security behavior remain unchanged.
- Full Access Manual is planned; Controlled Full Access Auto, raw auto, code rewind, background
  work, outbox workers, automatic repair, in-flight steering, FTS/embeddings, and Stage 5 learning
  are deferred.
- Public event lifecycle and bundled policy-default changes remain explicit hold points.
