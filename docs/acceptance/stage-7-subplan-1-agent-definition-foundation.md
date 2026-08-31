# Stage 7 Subplan 1 — Agent Definition Foundation Acceptance

Date: 2026-08-31

## Result

The Subplan 1 implementation satisfies its declared foundation boundary. It adds immutable,
versioned AgentDefinitions and composes them through the existing AgentRun preparation and
AgentLoop path. It does not add Workflow revisions, a compiler, scheduler, Workflow CLI, or a
second conversation-history writer.

The verified implementation was fast-forward integrated into local `main` on 2026-08-31.

## Implemented evidence

- Operational Store v23 persists immutable AgentDefinition versions, OCC-protected heads,
  one-way revocations, publication receipts and exact Skill references.
- Workspace `agent-definitions.yaml` remains desired source. Validation is pure; explicit publish
  is the only path that allocates a version or advances a head.
- AgentFactory freezes the exact definition, model, Skill/tool ceiling, conversation identity and
  a primary-generation-request cap into the ordinary prepared AgentRun path. Every admitted
  `purpose=agent` call counts, including tool follow-ups and retries; `cap=1` allows one model call,
  not a complete tool round with a subsequent model answer.
- New leaf admission requires a distinct empty standalone Session and matching current Task. The
  Session-owned ConversationLog remains the only transcript writer.
- Head disable gates new admission only. Exact-version revocation blocks admission and recovery;
  it is additive and cannot be undone.
- Definition source, including malformed desired bytes, participates in backup/verify/restore.
  Published corruption remains an error; malformed desired source is a non-degrading doctor
  warning. High-confidence credential values are refused before backup.
- Exact Skill references reuse existing enabled-binding, dependency, pin and removal guards.

## Validation

- Declared focused closeout gate: 90 passed.
- Full offline suite: 1327 passed, 2 nested-Seatbelt tests skipped, 2 Live tests deselected.
- Static gate: Ruff format check, Ruff lint, compileall, CLI help and `git diff --check`.

No Live or real-network test was run. Subplan 2 remains inactive.
