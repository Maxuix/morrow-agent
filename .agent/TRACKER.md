# Progress Tracker

## Current status

Stage 4 Durable Task, Session, Artifact, and Recovery planning is active. The three research
documents have been demoted to superseded decision input and reconciled against the Stage 3 code
baseline. The accepted route is one
data-root SQLite operational database plus filesystem Artifacts, durable ConversationLog and tool
journal boundaries, deterministic recovery, foreground TaskRun outcomes, context checkpoints,
conversation-only fork, auditable grants, and Full Access Manual.

The conditional plan review was confirmed against current code. Its five P0 findings and associated
P1 ownership/evidence findings are real; accepted ADRs now close them, and Subplans 36–45 have been
rewritten to consume those contracts without moving ownership forward or backward.

No Stage 4 production adapter, schema, public event change, or Full Access behavior has been
implemented.

## Last completed task

S4.35.7 accepted the reference-adoption ADR: Stage 4 uses upstream projects only for semantic and
failure-mode input, with zero copied code/schema/fixture/asset today. The three research drafts are
visibly superseded; any later direct reuse requires a new pinned provenance/license/notice hold
point.

## Active task

S4.35.8 — finish cross-document consistency and validation after applying the review remediation to
the fault matrix and Subplans 36–45.

## Next action

Present the validated review remediation for explicit acceptance. If accepted, commit/close
Subplan 35 and activate Subplan 36 only through a separate execution-state update.

## Blockers

No implementation dead end is known. S4.35.8 validation has passed; production implementation
remains deliberately gated on explicit acceptance of the remediation and a separate Subplan 36
activation.

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
